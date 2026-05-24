"""SQLite + JSONL dataset writer.

Design goals
------------
* **Append-only JSONL** for forensic durability: easy to grep, easy to
  recover even if SQLite is corrupted.
* **SQLite** for query-friendly indexing (joins between sessions, scans,
  signals, events).
* **Thread-safe**: a single :class:`DatasetWriter` may be shared between
  the signal logger thread, scan watcher thread, and the CLI main thread.
* **Schema migrations** are not needed in V0 because the schema is fresh,
  but we set ``user_version`` so future versions can detect old DBs.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from dataclasses import is_dataclass, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id   TEXT PRIMARY KEY,
    start_ts     REAL NOT NULL,
    end_ts       REAL,
    operator     TEXT,
    sample_id    TEXT,
    tip_id       TEXT,
    material     TEXT,
    notes        TEXT,
    instrument   TEXT  -- JSON
);

CREATE TABLE IF NOT EXISTS scans (
    scan_id      TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL REFERENCES sessions(session_id),
    captured_ts  REAL NOT NULL,
    sxm_path     TEXT NOT NULL,
    preview_path TEXT,
    bias_V       REAL,
    setpoint     REAL,
    setpoint_unit TEXT,
    pixels_x     INTEGER,
    pixels_y     INTEGER,
    range_x_m    REAL,
    range_y_m    REAL,
    offset_x_m   REAL,
    offset_y_m   REAL,
    angle_deg    REAL,
    channels     TEXT,  -- JSON list
    metadata     TEXT   -- JSON
);
CREATE INDEX IF NOT EXISTS ix_scans_session ON scans(session_id);
CREATE INDEX IF NOT EXISTS ix_scans_ts ON scans(captured_ts);

CREATE TABLE IF NOT EXISTS signals (
    session_id   TEXT NOT NULL REFERENCES sessions(session_id),
    ts           REAL NOT NULL,
    bias_V       REAL,
    current_A    REAL,
    z_m          REAL,
    z_ctrl_on    INTEGER,
    scan_status  INTEGER,
    errors       TEXT
);
CREATE INDEX IF NOT EXISTS ix_signals_session_ts ON signals(session_id, ts);

CREATE TABLE IF NOT EXISTS events (
    session_id   TEXT NOT NULL REFERENCES sessions(session_id),
    ts           REAL NOT NULL,
    kind         TEXT NOT NULL,
    payload      TEXT  -- JSON
);
CREATE INDEX IF NOT EXISTS ix_events_session_ts ON events(session_id, ts);

-- Offline image annotation (V1). Written by the annotation server,
-- never by the live logger. Multiple annotators may label the same scan;
-- repeated labels by the same annotator overwrite (we keep updated_ts only).
CREATE TABLE IF NOT EXISTS labels (
    scan_id              TEXT NOT NULL REFERENCES scans(scan_id),
    annotator            TEXT NOT NULL,
    created_ts           REAL NOT NULL,
    updated_ts           REAL NOT NULL,

    surface_quality      TEXT,
    substrate            TEXT,
    thin_film            TEXT,
    molecule             TEXT,
    image_quality        TEXT,
    tip_state            TEXT,
    artifact_tags        TEXT,   -- JSON array
    research_value_label TEXT,
    research_value_score REAL,
    next_action          TEXT,
    confidence           REAL,
    reason_text          TEXT,
    annotator_notes      TEXT,   -- free-text observations from annotator

    review_status        TEXT,   -- NULL / accept / dispute / need_redo
    review_comment       TEXT,
    reviewer             TEXT,
    reviewed_ts          REAL,

    PRIMARY KEY (scan_id, annotator)
);
CREATE INDEX IF NOT EXISTS ix_labels_annotator ON labels(annotator);
CREATE INDEX IF NOT EXISTS ix_labels_review ON labels(review_status);
"""

_LABEL_COLUMN_MIGRATIONS = {
    "substrate": "ALTER TABLE labels ADD COLUMN substrate TEXT",
    "thin_film": "ALTER TABLE labels ADD COLUMN thin_film TEXT",
    "molecule": "ALTER TABLE labels ADD COLUMN molecule TEXT",
}


def _to_jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    return value


class DatasetWriter:
    """Open a SQLite DB and a per-session set of JSONL append logs."""

    def __init__(self, data_root: Path | str) -> None:
        self.data_root = Path(data_root)
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.sessions_root = self.data_root / "sessions"
        self.sessions_root.mkdir(parents=True, exist_ok=True)
        self.previews_root = self.data_root / "previews"
        self.previews_root.mkdir(parents=True, exist_ok=True)

        self._db_path = self.data_root / "session.sqlite"
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False, timeout=30.0)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA)
        self._ensure_migrations()
        self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self._conn.commit()

        # cached JSONL handles per session_id and stream name
        self._jsonl_handles: Dict[tuple, Any] = {}

    def _ensure_migrations(self) -> None:
        """Apply additive schema migrations for older SQLite datasets."""
        existing = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(labels)").fetchall()
        }
        for column, sql in _LABEL_COLUMN_MIGRATIONS.items():
            if column not in existing:
                self._conn.execute(sql)

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        with self._lock:
            for fh in self._jsonl_handles.values():
                try:
                    fh.flush()
                    fh.close()
                except Exception:  # noqa: BLE001
                    logger.debug("Closing JSONL handle failed", exc_info=True)
            self._jsonl_handles.clear()
            try:
                self._conn.commit()
            finally:
                self._conn.close()

    def __enter__(self) -> "DatasetWriter":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # -- JSONL helpers -----------------------------------------------------
    def _session_dir(self, session_id: str) -> Path:
        d = self.sessions_root / session_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _jsonl_handle(self, session_id: str, stream: str):
        key = (session_id, stream)
        handle = self._jsonl_handles.get(key)
        if handle is None:
            path = self._session_dir(session_id) / f"{stream}.jsonl"
            handle = open(path, "a", encoding="utf-8", buffering=1)  # line-buffered
            self._jsonl_handles[key] = handle
        return handle

    def _append_jsonl(self, session_id: str, stream: str, payload: Dict[str, Any]) -> None:
        handle = self._jsonl_handle(session_id, stream)
        handle.write(json.dumps(payload, ensure_ascii=False, default=_to_jsonable) + "\n")

    # -- sessions ----------------------------------------------------------
    def upsert_session(self, *, session_id: str, start_ts: float,
                       operator: Optional[str] = None,
                       sample_id: Optional[str] = None,
                       tip_id: Optional[str] = None,
                       material: Optional[str] = None,
                       notes: Optional[str] = None,
                       instrument: Optional[Dict[str, Any]] = None,
                       end_ts: Optional[float] = None) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO sessions(session_id, start_ts, end_ts, operator, sample_id,
                                     tip_id, material, notes, instrument)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    end_ts = COALESCE(excluded.end_ts, sessions.end_ts),
                    operator = COALESCE(excluded.operator, sessions.operator),
                    sample_id = COALESCE(excluded.sample_id, sessions.sample_id),
                    tip_id = COALESCE(excluded.tip_id, sessions.tip_id),
                    material = COALESCE(excluded.material, sessions.material),
                    notes = COALESCE(excluded.notes, sessions.notes),
                    instrument = COALESCE(excluded.instrument, sessions.instrument)
                """,
                (
                    session_id, start_ts, end_ts, operator, sample_id, tip_id,
                    material, notes,
                    json.dumps(instrument, ensure_ascii=False) if instrument else None,
                ),
            )
            self._conn.commit()
        self._append_jsonl(session_id, "events", {
            "ts": time.time(),
            "kind": "session_upsert",
            "session_id": session_id,
            "operator": operator,
            "sample_id": sample_id,
            "tip_id": tip_id,
            "material": material,
            "instrument": instrument,
            "end_ts": end_ts,
        })

    # -- scans -------------------------------------------------------------
    def insert_scan(self, *, session_id: str, scan_id: str, captured_ts: float,
                    sxm_path: str, preview_path: Optional[str],
                    metadata: Dict[str, Any]) -> None:
        pixels = metadata.get("pixels") or [None, None]
        scan_range = metadata.get("scan_range_m") or [None, None]
        offset = metadata.get("scan_offset_m") or [None, None]
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO scans(scan_id, session_id, captured_ts, sxm_path,
                    preview_path, bias_V, setpoint, setpoint_unit,
                    pixels_x, pixels_y, range_x_m, range_y_m,
                    offset_x_m, offset_y_m, angle_deg, channels, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scan_id, session_id, captured_ts, sxm_path, preview_path,
                    metadata.get("bias_V"), metadata.get("setpoint"),
                    metadata.get("setpoint_unit"),
                    pixels[0] if len(pixels) > 0 else None,
                    pixels[1] if len(pixels) > 1 else None,
                    scan_range[0] if len(scan_range) > 0 else None,
                    scan_range[1] if len(scan_range) > 1 else None,
                    offset[0] if len(offset) > 0 else None,
                    offset[1] if len(offset) > 1 else None,
                    metadata.get("scan_angle_deg"),
                    json.dumps(metadata.get("channels"), ensure_ascii=False),
                    json.dumps(metadata, ensure_ascii=False),
                ),
            )
            self._conn.commit()
        self._append_jsonl(session_id, "scans", {
            "ts": captured_ts,
            "scan_id": scan_id,
            "sxm_path": sxm_path,
            "preview_path": preview_path,
            "metadata": metadata,
        })

    # -- signals -----------------------------------------------------------
    def insert_signals(self, session_id: str, samples: Iterable[Dict[str, Any]]) -> int:
        rows = []
        json_lines = []
        for s in samples:
            rows.append((
                session_id,
                float(s.get("ts", time.time())),
                s.get("bias_V"),
                s.get("current_A"),
                s.get("z_m"),
                int(s["z_controller_on"]) if s.get("z_controller_on") is not None else None,
                s.get("scan_status"),
                json.dumps(s.get("errors") or {}, ensure_ascii=False),
            ))
            json_lines.append(s)
        if not rows:
            return 0
        with self._lock:
            self._conn.executemany(
                """
                INSERT INTO signals(session_id, ts, bias_V, current_A, z_m,
                                    z_ctrl_on, scan_status, errors)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            self._conn.commit()
        handle = self._jsonl_handle(session_id, "signals")
        for s in json_lines:
            handle.write(json.dumps(s, ensure_ascii=False, default=_to_jsonable) + "\n")
        return len(rows)

    # -- events ------------------------------------------------------------
    def log_event(self, session_id: str, kind: str,
                  payload: Optional[Dict[str, Any]] = None, ts: Optional[float] = None) -> None:
        ts = ts if ts is not None else time.time()
        payload = payload or {}
        with self._lock:
            self._conn.execute(
                "INSERT INTO events(session_id, ts, kind, payload) VALUES (?, ?, ?, ?)",
                (session_id, ts, kind, json.dumps(payload, ensure_ascii=False, default=_to_jsonable)),
            )
            self._conn.commit()
        self._append_jsonl(session_id, "events", {"ts": ts, "kind": kind, **payload})

    # -- query helpers (used by reports/tests) -----------------------------
    def fetch_signal_count(self, session_id: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "SELECT COUNT(*) FROM signals WHERE session_id = ?", (session_id,)
            )
            return int(cur.fetchone()[0])

    def fetch_scan_count(self, session_id: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "SELECT COUNT(*) FROM scans WHERE session_id = ?", (session_id,)
            )
            return int(cur.fetchone()[0])
