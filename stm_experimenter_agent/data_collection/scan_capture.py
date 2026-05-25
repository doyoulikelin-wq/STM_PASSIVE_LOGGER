"""Watch a directory for newly saved Nanonis ``.sxm`` files.

Engineering notes
-----------------
* Polling-based watcher (no watchdog dependency); robust on Windows shares
  where inotify-style events are unreliable.
* A file is considered "ready" only after its size has been stable for
  ``stable_polls`` consecutive checks, so we never read while Nanonis is
  still flushing.
* Each file is processed at most once per logger lifetime, tracked by
  ``(path, mtime, size)`` to detect re-saves of the same name.
* Failures during parsing or preview generation do not crash the watcher.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Set, Tuple

from .dataset_writer import DatasetWriter
from .preview import render_preview
from .sxm_archive import archive_sxm_path, path_relative_to_data_root
from .sxm_parser import load_sxm, parse_sxm_header

logger = logging.getLogger(__name__)


@dataclass
class _PendingFile:
    path: Path
    last_size: int
    stable_count: int


class ScanCapture:
    def __init__(self, *, watch_dir: Path | str, writer: DatasetWriter,
                 session_id: str, previews_dir: Path | str,
                 poll_interval_s: float = 2.0, stable_polls: int = 2,
                 preview_channel_priority: Tuple[str, ...] = ("Z", "Current", "LI_Demod_1_X"),
                 render_preview_enabled: bool = True) -> None:
        self.watch_dir = Path(watch_dir)
        self.writer = writer
        self.session_id = session_id
        self.previews_dir = Path(previews_dir)
        self.previews_dir.mkdir(parents=True, exist_ok=True)
        self.poll_interval_s = poll_interval_s
        self.stable_polls = max(1, stable_polls)
        self.preview_channel_priority = preview_channel_priority
        self.render_preview_enabled = render_preview_enabled

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._pending: Dict[Path, _PendingFile] = {}
        self._processed: Set[Tuple[str, float, int]] = set()
        # Files that existed before the watcher started are not re-ingested,
        # but we still record them as "pre-existing" so reports can audit.
        self._baseline_taken = False

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        # Auto-create watch_dir if it does not exist yet (it might be a path
        # the experimenter plans to configure inside Nanonis after the
        # logger is already running).
        if not self.watch_dir.exists():
            try:
                self.watch_dir.mkdir(parents=True, exist_ok=True)
                logger.info("ScanCapture: created missing watch_dir %s", self.watch_dir)
            except OSError:
                logger.warning(
                    "ScanCapture: watch_dir %s does not exist and could not be created;"
                    " will keep polling and pick it up later",
                    self.watch_dir,
                )
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="stm-scan-capture", daemon=True
        )
        self._thread.start()
        logger.info("ScanCapture started on %s", self.watch_dir)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
        logger.info("ScanCapture stopped")

    # -- main loop ---------------------------------------------------------
    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception:  # noqa: BLE001
                logger.exception("ScanCapture poll failed")
            self._stop.wait(self.poll_interval_s)

    def _poll_once(self) -> None:
        if not self.watch_dir.exists():
            return
        current_files = sorted(self.watch_dir.glob("*.sxm"))

        if not self._baseline_taken:
            # Mark every pre-existing file as already processed so we don't
            # re-ingest historical data on startup. The user can run a
            # separate one-shot importer if they want backfill.
            for p in current_files:
                try:
                    st = p.stat()
                except OSError:
                    continue
                self._processed.add((str(p), st.st_mtime, st.st_size))
            self._baseline_taken = True
            logger.info("ScanCapture baseline: %d existing .sxm files skipped",
                        len(current_files))
            return

        for path in current_files:
            try:
                st = path.stat()
            except FileNotFoundError:
                continue
            fingerprint = (str(path), st.st_mtime, st.st_size)
            if fingerprint in self._processed:
                continue

            pending = self._pending.get(path)
            if pending is None or pending.last_size != st.st_size:
                first_sighting = pending is None
                self._pending[path] = _PendingFile(
                    path=path, last_size=st.st_size, stable_count=1
                )
                if first_sighting:
                    # Emit a visible breadcrumb so the operator can see the
                    # watcher noticed Nanonis writing a new file, even before
                    # it has stabilised enough to ingest.
                    try:
                        self.writer.log_event(self.session_id, "scan_candidate_seen", {
                            "sxm_path": str(path),
                            "size_bytes": st.st_size,
                        })
                    except Exception:  # noqa: BLE001
                        logger.debug("failed to log scan_candidate_seen", exc_info=True)
                continue

            pending.stable_count += 1
            if pending.stable_count < self.stable_polls:
                continue

            # File looks done, ingest it.
            try:
                self._ingest(path)
                self._processed.add(fingerprint)
            except Exception:  # noqa: BLE001
                logger.exception("Failed to ingest %s; will retry next poll", path)
            finally:
                self._pending.pop(path, None)

    # -- ingest one file ---------------------------------------------------
    def _ingest(self, path: Path) -> None:
        captured_ts = time.time()
        scan_id = self._make_scan_id(path, captured_ts)
        logger.info("Ingesting scan %s from %s", scan_id, path)

        archived_path = archive_sxm_path(
            path,
            self.writer.data_root,
            self.session_id,
            scan_id=scan_id,
        )

        if self.render_preview_enabled:
            sxm = load_sxm(archived_path)
            preview_path = self._render_first_available(sxm, scan_id)
        else:
            sxm = parse_sxm_header(archived_path)
            preview_path = None

        metadata = sxm.to_metadata()
        metadata["source_sxm_path"] = str(path)
        metadata["archived_sxm_path"] = path_relative_to_data_root(
            archived_path,
            self.writer.data_root,
        )
        self.writer.insert_scan(
            session_id=self.session_id,
            scan_id=scan_id,
            captured_ts=captured_ts,
            sxm_path=path_relative_to_data_root(archived_path, self.writer.data_root),
            preview_path=(
                path_relative_to_data_root(preview_path, self.writer.data_root)
                if preview_path else None
            ),
            metadata=metadata,
        )
        self.writer.log_event(self.session_id, "scan_captured", {
            "scan_id": scan_id,
            "sxm_path": str(path),
            "archived_sxm_path": path_relative_to_data_root(
                archived_path,
                self.writer.data_root,
            ),
            "preview_path": (
                path_relative_to_data_root(preview_path, self.writer.data_root)
                if preview_path else None
            ),
        }, ts=captured_ts)

    def _render_first_available(self, sxm, scan_id: str) -> Optional[Path]:
        if not sxm.data:
            return None
        for channel_name in self.preview_channel_priority:
            ch = sxm.data.get(channel_name)
            if ch and "forward" in ch:
                out = self.previews_dir / f"{scan_id}_{channel_name}_fwd.png"
                try:
                    return render_preview(
                        ch["forward"], out,
                        title=f"{scan_id} / {channel_name} fwd",
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("Preview render failed for %s/%s", scan_id, channel_name)
                    return None
        # Fall back to the first channel we have.
        first_name = next(iter(sxm.data.keys()))
        first_dir = "forward" if "forward" in sxm.data[first_name] else next(iter(sxm.data[first_name].keys()))
        out = self.previews_dir / f"{scan_id}_{first_name}_{first_dir}.png"
        try:
            return render_preview(sxm.data[first_name][first_dir], out,
                                  title=f"{scan_id} / {first_name} {first_dir}")
        except Exception:  # noqa: BLE001
            logger.exception("Fallback preview render failed for %s", scan_id)
            return None

    def _make_scan_id(self, path: Path, captured_ts: float) -> str:
        h = hashlib.sha1(f"{path.resolve()}|{captured_ts:.3f}".encode("utf-8")).hexdigest()[:8]
        stem = path.stem.replace(" ", "_")
        return f"{stem}_{h}"
