"""One-shot import/backfill for historical Nanonis ``.sxm`` files."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, BinaryIO, Dict, Iterable, List, Optional

from .dataset_writer import DatasetWriter
from .preview import render_preview
from .sxm_archive import (
    archive_sxm_path,
    archive_sxm_stream,
    hash_file,
    path_relative_to_data_root,
    safe_path_part,
)
from .sxm_parser import load_sxm


DEFAULT_PREVIEW_CHANNEL_PRIORITY = ("Z", "Current", "LI_Demod_1_X")


def default_import_session_id(source: str | Path) -> str:
    name = Path(source).stem if Path(source).suffix else Path(source).name
    stamp = time.strftime("%Y%m%d_%H%M%S")
    return f"{stamp}_{safe_path_part(name, fallback='historical')}"


def discover_sxm_files(path: Path | str, *, recursive: bool = False) -> List[Path]:
    root = Path(path)
    if root.is_file():
        return [root] if root.suffix.lower() == ".sxm" else []
    if not root.exists():
        raise FileNotFoundError(root)
    globber = root.rglob if recursive else root.glob
    return sorted(p for p in globber("*.sxm") if p.is_file())


def import_sxm_paths(
    data_root: Path | str,
    paths: Iterable[Path | str],
    *,
    session_id: str,
    session_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Import existing local ``.sxm`` files into ``data_root``."""
    data_root = Path(data_root)
    session_meta = session_meta or {}
    source_paths = [Path(p) for p in paths]
    result: Dict[str, Any] = {
        "session_id": session_id,
        "imported": 0,
        "errors": [],
        "scans": [],
    }
    if not source_paths:
        return result

    writer = DatasetWriter(data_root)
    try:
        _ensure_session(writer, session_id, session_meta)
        for source_path in source_paths:
            try:
                scan = _import_archived_path(
                    writer,
                    data_root,
                    source_path,
                    session_id=session_id,
                    session_meta=session_meta,
                    source_label=str(source_path),
                )
                result["imported"] += 1
                result["scans"].append(scan)
            except Exception as exc:  # noqa: BLE001
                result["errors"].append({
                    "path": str(source_path),
                    "error": f"{type(exc).__name__}: {exc}",
                })
    finally:
        writer.close()
    return result


def import_sxm_folder(
    data_root: Path | str,
    path: Path | str,
    *,
    session_id: Optional[str] = None,
    session_meta: Optional[Dict[str, Any]] = None,
    recursive: bool = False,
) -> Dict[str, Any]:
    source = Path(path)
    sid = session_id or default_import_session_id(source)
    return import_sxm_paths(
        data_root,
        discover_sxm_files(source, recursive=recursive),
        session_id=sid,
        session_meta=session_meta,
    )


def import_sxm_upload(
    data_root: Path | str,
    fileobj: BinaryIO,
    *,
    filename: str,
    session_id: str,
    session_meta: Optional[Dict[str, Any]] = None,
    source_relative_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Import one uploaded ``.sxm`` stream into ``data_root``."""
    data_root = Path(data_root)
    session_meta = session_meta or {}
    writer = DatasetWriter(data_root)
    try:
        _ensure_session(writer, session_id, session_meta)
        archive_path, scan_id = archive_sxm_stream(fileobj, filename, data_root, session_id)
        return _import_archived_path(
            writer,
            data_root,
            archive_path,
            session_id=session_id,
            session_meta=session_meta,
            scan_id=scan_id,
            source_label=source_relative_path or filename,
            already_archived=True,
        )
    finally:
        writer.close()


def _ensure_session(
    writer: DatasetWriter,
    session_id: str,
    session_meta: Dict[str, Any],
) -> None:
    writer.upsert_session(
        session_id=session_id,
        start_ts=float(session_meta.get("start_ts") or time.time()),
        operator=session_meta.get("operator"),
        sample_id=session_meta.get("sample_id"),
        tip_id=session_meta.get("tip_id"),
        material=session_meta.get("material"),
        notes=session_meta.get("notes"),
        instrument={"imported_historical_session": True},
    )


def _import_archived_path(
    writer: DatasetWriter,
    data_root: Path,
    source_path: Path,
    *,
    session_id: str,
    session_meta: Dict[str, Any],
    scan_id: Optional[str] = None,
    source_label: Optional[str] = None,
    already_archived: bool = False,
) -> Dict[str, Any]:
    if scan_id is None:
        scan_id = f"{safe_path_part(source_path.stem, fallback='scan')}_{hash_file(source_path)[:8]}"
    archive_path = source_path if already_archived else archive_sxm_path(
        source_path,
        data_root,
        session_id,
        scan_id=scan_id,
    )
    sxm = load_sxm(archive_path)
    previews_dir = data_root / "previews" / session_id
    preview_path = _render_first_available(sxm, scan_id, previews_dir)

    metadata = sxm.to_metadata()
    metadata["import_source"] = source_label or str(source_path)
    metadata["imported_ts"] = time.time()
    if session_meta:
        metadata["import_session_meta"] = session_meta

    captured_ts = _captured_ts_from_metadata(metadata) or archive_path.stat().st_mtime
    sxm_storage_path = path_relative_to_data_root(archive_path, data_root)
    preview_storage_path = (
        path_relative_to_data_root(preview_path, data_root) if preview_path else None
    )
    writer.insert_scan(
        session_id=session_id,
        scan_id=scan_id,
        captured_ts=captured_ts,
        sxm_path=sxm_storage_path,
        preview_path=preview_storage_path,
        metadata=metadata,
    )
    writer.log_event(session_id, "historical_scan_imported", {
        "scan_id": scan_id,
        "source": source_label or str(source_path),
        "sxm_path": sxm_storage_path,
        "preview_path": preview_storage_path,
    }, ts=captured_ts)
    return {
        "scan_id": scan_id,
        "sxm_path": sxm_storage_path,
        "preview_path": preview_storage_path,
        "source": source_label or str(source_path),
    }


def _render_first_available(sxm, scan_id: str, previews_dir: Path) -> Optional[Path]:
    if not sxm.data:
        return None
    for channel_name in DEFAULT_PREVIEW_CHANNEL_PRIORITY:
        ch = sxm.data.get(channel_name)
        if ch and "forward" in ch:
            out = previews_dir / f"{scan_id}_{channel_name}_fwd.png"
            return render_preview(ch["forward"], out, title=f"{scan_id} / {channel_name} fwd")
    first_name = next(iter(sxm.data.keys()))
    first_dir = "forward" if "forward" in sxm.data[first_name] else next(iter(sxm.data[first_name].keys()))
    out = previews_dir / f"{scan_id}_{first_name}_{first_dir}.png"
    return render_preview(sxm.data[first_name][first_dir], out,
                          title=f"{scan_id} / {first_name} {first_dir}")


def _captured_ts_from_metadata(metadata: Dict[str, Any]) -> Optional[float]:
    # The parser keeps REC_DATE/REC_TIME as strings in metadata; for now we
    # use file mtime as a stable fallback rather than guessing locale formats.
    return None