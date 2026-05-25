"""Helpers for archiving Nanonis ``.sxm`` files inside a data root."""
from __future__ import annotations

import hashlib
import re
import shutil
import time
from pathlib import Path
from typing import BinaryIO, Optional


_SAFE_PART_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def safe_path_part(value: str, *, fallback: str = "item") -> str:
    """Return a filesystem-safe ASCII-ish path component."""
    cleaned = _SAFE_PART_RE.sub("_", (value or "").strip()).strip("._")
    return cleaned or fallback


def path_relative_to_data_root(path: Path | str, data_root: Path | str) -> str:
    """Store paths under ``data_root`` as portable POSIX-style relatives."""
    p = Path(path)
    root = Path(data_root)
    try:
        return p.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(p)


def hash_file(path: Path | str) -> str:
    digest = hashlib.sha1()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def archive_sxm_path(
    source_path: Path | str,
    data_root: Path | str,
    session_id: str,
    *,
    scan_id: Optional[str] = None,
) -> Path:
    """Copy an existing ``.sxm`` file into ``data_root/raw_sxm/<session>``."""
    source = Path(source_path)
    if not source.exists():
        raise FileNotFoundError(source)
    if source.suffix.lower() != ".sxm":
        raise ValueError(f"not an .sxm file: {source}")

    if scan_id is None:
        scan_id = f"{safe_path_part(source.stem, fallback='scan')}_{hash_file(source)[:8]}"
    archive_dir = Path(data_root) / "raw_sxm" / safe_path_part(session_id, fallback="session")
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / f"{safe_path_part(scan_id, fallback='scan')}.sxm"

    try:
        same_file = source.resolve() == archive_path.resolve()
    except OSError:
        same_file = False
    if not same_file:
        shutil.copy2(source, archive_path)
    return archive_path


def archive_sxm_stream(
    fileobj: BinaryIO,
    filename: str,
    data_root: Path | str,
    session_id: str,
) -> tuple[Path, str]:
    """Archive an uploaded ``.sxm`` stream and return ``(path, scan_id)``."""
    original = Path(filename or "scan.sxm")
    if original.suffix.lower() != ".sxm":
        raise ValueError(f"not an .sxm file: {filename}")
    archive_dir = Path(data_root) / "raw_sxm" / safe_path_part(session_id, fallback="session")
    archive_dir.mkdir(parents=True, exist_ok=True)

    tmp_path = archive_dir / f".{safe_path_part(original.stem, fallback='scan')}_{int(time.time() * 1000)}.tmp"
    digest = hashlib.sha1()
    with tmp_path.open("wb") as out:
        for chunk in iter(lambda: fileobj.read(1024 * 1024), b""):
            if not chunk:
                break
            digest.update(chunk)
            out.write(chunk)

    scan_id = f"{safe_path_part(original.stem, fallback='scan')}_{digest.hexdigest()[:8]}"
    archive_path = archive_dir / f"{safe_path_part(scan_id, fallback='scan')}.sxm"
    tmp_path.replace(archive_path)
    return archive_path, scan_id