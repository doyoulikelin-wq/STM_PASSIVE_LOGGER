"""Offline tests for the .sxm parser using a synthetic file."""
from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pytest

from stm_experimenter_agent.data_collection.sxm_parser import (
    load_sxm,
    parse_sxm_header,
)


def _build_synthetic_sxm(tmp_path: Path,
                         nx: int = 4, ny: int = 3,
                         z_forward: np.ndarray | None = None,
                         z_backward: np.ndarray | None = None,
                         current_forward: np.ndarray | None = None) -> Path:
    header = (
        ":NANONIS_VERSION:\n"
        "Generic 5e\n"
        ":SCAN_PIXELS:\n"
        f"\t{nx} {ny}\n"
        ":SCAN_RANGE:\n"
        "\t1.000E-7 1.000E-7\n"
        ":SCAN_OFFSET:\n"
        "\t0.000E+0 0.000E+0\n"
        ":SCAN_ANGLE:\n"
        "\t0\n"
        ":SCAN_DIR:\n"
        "\tdown\n"
        ":BIAS:\n"
        "\t1.0\n"
        ":Z-CONTROLLER:\n"
        "\tName\tSetpoint\tSetpoint unit\tP gain\tT const\n"
        "\tLog Current\t100E-12\tA\t1.0E-12\t0.001\n"
        ":REC_DATE:\n"
        "\t19.05.2026\n"
        ":REC_TIME:\n"
        "\t12:00:00\n"
        ":DATA_INFO:\n"
        "\tChannel\tName\tUnit\tDirection\tCalibration\tOffset\n"
        "\t14\tZ\tm\tboth\t1.000E+0\t0.000E+0\n"
        "\t0\tCurrent\tA\tforward\t1.000E+0\t0.000E+0\n"
        ":SCANIT_END:\n"
    )
    if z_forward is None:
        z_forward = np.arange(nx * ny, dtype=np.float32).reshape(ny, nx)
    if z_backward is None:
        # Store backward in scan order (right-to-left); parser must flip it.
        z_backward = z_forward[:, ::-1].copy()
    if current_forward is None:
        current_forward = (z_forward + 0.5).astype(np.float32)

    body = b""
    for frame in (z_forward, z_backward[:, ::-1], current_forward):
        body += frame.astype(">f4").tobytes()

    path = tmp_path / "test_scan.sxm"
    path.write_bytes(header.encode("latin-1") + b"\x1a\x04" + body)
    return path


def test_parse_header_extracts_core_fields(tmp_path: Path) -> None:
    path = _build_synthetic_sxm(tmp_path)
    sxm = parse_sxm_header(path)

    assert sxm.pixels == (4, 3)
    assert sxm.scan_range_m == pytest.approx((1e-7, 1e-7))
    assert sxm.scan_dir == "down"
    assert sxm.bias_V == pytest.approx(1.0)
    assert sxm.setpoint == pytest.approx(100e-12)
    assert sxm.setpoint_unit == "A"
    assert sxm.nanonis_version == "Generic 5e"
    assert sxm.rec_date == "19.05.2026"
    names = [c.name for c in sxm.channels]
    assert names == ["Z", "Current"]
    z_chan = next(c for c in sxm.channels if c.name == "Z")
    assert z_chan.direction == "both"
    assert z_chan.n_frames == 2


def test_load_sxm_returns_correct_arrays(tmp_path: Path) -> None:
    z = np.array([[1, 2, 3, 4], [5, 6, 7, 8], [9, 10, 11, 12]], dtype=np.float32)
    # Pass identical forward/backward so we can assert the parser's
    # right-to-left -> left-to-right flip is correct.
    path = _build_synthetic_sxm(tmp_path, nx=4, ny=3, z_forward=z, z_backward=z)
    sxm = load_sxm(path)

    np.testing.assert_array_equal(sxm.data["Z"]["forward"], z)
    np.testing.assert_array_equal(sxm.data["Z"]["backward"], z)
    np.testing.assert_array_equal(sxm.data["Current"]["forward"], z + 0.5)


def test_truncated_file_is_padded(tmp_path: Path) -> None:
    path = _build_synthetic_sxm(tmp_path, nx=4, ny=3)
    # Chop off the last 10 bytes of binary data.
    raw = path.read_bytes()
    path.write_bytes(raw[:-10])
    sxm = load_sxm(path)
    assert sxm.data["Z"]["forward"].shape == (3, 4)


def test_missing_sentinel_raises(tmp_path: Path) -> None:
    path = tmp_path / "broken.sxm"
    path.write_bytes(b":SCAN_PIXELS:\n4 3\n")
    with pytest.raises(ValueError):
        parse_sxm_header(path)
