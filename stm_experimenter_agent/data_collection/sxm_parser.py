"""Minimal Nanonis ``.sxm`` parser.

Goals
-----
* Pure-Python, depends only on numpy.
* Parse the ASCII header into a tag dict regardless of CR/LF style.
* Extract scan parameters and channel data table.
* Lazy load: ``parse_sxm_header`` does no numpy work so it is safe to call
  from the file watcher; ``load_sxm`` reads channel arrays on demand.

The Nanonis ``.sxm`` layout used here:

* Header is ASCII, sections introduced by a line ``:TAGNAME:`` followed by
  one or more value lines until the next ``:TAG:`` line.
* Header terminates with the bytes ``\\x1a\\x04`` (after which the binary
  data section starts). We locate this sentinel directly, so trailing
  whitespace differences between firmware versions are tolerated.
* Binary section: big-endian float32, ordered as
  ``channel0_forward, [channel0_backward,] channel1_forward, ...`` where
  the backward frame is present when the ``Direction`` column in
  ``:DATA_INFO:`` is ``both``.
* Each frame is ``Ny`` rows of ``Nx`` columns (rows are written
  top-to-bottom in scan order). Callers may need to flip vertically
  depending on the Nanonis ``SCAN_DIR`` setting.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

_HEADER_SENTINEL = b"\x1a\x04"


@dataclass
class ChannelInfo:
    index: int
    name: str
    unit: str
    direction: str  # 'forward' | 'backward' | 'both'

    @property
    def n_frames(self) -> int:
        return 2 if self.direction.lower() == "both" else 1


@dataclass
class SxmFile:
    path: Path
    tags: Dict[str, str] = field(default_factory=dict)
    pixels: Tuple[int, int] = (0, 0)              # (Nx, Ny)
    scan_range_m: Tuple[float, float] = (0.0, 0.0)
    scan_offset_m: Tuple[float, float] = (0.0, 0.0)
    scan_angle_deg: float = 0.0
    bias_V: Optional[float] = None
    setpoint: Optional[float] = None              # raw setpoint, unit in setpoint_unit
    setpoint_unit: Optional[str] = None
    rec_date: Optional[str] = None
    rec_time: Optional[str] = None
    nanonis_version: Optional[str] = None
    channels: List[ChannelInfo] = field(default_factory=list)
    _header_size: int = 0  # bytes from start of file to first data byte

    # populated by load_sxm
    data: Dict[str, Dict[str, np.ndarray]] = field(default_factory=dict)

    def channel_array(self, name: str, direction: str = "forward") -> Optional[np.ndarray]:
        return self.data.get(name, {}).get(direction)

    def to_metadata(self) -> Dict[str, object]:
        """Compact JSON-friendly metadata for SQLite/JSONL."""
        return {
            "path": str(self.path),
            "pixels": list(self.pixels),
            "scan_range_m": list(self.scan_range_m),
            "scan_offset_m": list(self.scan_offset_m),
            "scan_angle_deg": self.scan_angle_deg,
            "bias_V": self.bias_V,
            "setpoint": self.setpoint,
            "setpoint_unit": self.setpoint_unit,
            "rec_date": self.rec_date,
            "rec_time": self.rec_time,
            "nanonis_version": self.nanonis_version,
            "channels": [
                {"name": c.name, "unit": c.unit, "direction": c.direction}
                for c in self.channels
            ],
        }


# ---------------------------------------------------------------------------
# Header parsing
# ---------------------------------------------------------------------------

def _split_header(blob: bytes) -> Tuple[str, int]:
    idx = blob.find(_HEADER_SENTINEL)
    if idx < 0:
        raise ValueError("Not a Nanonis .sxm file: missing \\x1a\\x04 sentinel")
    header_bytes = blob[:idx]
    data_offset = idx + len(_HEADER_SENTINEL)
    return header_bytes.decode("latin-1"), data_offset


def _tagify(header_text: str) -> Dict[str, str]:
    """Split a Nanonis header into a {TAG: text} dict.

    Tag lines look like ``:NANONIS_VERSION:`` and stand alone on a line.
    Everything until the next tag belongs to the current tag's value.
    """
    tags: Dict[str, str] = {}
    current_tag: Optional[str] = None
    current_lines: List[str] = []
    for raw_line in header_text.splitlines():
        line = raw_line.rstrip("\r")
        stripped = line.strip()
        if (
            len(stripped) >= 3
            and stripped.startswith(":")
            and stripped.endswith(":")
            and ":" not in stripped[1:-1]
        ):
            if current_tag is not None:
                tags[current_tag] = "\n".join(current_lines).strip()
            current_tag = stripped[1:-1]
            current_lines = []
        else:
            current_lines.append(line)
    if current_tag is not None:
        tags[current_tag] = "\n".join(current_lines).strip()
    return tags


def _parse_float_pair(text: str) -> Tuple[float, float]:
    parts = text.split()
    if len(parts) < 2:
        raise ValueError(f"Expected two floats, got: {text!r}")
    return float(parts[0]), float(parts[1])


def _parse_int_pair(text: str) -> Tuple[int, int]:
    parts = text.split()
    if len(parts) < 2:
        raise ValueError(f"Expected two ints, got: {text!r}")
    return int(parts[0]), int(parts[1])


def _parse_data_info(text: str) -> List[ChannelInfo]:
    """Parse the ``:DATA_INFO:`` table.

    Example::

        Channel\tName\tUnit\tDirection\tCalibration\tOffset
        14\tZ\tm\tboth\t1.000E+0\t0.000E+0
        0\tCurrent\tA\tboth\t1.000E+0\t0.000E+0
    """
    channels: List[ChannelInfo] = []
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return channels
    # First line is a header; skip if it does not start with a digit/whitespace number.
    start = 1 if not lines[0].lstrip().split()[:1] or not lines[0].lstrip().split()[0].lstrip("-").isdigit() else 0
    for line in lines[start:]:
        parts = line.split()
        if len(parts) < 4:
            continue
        try:
            idx = int(parts[0])
        except ValueError:
            continue
        name = parts[1]
        unit = parts[2]
        direction = parts[3].lower()
        channels.append(ChannelInfo(index=idx, name=name, unit=unit, direction=direction))
    return channels


def _parse_zcontroller_setpoint(text: str) -> Tuple[Optional[float], Optional[str]]:
    """Best-effort extraction of setpoint from the ``:Z-CONTROLLER:`` table.

    Format varies, but typical Mimea output has a header row followed by a
    data row containing ``Setpoint`` in scientific notation with a unit.
    """
    # Strip leading whitespace (Nanonis indents table rows with a tab) before
    # splitting on tab so that an empty leading cell does not shift the index.
    lines = [ln.lstrip() for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return None, None
    header_cols = lines[0].split("\t") if "\t" in lines[0] else lines[0].split()
    value_cols = lines[1].split("\t") if "\t" in lines[1] else lines[1].split()
    try:
        sp_idx = next(i for i, c in enumerate(header_cols) if c.lower().startswith("setpoint"))
    except StopIteration:
        return None, None
    if sp_idx >= len(value_cols):
        return None, None
    token = value_cols[sp_idx].strip()
    # token may be "100E-12" alone or "100E-12 A" with the unit appended.
    parts = token.split()
    try:
        val = float(parts[0])
    except ValueError:
        return None, None
    unit = parts[1] if len(parts) > 1 else None
    # Fall back to a dedicated "Setpoint unit" column if present.
    if unit is None:
        for j, col in enumerate(header_cols):
            if col.strip().lower() == "setpoint unit" and j < len(value_cols):
                unit = value_cols[j].strip() or None
                break
    return val, unit


def parse_sxm_header(path: Path | str, *, max_header_bytes: int = 2 * 1024 * 1024) -> SxmFile:
    """Parse only the header of an ``.sxm`` file.

    Reads up to ``max_header_bytes`` from disk, which is enough for any
    realistic Nanonis header (typically < 200 KB).
    """
    path = Path(path)
    with path.open("rb") as fh:
        head = fh.read(max_header_bytes)
        # If sentinel not found in the first chunk, fall back to full read.
        if _HEADER_SENTINEL not in head:
            head = head + fh.read()

    header_text, data_offset = _split_header(head)
    tags = _tagify(header_text)

    sxm = SxmFile(path=path, tags=tags, _header_size=data_offset)
    if "SCAN_PIXELS" in tags:
        sxm.pixels = _parse_int_pair(tags["SCAN_PIXELS"])
    if "SCAN_RANGE" in tags:
        sxm.scan_range_m = _parse_float_pair(tags["SCAN_RANGE"])
    if "SCAN_OFFSET" in tags:
        sxm.scan_offset_m = _parse_float_pair(tags["SCAN_OFFSET"])
    if "SCAN_ANGLE" in tags:
        try:
            sxm.scan_angle_deg = float(tags["SCAN_ANGLE"].split()[0])
        except (ValueError, IndexError):
            pass
    if "BIAS" in tags:
        try:
            sxm.bias_V = float(tags["BIAS"].split()[0])
        except (ValueError, IndexError):
            pass
    if "REC_DATE" in tags:
        sxm.rec_date = tags["REC_DATE"].strip()
    if "REC_TIME" in tags:
        sxm.rec_time = tags["REC_TIME"].strip()
    if "NANONIS_VERSION" in tags:
        sxm.nanonis_version = tags["NANONIS_VERSION"].strip()
    if "Z-CONTROLLER" in tags:
        sxm.setpoint, sxm.setpoint_unit = _parse_zcontroller_setpoint(tags["Z-CONTROLLER"])
    if "DATA_INFO" in tags:
        sxm.channels = _parse_data_info(tags["DATA_INFO"])
    return sxm


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_sxm(path: Path | str) -> SxmFile:
    """Parse header and load all channel arrays into ``sxm.data``."""
    sxm = parse_sxm_header(path)
    nx, ny = sxm.pixels
    if nx <= 0 or ny <= 0:
        return sxm
    n_pixels_per_frame = nx * ny

    n_frames_total = sum(c.n_frames for c in sxm.channels)
    expected_bytes = n_frames_total * n_pixels_per_frame * 4

    with open(sxm.path, "rb") as fh:
        fh.seek(sxm._header_size)
        raw = fh.read(expected_bytes)
    if len(raw) < expected_bytes:
        # Truncated file: parse only what we can; do not raise so a partially
        # saved scan still ends up indexed.
        raw += b"\x00" * (expected_bytes - len(raw))

    arr = np.frombuffer(raw, dtype=">f4").astype(np.float32, copy=False)
    cursor = 0
    for ch in sxm.channels:
        ch_dict: Dict[str, np.ndarray] = {}
        for direction in ("forward", "backward")[: ch.n_frames]:
            frame = arr[cursor:cursor + n_pixels_per_frame].reshape(ny, nx)
            # Backward frames are stored right-to-left in scan order;
            # flip horizontally so spatial coordinates align with forward.
            if direction == "backward":
                frame = np.ascontiguousarray(frame[:, ::-1])
            ch_dict[direction] = frame
            cursor += n_pixels_per_frame
        sxm.data[ch.name] = ch_dict
    return sxm
