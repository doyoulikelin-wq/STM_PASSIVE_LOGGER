"""Offline tests for the raw protocol helpers."""
from __future__ import annotations

import struct

import pytest

from stm_experimenter_agent.nanonis_driver.raw_protocol import (
    RawNanonisProtocol,
    _decode_six_strings,
)


def test_decode_six_strings_handles_valid_payload() -> None:
    parts = [b"App", b"Ctrl", b"RT", b"FPGA", b"Sigs", b"DSP"]
    body = b"".join(struct.pack(">I", len(p)) + p for p in parts)
    out = _decode_six_strings(body)
    assert out == ("App", "Ctrl", "RT", "FPGA", "Sigs", "DSP")


def test_decode_six_strings_tolerates_truncation() -> None:
    body = struct.pack(">I", 3) + b"App"
    out = _decode_six_strings(body)
    assert out[0] == "App"
    assert out[1:] == ("", "", "", "", "")


def test_raw_protocol_rejects_unknown_command() -> None:
    proto = RawNanonisProtocol("127.0.0.1", 1)
    # Bypass connect; the allow-list check happens before any socket use.
    with pytest.raises(PermissionError):
        proto._send_command("Bias.Set", b"")
