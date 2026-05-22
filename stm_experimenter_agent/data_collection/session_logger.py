"""Top-level orchestrator: opens a session, starts signal + scan loggers."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from ..nanonis_driver.client import NanonisReadOnlyClient
from .dataset_writer import DatasetWriter
from .scan_capture import ScanCapture
from .signal_logger import SignalLogger

logger = logging.getLogger(__name__)


@dataclass
class SessionMeta:
    operator: Optional[str] = None
    sample_id: Optional[str] = None
    tip_id: Optional[str] = None
    material: Optional[str] = None
    notes: Optional[str] = None
    extras: Dict[str, Any] = field(default_factory=dict)


def make_session_id(meta: SessionMeta, now: Optional[datetime] = None) -> str:
    now = now or datetime.now()
    base = now.strftime("%Y%m%d_%H%M%S")
    parts = [base]
    if meta.sample_id:
        parts.append(meta.sample_id)
    if meta.tip_id:
        parts.append(meta.tip_id)
    return "_".join(parts)


class SessionLogger:
    """Owns one session's worth of writers + background threads."""

    def __init__(self, *, data_root: Path | str, watch_dir: Optional[Path | str],
                 client: NanonisReadOnlyClient, meta: SessionMeta,
                 poll_hz: float = 1.0) -> None:
        self.meta = meta
        self.session_id = make_session_id(meta)
        self.writer = DatasetWriter(data_root)
        self.client = client
        self.poll_hz = poll_hz

        self.signal_logger = SignalLogger(
            client=client, writer=self.writer,
            session_id=self.session_id, poll_hz=poll_hz,
        )
        self.scan_capture: Optional[ScanCapture] = None
        if watch_dir is not None:
            self.scan_capture = ScanCapture(
                watch_dir=watch_dir,
                writer=self.writer,
                session_id=self.session_id,
                previews_dir=self.writer.previews_root / self.session_id,
            )

        self._started = False

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if self._started:
            return
        start_ts = time.time()
        # Probe instrument version up front for the session record.
        try:
            self.client.connect()
            instrument = {
                "tcp_host": self.client.host,
                "tcp_port": self.client._connected_port or self.client.port,
                "version": self.client.version_info(),
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not contact Nanonis at startup: %s", exc)
            instrument = {
                "tcp_host": self.client.host,
                "tcp_port": self.client.port,
                "version": {"ok": False, "error": str(exc)},
            }

        self.writer.upsert_session(
            session_id=self.session_id,
            start_ts=start_ts,
            operator=self.meta.operator,
            sample_id=self.meta.sample_id,
            tip_id=self.meta.tip_id,
            material=self.meta.material,
            notes=self.meta.notes,
            instrument=instrument,
        )
        self.writer.log_event(self.session_id, "session_start", {
            "instrument": instrument,
            "poll_hz": self.poll_hz,
            "extras": self.meta.extras,
        }, ts=start_ts)

        if self.scan_capture is not None:
            watch_dir = self.scan_capture.watch_dir
            try:
                resolved = str(watch_dir.resolve())
            except OSError:
                resolved = str(watch_dir)
            existed_before = watch_dir.exists()
            pre_existing = (
                len(list(watch_dir.glob("*.sxm"))) if existed_before else 0
            )
            self.writer.log_event(self.session_id, "watch_dir_resolved", {
                "watch_dir": resolved,
                "existed_before_start": existed_before,
                "pre_existing_sxm_count": pre_existing,
                "note": (
                    "Pre-existing .sxm files are treated as baseline and NOT"
                    " ingested. Only files saved after this timestamp will"
                    " appear in scans/events."
                ),
            }, ts=start_ts)

        self.signal_logger.start()
        if self.scan_capture is not None:
            self.scan_capture.start()
        self._started = True
        logger.info("Session %s started", self.session_id)

    def stop(self) -> None:
        if not self._started:
            return
        logger.info("Stopping session %s", self.session_id)
        try:
            self.signal_logger.stop()
        finally:
            if self.scan_capture is not None:
                self.scan_capture.stop()
        end_ts = time.time()
        try:
            self.writer.upsert_session(
                session_id=self.session_id,
                start_ts=0.0,    # ignored by ON CONFLICT update
                end_ts=end_ts,
            )
            self.writer.log_event(self.session_id, "session_end", {
                "signal_rows": self.writer.fetch_signal_count(self.session_id),
                "scan_rows": self.writer.fetch_scan_count(self.session_id),
            }, ts=end_ts)
        finally:
            self.writer.close()
            try:
                self.client.close()
            except Exception:  # noqa: BLE001
                logger.debug("client close on session end raised", exc_info=True)
        self._started = False

    # -- context manager ---------------------------------------------------
    def __enter__(self) -> "SessionLogger":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()
