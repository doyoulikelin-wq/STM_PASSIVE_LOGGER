"""Stdlib HTTP server for the annotation UI.

We deliberately avoid FastAPI / Flask so the logger keeps zero extra
runtime deps. The single static frontend lives next to this file as
``index.html``. JSON endpoints under ``/api/`` do the real work.
"""
from __future__ import annotations

import cgi
import json
import logging
import socketserver
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional

from ..config import load_yaml
from ..data_collection.sxm_importer import import_sxm_folder, import_sxm_upload
from .store import AnnotationStore

logger = logging.getLogger(__name__)

_INDEX_HTML = Path(__file__).resolve().parent / "index.html"


def _make_handler(store: AnnotationStore, schema: Dict[str, Any]):

    class Handler(BaseHTTPRequestHandler):

        # silence default noisy access log; route through logger instead
        def log_message(self, fmt, *args):  # noqa: A003
            logger.debug("%s - - %s", self.address_string(), fmt % args)

        # -- helpers ---------------------------------------------------

        def _send_json(self, status: int, body: Any) -> None:
            payload = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> Dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            try:
                return json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                raise ValueError(f"invalid JSON body: {exc}") from exc

        def _session_meta_from_mapping(self, body: Dict[str, Any]) -> Dict[str, Any]:
            meta = body.get("session_meta") or body
            return {
                "operator": meta.get("operator") or None,
                "sample_id": meta.get("sample_id") or None,
                "tip_id": meta.get("tip_id") or None,
                "material": meta.get("material") or None,
                "notes": meta.get("notes") or None,
            }

        def _form_value(self, form: cgi.FieldStorage, name: str) -> Optional[str]:
            item = form[name] if name in form else None
            if item is None or item.filename:
                return None
            value = item.value
            return value.strip() if isinstance(value, str) and value.strip() else None

        def _read_multipart(self) -> cgi.FieldStorage:
            env = {
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                "CONTENT_LENGTH": self.headers.get("Content-Length", "0"),
            }
            return cgi.FieldStorage(
                fp=self.rfile,
                headers=self.headers,
                environ=env,
                keep_blank_values=True,
            )

        def _parse(self) -> tuple[str, Dict[str, str]]:
            parsed = urllib.parse.urlparse(self.path)
            query = {k: v[0] for k, v in
                     urllib.parse.parse_qs(parsed.query).items()}
            return parsed.path, query

        # -- routing ---------------------------------------------------

        def do_GET(self):  # noqa: N802
            path, query = self._parse()
            try:
                if path in ("/", "/index.html"):
                    body = _INDEX_HTML.read_bytes()
                    return self._send_bytes(200, body, "text/html; charset=utf-8")
                if path == "/api/schema":
                    return self._send_json(200, schema)
                if path == "/api/sessions":
                    return self._send_json(200, store.list_sessions())
                if path == "/api/stats":
                    return self._send_json(200, store.stats(query.get("annotator")))
                if path == "/api/session-overview":
                    return self._send_json(200, store.session_overview(
                        session_id=query.get("session_id") or None,
                        annotator=query.get("annotator") or None,
                    ))
                if path == "/api/scans":
                    return self._send_json(200, store.list_scans(
                        annotator=query.get("annotator"),
                        mode=query.get("mode", "unlabeled"),
                        session_id=query.get("session_id") or None,
                        limit=int(query.get("limit", "200")),
                    ))
                if path.startswith("/api/scan/"):
                    scan_id = urllib.parse.unquote(path[len("/api/scan/"):])
                    scan = store.get_scan(scan_id)
                    if scan is None:
                        return self._send_json(404, {"error": "scan not found"})
                    return self._send_json(200, scan)
                if path.startswith("/preview/") and path.endswith(".png"):
                    scan_id = urllib.parse.unquote(path[len("/preview/"):-len(".png")])
                    png_path = store.resolve_preview(scan_id)
                    if png_path is None:
                        return self._send_bytes(404, b"no preview", "text/plain")
                    return self._send_bytes(200, png_path.read_bytes(), "image/png")
                return self._send_json(404, {"error": "not found", "path": path})
            except Exception as exc:  # noqa: BLE001
                logger.exception("GET %s failed", path)
                return self._send_json(500, {"error": f"{type(exc).__name__}: {exc}"})

        def do_POST(self):  # noqa: N802
            path, _query = self._parse()
            try:
                if path == "/api/import-upload":
                    form = self._read_multipart()
                    file_item = form["file"] if "file" in form else None
                    if file_item is None or not file_item.filename:
                        raise ValueError("file is required")
                    session_id = self._form_value(form, "session_id")
                    if not session_id:
                        raise ValueError("session_id is required")
                    meta = {
                        "operator": self._form_value(form, "operator"),
                        "sample_id": self._form_value(form, "sample_id"),
                        "tip_id": self._form_value(form, "tip_id"),
                        "material": self._form_value(form, "material"),
                        "notes": self._form_value(form, "notes"),
                    }
                    result = import_sxm_upload(
                        store.data_root,
                        file_item.file,
                        filename=file_item.filename,
                        session_id=session_id,
                        session_meta=meta,
                        source_relative_path=self._form_value(form, "relative_path"),
                    )
                    return self._send_json(200, {"imported": 1, "scan": result})

                body = self._read_json()
                if path == "/api/import-path":
                    import_path = (body.get("path") or "").strip()
                    if not import_path:
                        raise ValueError("path is required")
                    session_id = (body.get("session_id") or "").strip() or None
                    result = import_sxm_folder(
                        store.data_root,
                        import_path,
                        session_id=session_id,
                        session_meta=self._session_meta_from_mapping(body),
                        recursive=bool(body.get("recursive")),
                    )
                    return self._send_json(200, result)
                if path == "/api/label":
                    row = store.upsert_label(
                        scan_id=body.get("scan_id", ""),
                        annotator=body.get("annotator", ""),
                        fields=body.get("fields", {}),
                    )
                    return self._send_json(200, row)
                if path == "/api/review":
                    row = store.set_review(
                        scan_id=body.get("scan_id", ""),
                        annotator=body.get("annotator", ""),
                        reviewer=body.get("reviewer", ""),
                        status=body.get("status", ""),
                        comment=body.get("comment"),
                    )
                    return self._send_json(200, row)
                return self._send_json(404, {"error": "not found", "path": path})
            except (ValueError, KeyError) as exc:
                return self._send_json(400, {"error": str(exc)})
            except Exception as exc:  # noqa: BLE001
                logger.exception("POST %s failed", path)
                return self._send_json(500, {"error": f"{type(exc).__name__}: {exc}"})

    return Handler


class _ReusableServer(ThreadingHTTPServer):
    allow_reuse_address = True


def serve(
    data_root: Path | str,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    """Run the annotation server until Ctrl+C."""
    store = AnnotationStore(data_root)
    try:
        schema = load_yaml("label_schema")
    except FileNotFoundError:
        schema = {"version": 0, "fields": {}}

    handler = _make_handler(store, schema)
    server = _ReusableServer((host, port), handler)
    url = f"http://{host}:{port}/"
    print(f"[stm-annotate] serving on {url}")
    print(f"[stm-annotate] data root: {store.data_root}")
    print(f"[stm-annotate] press Ctrl+C to stop")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[stm-annotate] shutting down")
    finally:
        server.server_close()
        store.close()
