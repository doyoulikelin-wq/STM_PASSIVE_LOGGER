"""Tests proving the read-only client refuses write/actuation calls."""
from __future__ import annotations

import pytest

from stm_experimenter_agent.nanonis_driver.client import (
    NanonisReadOnlyClient,
    WriteOperationNotAllowed,
)


@pytest.mark.parametrize("forbidden", [
    "Bias_Set",
    "Current_Set",
    "ZCtrl_OnOffSet",
    "AutoApproach_Open",
    "Motor_StartMove",
    "BiasPulse_Open",
    "TipShaper_Start",
    "TipShape_PropsSet",
    "Withdraw_Now",
])
def test_forbidden_methods_raise(forbidden: str) -> None:
    client = NanonisReadOnlyClient(host="127.0.0.1", port=1)
    with pytest.raises(WriteOperationNotAllowed):
        client._call(forbidden)


def test_non_allowlisted_read_method_is_refused() -> None:
    client = NanonisReadOnlyClient(host="127.0.0.1", port=1)
    with pytest.raises(WriteOperationNotAllowed):
        client._call("Some_RandomGet_NotInAllowList")


def test_first_scalar_unwraps_tuple_or_passthrough() -> None:
    # Real nanonis_spm shape: (err_desc, raw_bytes, parsed_values_list).
    assert NanonisReadOnlyClient._first_scalar(("", b"\x00", [1.5])) == 1.5
    assert NanonisReadOnlyClient._first_scalar(("", b"\x00", [9, "D:\\foo"])) == 9
    # Backwards-compatible legacy shape.
    assert NanonisReadOnlyClient._first_scalar((1.5,)) == 1.5
    assert NanonisReadOnlyClient._first_scalar([2]) == 2
    assert NanonisReadOnlyClient._first_scalar(3.0) == 3.0
    with pytest.raises(ValueError):
        NanonisReadOnlyClient._first_scalar(())
    with pytest.raises(ValueError):
        NanonisReadOnlyClient._first_scalar(("", b"", []))


class _FakeSpm:
    """Mimics nanonis_spm>=1.0.9: returns (err_desc, raw_body, parsed_list)."""

    def Bias_Get(self):
        return ("", b"\x00\x00\x00\x00", [1.25])

    def Current_Get(self):
        return ("", b"\x00\x00\x00\x00", [3.0e-10])

    def ZCtrl_ZPosGet(self):
        return ("", b"\x00\x00\x00\x00", [-1.5e-9])

    def ZCtrl_OnOffGet(self):
        return ("", b"\x00\x00\x00\x00", [1])

    def Scan_StatusGet(self):
        return ("", b"\x00\x00\x00\x00", [0])

    def Util_SessionPathGet(self):
        return ("", b"", [9, "D:\\STM\\S2"])

    def close(self):
        pass


def test_typed_accessors_unwrap_tuples_from_nanonis_spm() -> None:
    client = NanonisReadOnlyClient(host="127.0.0.1", port=1)
    client._spm = _FakeSpm()  # type: ignore[assignment]
    client._connected_port = 1
    assert client.bias_V() == pytest.approx(1.25)
    assert client.current_A() == pytest.approx(3.0e-10)
    assert client.z_m() == pytest.approx(-1.5e-9)
    assert client.z_controller_on() is True
    assert client.scan_status() == 0
    assert client.session_path() == "D:\\STM\\S2"


def test_snapshot_unwraps_tuples_and_records_no_errors() -> None:
    client = NanonisReadOnlyClient(host="127.0.0.1", port=1)
    client._spm = _FakeSpm()  # type: ignore[assignment]
    client._connected_port = 1
    snap = client.snapshot()
    assert snap.errors == {}
    assert snap.bias_V == pytest.approx(1.25)
    assert snap.z_controller_on is True
    assert snap.scan_status == 0
