"""SQLite access for the annotation UI.

This module deliberately re-opens the dataset DB (instead of going through
:class:`DatasetWriter`) because:

* the annotation tool runs offline, possibly long after the logger session
  ended;
* we want a clear separation: logger only writes signals/scans/events,
  annotation server only writes labels.

It still calls :class:`DatasetWriter` once on startup to make sure the
``labels`` table exists for old DBs created before V1.
"""
from __future__ import annotations

import json
import sqlite3
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..data_collection.dataset_writer import DatasetWriter


# Columns the UI is allowed to write into ``labels``. Keep in sync with
# ``label_schema.yaml`` field names + the extra free-text/review fields.
_WRITABLE_LABEL_FIELDS = (
    "surface_quality",
    "substrate",
    "thin_film",
    "molecule",
    "image_quality",
    "tip_state",
    "artifact_tags",            # JSON-encoded list
    "research_value_label",
    "research_value_score",
    "next_action",
    "confidence",
    "reason_text",
    "annotator_notes",
)

_REVIEW_STATUSES = ("accept", "dispute", "need_redo")

_DISTRIBUTION_FIELDS = (
    "substrate",
    "thin_film",
    "molecule",
    "image_quality",
    "tip_state",
    "surface_quality",
    "research_value_label",
    "next_action",
    "review_status",
)


class AnnotationStore:
    """Thin SQLite helper for the annotation server.

    Connection is opened with ``check_same_thread=False`` so the stdlib
    ``ThreadingHTTPServer`` can call into it from worker threads. All
    writes go through ``_lock`` to keep them serialised.
    """

    def __init__(self, data_root: Path | str) -> None:
        self.data_root = Path(data_root)
        if not self.data_root.exists():
            raise FileNotFoundError(f"data_root does not exist: {self.data_root}")
        self.db_path = self.data_root / "session.sqlite"
        if not self.db_path.exists():
            raise FileNotFoundError(
                f"session.sqlite not found under {self.data_root}; "
                "run `stm-logger start` at least once first."
            )
        # Touching DatasetWriter ensures the ``labels`` table exists on
        # databases that were created before V1.
        DatasetWriter(self.data_root).close()

        import threading
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False,
                                     timeout=30.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- queries -----------------------------------------------------------

    def stats(self, annotator: Optional[str] = None) -> Dict[str, int]:
        """Counts of total scans / labelled / unlabelled (for a given annotator)."""
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) FROM scans").fetchone()[0]
            if annotator:
                done = self._conn.execute(
                    "SELECT COUNT(*) FROM labels WHERE annotator = ?",
                    (annotator,),
                ).fetchone()[0]
            else:
                done = self._conn.execute(
                    "SELECT COUNT(DISTINCT scan_id) FROM labels"
                ).fetchone()[0]
            pending_review = self._conn.execute(
                "SELECT COUNT(*) FROM labels WHERE review_status IS NULL"
            ).fetchone()[0]
        return {
            "total_scans": int(total),
            "labelled": int(done),
            "unlabelled": int(total - done),
            "labels_awaiting_review": int(pending_review),
        }

    def list_scans(
        self,
        *,
        annotator: Optional[str] = None,
        mode: str = "unlabeled",   # "unlabeled" | "labeled" | "all"
        session_id: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """List scans for the annotation UI side-panel.

        ``mode='unlabeled'`` means: scans that *this* annotator has not yet
        labelled. Other annotators may already have labelled them.
        """
        sql = [
            "SELECT s.scan_id, s.session_id, s.captured_ts, s.sxm_path,",
            "       s.preview_path, s.bias_V, s.pixels_x, s.pixels_y,",
            "       s.range_x_m, s.range_y_m,",
            "       (SELECT COUNT(*) FROM labels l WHERE l.scan_id = s.scan_id) AS n_labels,",
            "       EXISTS(SELECT 1 FROM labels l WHERE l.scan_id = s.scan_id",
            "              AND l.annotator = ?) AS labelled_by_me",
            "FROM scans s",
            "WHERE 1=1",
        ]
        params: List[Any] = [annotator or ""]
        if session_id:
            sql.append("AND s.session_id = ?")
            params.append(session_id)
        if mode == "unlabeled" and annotator:
            sql.append(
                "AND NOT EXISTS(SELECT 1 FROM labels l WHERE l.scan_id = s.scan_id"
                " AND l.annotator = ?)"
            )
            params.append(annotator)
        elif mode == "labeled" and annotator:
            sql.append(
                "AND EXISTS(SELECT 1 FROM labels l WHERE l.scan_id = s.scan_id"
                " AND l.annotator = ?)"
            )
            params.append(annotator)
        sql.append("ORDER BY s.captured_ts DESC")
        sql.append("LIMIT ?")
        params.append(int(limit))
        with self._lock:
            rows = self._conn.execute(" ".join(sql), params).fetchall()
        return [dict(r) for r in rows]

    def list_sessions(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT session_id, start_ts, end_ts, operator, sample_id, tip_id,"
                "       material,"
                "       (SELECT COUNT(*) FROM scans s WHERE s.session_id = sessions.session_id) AS n_scans,"
                "       (SELECT COUNT(DISTINCT l.scan_id) FROM labels l"
                "          JOIN scans s ON s.scan_id = l.scan_id"
                "         WHERE s.session_id = sessions.session_id) AS n_labelled_scans"
                " FROM sessions ORDER BY start_ts DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def session_overview(
        self,
        *,
        session_id: Optional[str] = None,
        annotator: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return per-session annotation progress and label distributions."""
        where_scan = ["1=1"]
        scan_params: List[Any] = []
        if session_id:
            where_scan.append("s.session_id = ?")
            scan_params.append(session_id)
        where_scan_sql = " AND ".join(where_scan)

        where_label = ["1=1"]
        label_params: List[Any] = []
        if session_id:
            where_label.append("s.session_id = ?")
            label_params.append(session_id)
        where_label_sql = " AND ".join(where_label)

        with self._lock:
            session = None
            if session_id:
                row = self._conn.execute(
                    "SELECT * FROM sessions WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
                session = dict(row) if row else None

            total_scans = self._conn.execute(
                f"SELECT COUNT(*) FROM scans s WHERE {where_scan_sql}",
                scan_params,
            ).fetchone()[0]
            labelled_scans = self._conn.execute(
                f"SELECT COUNT(DISTINCT l.scan_id) FROM labels l"
                f" JOIN scans s ON s.scan_id = l.scan_id WHERE {where_label_sql}",
                label_params,
            ).fetchone()[0]
            total_labels = self._conn.execute(
                f"SELECT COUNT(*) FROM labels l"
                f" JOIN scans s ON s.scan_id = l.scan_id WHERE {where_label_sql}",
                label_params,
            ).fetchone()[0]
            pending_review = self._conn.execute(
                f"SELECT COUNT(*) FROM labels l"
                f" JOIN scans s ON s.scan_id = l.scan_id"
                f" WHERE {where_label_sql} AND l.review_status IS NULL",
                label_params,
            ).fetchone()[0]

            labelled_by_annotator = None
            if annotator:
                labelled_by_annotator = self._conn.execute(
                    f"SELECT COUNT(*) FROM labels l"
                    f" JOIN scans s ON s.scan_id = l.scan_id"
                    f" WHERE {where_label_sql} AND l.annotator = ?",
                    [*label_params, annotator],
                ).fetchone()[0]

            rows = self._conn.execute(
                f"SELECT l.* FROM labels l"
                f" JOIN scans s ON s.scan_id = l.scan_id WHERE {where_label_sql}",
                label_params,
            ).fetchall()

        distributions: Dict[str, Dict[str, int]] = {
            field: {} for field in _DISTRIBUTION_FIELDS
        }
        artifact_counter: Counter[str] = Counter()
        for row in rows:
            label = dict(row)
            for field in _DISTRIBUTION_FIELDS:
                value = label.get(field)
                if value is None or value == "":
                    continue
                distributions[field][str(value)] = distributions[field].get(str(value), 0) + 1
            raw_tags = label.get("artifact_tags")
            if raw_tags:
                try:
                    tags = json.loads(raw_tags)
                except (TypeError, ValueError):
                    tags = []
                if isinstance(tags, list):
                    for tag in tags:
                        if tag:
                            artifact_counter[str(tag)] += 1

        distributions["artifact_tags"] = dict(artifact_counter.most_common())
        return {
            "session_id": session_id,
            "session": session,
            "total_scans": int(total_scans),
            "labelled_scans": int(labelled_scans),
            "unlabelled_scans": int(total_scans - labelled_scans),
            "total_labels": int(total_labels),
            "labels_awaiting_review": int(pending_review),
            "annotator": annotator,
            "labelled_by_annotator": (
                int(labelled_by_annotator) if labelled_by_annotator is not None else None
            ),
            "unlabelled_by_annotator": (
                int(total_scans - labelled_by_annotator)
                if labelled_by_annotator is not None else None
            ),
            "distributions": distributions,
        }

    def get_scan(self, scan_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT s.*, sess.operator, sess.sample_id, sess.tip_id, sess.material"
                " FROM scans s LEFT JOIN sessions sess ON sess.session_id = s.session_id"
                " WHERE s.scan_id = ?",
                (scan_id,),
            ).fetchone()
            if row is None:
                return None
            scan = dict(row)
            labels = self._conn.execute(
                "SELECT * FROM labels WHERE scan_id = ? ORDER BY updated_ts DESC",
                (scan_id,),
            ).fetchall()
        scan["labels"] = [dict(l) for l in labels]
        # Decode JSON list fields for the UI's convenience.
        for label in scan["labels"]:
            if label.get("artifact_tags"):
                try:
                    label["artifact_tags"] = json.loads(label["artifact_tags"])
                except (ValueError, TypeError):
                    pass
        if scan.get("channels"):
            try:
                scan["channels"] = json.loads(scan["channels"])
            except (ValueError, TypeError):
                pass
        return scan

    # -- writes ------------------------------------------------------------

    def upsert_label(
        self,
        *,
        scan_id: str,
        annotator: str,
        fields: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Insert or update a label row keyed by (scan_id, annotator).

        Only keys in :data:`_WRITABLE_LABEL_FIELDS` are persisted; others
        are silently ignored. ``artifact_tags`` may be a list; we
        JSON-encode it before storing.
        """
        annotator = (annotator or "").strip()
        if not annotator:
            raise ValueError("annotator name is required")
        if not scan_id:
            raise ValueError("scan_id is required")

        clean: Dict[str, Any] = {}
        for key in _WRITABLE_LABEL_FIELDS:
            if key not in fields:
                continue
            value = fields[key]
            if key == "artifact_tags" and isinstance(value, list):
                value = json.dumps(value, ensure_ascii=False)
            if isinstance(value, str):
                value = value.strip() or None
            clean[key] = value

        now = time.time()
        with self._lock:
            # Make sure the scan exists; better error than a FK violation.
            exists = self._conn.execute(
                "SELECT 1 FROM scans WHERE scan_id = ?", (scan_id,)
            ).fetchone()
            if not exists:
                raise KeyError(f"scan_id not found: {scan_id}")

            cols = ["scan_id", "annotator", "created_ts", "updated_ts"]
            vals: List[Any] = [scan_id, annotator, now, now]
            updates = ["updated_ts = excluded.updated_ts"]
            for key, val in clean.items():
                cols.append(key)
                vals.append(val)
                updates.append(f"{key} = excluded.{key}")
            placeholders = ",".join(["?"] * len(cols))
            sql = (
                f"INSERT INTO labels({','.join(cols)}) VALUES ({placeholders})"
                f" ON CONFLICT(scan_id, annotator) DO UPDATE SET "
                + ", ".join(updates)
            )
            self._conn.execute(sql, vals)
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM labels WHERE scan_id = ? AND annotator = ?",
                (scan_id, annotator),
            ).fetchone()
        return dict(row)

    def set_review(
        self,
        *,
        scan_id: str,
        annotator: str,
        reviewer: str,
        status: str,
        comment: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Set review_status / review_comment / reviewer on an existing label."""
        reviewer = (reviewer or "").strip()
        if not reviewer:
            raise ValueError("reviewer name is required")
        if status not in _REVIEW_STATUSES:
            raise ValueError(
                f"status must be one of {_REVIEW_STATUSES}, got {status!r}"
            )
        now = time.time()
        with self._lock:
            cur = self._conn.execute(
                "UPDATE labels SET review_status = ?, review_comment = ?,"
                " reviewer = ?, reviewed_ts = ?"
                " WHERE scan_id = ? AND annotator = ?",
                (status, (comment or None), reviewer, now, scan_id, annotator),
            )
            if cur.rowcount == 0:
                raise KeyError(
                    f"no label found for scan_id={scan_id} annotator={annotator}"
                )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM labels WHERE scan_id = ? AND annotator = ?",
                (scan_id, annotator),
            ).fetchone()
        return dict(row)

    # -- preview lookup ----------------------------------------------------

    def resolve_preview(self, scan_id: str) -> Optional[Path]:
        """Return the absolute path of the PNG preview for a scan, or None."""
        with self._lock:
            row = self._conn.execute(
                "SELECT preview_path FROM scans WHERE scan_id = ?", (scan_id,),
            ).fetchone()
        if not row or not row["preview_path"]:
            return None
        p = Path(row["preview_path"])
        candidates = [p if p.is_absolute() else (self.data_root / p).resolve()]
        if p.is_absolute():
            parts = p.parts
            if "previews" in parts:
                idx = parts.index("previews")
                candidates.append((self.data_root / Path(*parts[idx:])).resolve())
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    def resolve_sxm(self, scan_id: str) -> Optional[Path]:
        """Return the absolute path of the archived/raw .sxm for a scan."""
        with self._lock:
            row = self._conn.execute(
                "SELECT sxm_path FROM scans WHERE scan_id = ?", (scan_id,),
            ).fetchone()
        if not row or not row["sxm_path"]:
            return None
        p = Path(row["sxm_path"])
        candidates = [p if p.is_absolute() else (self.data_root / p).resolve()]
        if p.is_absolute():
            parts = p.parts
            if "raw_sxm" in parts:
                idx = parts.index("raw_sxm")
                candidates.append((self.data_root / Path(*parts[idx:])).resolve())
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None
