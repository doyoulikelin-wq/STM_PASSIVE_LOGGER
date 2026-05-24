"""Tests for the offline annotation subsystem (store + HTTP server)."""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from stm_experimenter_agent.annotation.server import _make_handler, _ReusableServer
from stm_experimenter_agent.annotation.store import AnnotationStore
from stm_experimenter_agent.data_collection.dataset_writer import DatasetWriter


def _seed_dataset(root: Path) -> str:
    """Create a minimal logger output with one session + two scans."""
    dw = DatasetWriter(root)
    sid = "20260520_120000_sampleA_tip01"
    dw.upsert_session(
        session_id=sid,
        start_ts=time.time() - 60,
        end_ts=time.time(),
        operator="alice",
        sample_id="sampleA",
        tip_id="tip01",
        material="Bi2Se3",
    )
    for i, scan_id in enumerate(["scanA_aaaa1111", "scanB_bbbb2222"]):
        dw.insert_scan(
            session_id=sid,
            scan_id=scan_id,
            captured_ts=time.time() - 30 + i,
            sxm_path=f"D:/STM/S2/scan_{i}.sxm",
            preview_path=None,
            metadata={
                "bias_V": 1.5,
                "setpoint": 2e-10,
                "setpoint_unit": "A",
                "pixels": [256, 256],
                "scan_range_m": [5e-8, 5e-8],
                "scan_offset_m": [0.0, 0.0],
                "scan_angle_deg": 0.0,
                "channels": ["Z", "Current"],
            },
        )
    dw.close()
    return sid


def test_annotation_store_upsert_and_list(tmp_path: Path) -> None:
    sid = _seed_dataset(tmp_path)
    store = AnnotationStore(tmp_path)
    try:
        # Before any labels: both scans unlabelled for alice
        scans = store.list_scans(annotator="alice", mode="unlabeled")
        assert len(scans) == 2
        for s in scans:
            assert s["labelled_by_me"] == 0
            assert s["n_labels"] == 0

        # alice labels scanA
        row = store.upsert_label(
            scan_id="scanA_aaaa1111",
            annotator="alice",
            fields={
                "image_quality": "usable",
                "substrate": "HOPG",
                "thin_film": "Bi2Se3",
                "molecule": "PTCDA",
                "tip_state": "good_tip",
                "surface_quality": "flat_terrace",
                "artifact_tags": ["drift", "stripe_noise"],
                "research_value_score": 0.8,
                "annotator_notes": "looks promising",
            },
        )
        assert row["annotator"] == "alice"
        assert row["image_quality"] == "usable"
        assert row["substrate"] == "HOPG"
        assert row["thin_film"] == "Bi2Se3"
        assert row["molecule"] == "PTCDA"
        # artifact_tags persisted as JSON
        assert json.loads(row["artifact_tags"]) == ["drift", "stripe_noise"]
        assert row["created_ts"] == row["updated_ts"]

        # Now scanA is labelled for alice but not bob
        alice_unlabeled = store.list_scans(annotator="alice", mode="unlabeled")
        assert {s["scan_id"] for s in alice_unlabeled} == {"scanB_bbbb2222"}
        bob_unlabeled = store.list_scans(annotator="bob", mode="unlabeled")
        assert {s["scan_id"] for s in bob_unlabeled} == {"scanA_aaaa1111", "scanB_bbbb2222"}

        # bob also labels scanA -> two labels coexist
        store.upsert_label(
            scan_id="scanA_aaaa1111",
            annotator="bob",
            fields={"image_quality": "questionable", "annotator_notes": "I'm not sure"},
        )
        scan = store.get_scan("scanA_aaaa1111")
        assert len(scan["labels"]) == 2
        annotators = {l["annotator"] for l in scan["labels"]}
        assert annotators == {"alice", "bob"}

        # Re-label by alice overwrites and bumps updated_ts
        time.sleep(0.01)
        row2 = store.upsert_label(
            scan_id="scanA_aaaa1111",
            annotator="alice",
            fields={"image_quality": "excellent"},
        )
        assert row2["image_quality"] == "excellent"
        assert row2["updated_ts"] > row2["created_ts"]

        # Stats
        stats_alice = store.stats("alice")
        assert stats_alice["total_scans"] == 2
        assert stats_alice["labelled"] == 1
        assert stats_alice["unlabelled"] == 1

        overview = store.session_overview(session_id=sid, annotator="alice")
        assert overview["total_scans"] == 2
        assert overview["labelled_scans"] == 1
        assert overview["labelled_by_annotator"] == 1
        assert overview["distributions"]["substrate"]["HOPG"] == 1
        assert overview["distributions"]["thin_film"]["Bi2Se3"] == 1
        assert overview["distributions"]["molecule"]["PTCDA"] == 1
        assert overview["distributions"]["artifact_tags"]["drift"] == 1
    finally:
        store.close()


def test_annotation_store_review_flow(tmp_path: Path) -> None:
    _seed_dataset(tmp_path)
    store = AnnotationStore(tmp_path)
    try:
        store.upsert_label(
            scan_id="scanA_aaaa1111",
            annotator="alice",
            fields={"image_quality": "usable"},
        )
        # reviewer disputes alice's label
        row = store.set_review(
            scan_id="scanA_aaaa1111",
            annotator="alice",
            reviewer="charlie",
            status="dispute",
            comment="looks closer to questionable to me",
        )
        assert row["review_status"] == "dispute"
        assert row["reviewer"] == "charlie"
        assert row["review_comment"].startswith("looks")
        assert row["reviewed_ts"] is not None

        # invalid status
        with pytest.raises(ValueError):
            store.set_review(
                scan_id="scanA_aaaa1111", annotator="alice",
                reviewer="charlie", status="bogus",
            )
        # missing reviewer
        with pytest.raises(ValueError):
            store.set_review(
                scan_id="scanA_aaaa1111", annotator="alice",
                reviewer="", status="accept",
            )
        # unknown (scan, annotator) pair
        with pytest.raises(KeyError):
            store.set_review(
                scan_id="scanA_aaaa1111", annotator="nobody",
                reviewer="charlie", status="accept",
            )
    finally:
        store.close()


def test_annotation_store_requires_annotator(tmp_path: Path) -> None:
    _seed_dataset(tmp_path)
    store = AnnotationStore(tmp_path)
    try:
        with pytest.raises(ValueError):
            store.upsert_label(scan_id="scanA_aaaa1111", annotator="  ",
                               fields={"image_quality": "usable"})
        with pytest.raises(KeyError):
            store.upsert_label(scan_id="does_not_exist", annotator="alice",
                               fields={"image_quality": "usable"})
    finally:
        store.close()


# -- HTTP integration test --------------------------------------------------

def _start_test_server(tmp_path: Path) -> tuple[_ReusableServer, AnnotationStore, str]:
    store = AnnotationStore(tmp_path)
    schema = {
        "version": 1,
        "fields": {
            "image_quality": {"type": "ordinal",
                              "options": ["excellent", "usable", "questionable", "unusable"]},
            "artifact_tags": {"type": "multi", "options": ["drift", "stripe_noise"]},
        },
    }
    handler = _make_handler(store, schema)
    server = _ReusableServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, store, f"http://127.0.0.1:{port}"


def _http_json(method: str, url: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def test_http_endpoints_round_trip(tmp_path: Path) -> None:
    _seed_dataset(tmp_path)
    server, store, base = _start_test_server(tmp_path)
    try:
        # Schema endpoint
        status, body = _http_json("GET", f"{base}/api/schema")
        assert status == 200 and "fields" in body

        # List scans for alice -> 2 unlabelled
        status, body = _http_json(
            "GET", f"{base}/api/scans?annotator=alice&mode=unlabeled")
        assert status == 200
        assert len(body) == 2

        # POST a label
        status, body = _http_json("POST", f"{base}/api/label", {
            "scan_id": "scanA_aaaa1111",
            "annotator": "alice",
            "fields": {"image_quality": "usable",
                       "substrate": "Au(111)",
                       "thin_film": "hBN",
                       "molecule": "custom_molecule",
                       "artifact_tags": ["drift"],
                       "annotator_notes": "ok"},
        })
        assert status == 200
        assert body["annotator"] == "alice"
        assert body["substrate"] == "Au(111)"

        # Now scanA shows up in 'labeled' for alice
        status, body = _http_json(
            "GET", f"{base}/api/scans?annotator=alice&mode=labeled")
        assert status == 200
        assert {s["scan_id"] for s in body} == {"scanA_aaaa1111"}

        # Session overview includes progress + label distributions
        status, body = _http_json(
            "GET", f"{base}/api/session-overview?session_id=20260520_120000_sampleA_tip01&annotator=alice")
        assert status == 200
        assert body["total_scans"] == 2
        assert body["labelled_by_annotator"] == 1
        assert body["distributions"]["substrate"]["Au(111)"] == 1

        # Reviewer flow
        status, body = _http_json("POST", f"{base}/api/review", {
            "scan_id": "scanA_aaaa1111", "annotator": "alice",
            "reviewer": "charlie", "status": "accept",
            "comment": "agreed",
        })
        assert status == 200
        assert body["review_status"] == "accept"

        # Bad review status -> 400
        status, body = _http_json("POST", f"{base}/api/review", {
            "scan_id": "scanA_aaaa1111", "annotator": "alice",
            "reviewer": "charlie", "status": "junk",
        })
        assert status == 400

        # Missing preview file -> 404 (returns text/plain, not JSON)
        req = urllib.request.Request(f"{base}/preview/scanA_aaaa1111.png")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                status = resp.status
        except urllib.error.HTTPError as e:
            status = e.code
        assert status == 404
    finally:
        server.shutdown()
        server.server_close()
        store.close()
