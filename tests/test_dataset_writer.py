"""Tests for the SQLite + JSONL DatasetWriter."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from stm_experimenter_agent.data_collection.dataset_writer import DatasetWriter


def test_session_signals_scans_events_roundtrip(tmp_path: Path) -> None:
    writer = DatasetWriter(tmp_path)
    sid = "20260519_test"
    writer.upsert_session(session_id=sid, start_ts=100.0, operator="alice",
                          sample_id="sA", instrument={"version": {"ok": True}})

    n = writer.insert_signals(sid, [
        {"ts": 101.0, "bias_V": 1.0, "current_A": 1e-10, "z_m": -5e-9,
         "z_controller_on": True, "scan_status": 0, "errors": {}},
        {"ts": 102.0, "bias_V": 1.0, "current_A": 9e-11, "z_m": -5e-9,
         "z_controller_on": True, "scan_status": 1, "errors": {}},
    ])
    assert n == 2

    writer.insert_scan(
        session_id=sid, scan_id="scan_001", captured_ts=103.0,
        sxm_path=str(tmp_path / "fake.sxm"), preview_path=None,
        metadata={
            "pixels": [256, 256], "scan_range_m": [1e-7, 1e-7],
            "scan_offset_m": [0.0, 0.0], "scan_angle_deg": 0.0,
            "bias_V": 1.0, "setpoint": 1e-10, "setpoint_unit": "A",
            "channels": [{"name": "Z", "unit": "m", "direction": "both"}],
        },
    )
    writer.log_event(sid, "operator_note", {"text": "good area"}, ts=104.0)
    writer.upsert_session(session_id=sid, start_ts=0.0, end_ts=200.0)
    writer.close()

    # Re-open SQLite directly and verify rows landed.
    conn = sqlite3.connect(tmp_path / "session.sqlite")
    try:
        rows = conn.execute("SELECT operator, sample_id, end_ts FROM sessions").fetchall()
        assert rows == [("alice", "sA", 200.0)]
        assert conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0] == 2
        scan_row = conn.execute(
            "SELECT scan_id, bias_V, pixels_x FROM scans"
        ).fetchone()
        assert scan_row == ("scan_001", 1.0, 256)
        evt = conn.execute("SELECT kind, payload FROM events WHERE kind='operator_note'").fetchone()
        assert evt[0] == "operator_note"
        assert json.loads(evt[1])["text"] == "good area"
    finally:
        conn.close()

    # JSONL mirror must exist and be readable.
    signals_jsonl = tmp_path / "sessions" / sid / "signals.jsonl"
    assert signals_jsonl.exists()
    lines = signals_jsonl.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    for line in lines:
        assert json.loads(line)["bias_V"] == 1.0


def test_count_helpers(tmp_path: Path) -> None:
    writer = DatasetWriter(tmp_path)
    sid = "s1"
    writer.upsert_session(session_id=sid, start_ts=0.0)
    writer.insert_signals(sid, [{"ts": 1.0}])
    assert writer.fetch_signal_count(sid) == 1
    assert writer.fetch_scan_count(sid) == 0
    writer.close()
