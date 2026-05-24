"""Passive logger CLI.

Examples
--------
Start a session::

    python -m stm_experimenter_agent.cli start ^
        --sample sampleA --tip tip03 --operator linye ^
        --watch-dir "C:\\Nanonis Data" --poll-hz 1.0

Health-check Nanonis without starting any logger::

    python -m stm_experimenter_agent.cli probe --host 127.0.0.1 --port 6501
"""
from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading
from pathlib import Path
from typing import Optional

from .config import load_yaml
from .data_collection.session_logger import SessionLogger, SessionMeta
from .nanonis_driver.client import NanonisReadOnlyClient
from .nanonis_driver.raw_protocol import util_version_get

logger = logging.getLogger("stm_experimenter_agent")


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


def _load_runtime_defaults() -> dict:
    try:
        ports = load_yaml("nanonis_ports")
    except FileNotFoundError:
        ports = {}
    try:
        runtime = load_yaml("logger")
    except FileNotFoundError:
        runtime = {}
    return {"ports": ports, "runtime": runtime}


def _build_client(args, ports_cfg: dict) -> NanonisReadOnlyClient:
    host = args.host or ports_cfg.get("host", "127.0.0.1")
    port = args.port or int(ports_cfg.get("primary_port", 6501))
    fallback = ports_cfg.get("fallback_ports", []) or []
    timeout = float(ports_cfg.get("connect_timeout_s", 5.0))
    return NanonisReadOnlyClient(host=host, port=port, timeout=timeout,
                                 fallback_ports=tuple(fallback))


def cmd_probe(args) -> int:
    """Connect to Nanonis, dump version + which port answered + session path."""
    defaults = _load_runtime_defaults()
    client = _build_client(args, defaults["ports"])
    info: dict = {}
    try:
        client.connect()
        info["connected_port"] = client._connected_port
        info["port_probe_results"] = client.port_probe_results()
        info["version"] = client.version_info()
        try:
            info["session_path"] = client.session_path()
        except Exception as exc:  # noqa: BLE001
            info["session_path_error"] = f"{type(exc).__name__}: {exc}"
        try:
            info["sample_snapshot"] = client.snapshot().to_dict()
        except Exception as exc:  # noqa: BLE001
            info["snapshot_error"] = f"{type(exc).__name__}: {exc}"
        ok = True
    except Exception as exc:  # noqa: BLE001
        info["ok"] = False
        info["error"] = f"{type(exc).__name__}: {exc}"
        ok = False
    finally:
        try:
            client.close()
        except Exception:
            pass
    if "ok" not in info:
        info["ok"] = ok
    print(json.dumps(info, ensure_ascii=False, indent=2, default=str))
    return 0 if info.get("ok") else 1


def cmd_start(args) -> int:
    defaults = _load_runtime_defaults()
    runtime = defaults["runtime"]
    _setup_logging(args.log_level or runtime.get("log_level", "INFO"))

    data_root = Path(args.data_root or runtime.get("data_root", "data")).resolve()
    poll_hz = args.poll_hz if args.poll_hz is not None else float(runtime.get("poll_hz", 1.0))

    client = _build_client(args, defaults["ports"])
    meta = SessionMeta(
        operator=args.operator,
        sample_id=args.sample,
        tip_id=args.tip,
        material=args.material,
        notes=args.notes,
    )

    # Connect once up front: lets us (a) fail fast with a useful message
    # if Nanonis isn't reachable, (b) auto-discover the .sxm save directory
    # from Nanonis itself when the user didn't pass --watch-dir.
    watch_dir: Optional[Path] = (
        Path(args.watch_dir).resolve() if args.watch_dir else None
    )
    try:
        client.connect()
        print(f"[stm-logger] connected to Nanonis on "
              f"{client.host}:{client._connected_port}")
        if watch_dir is None:
            try:
                discovered = client.session_path()
            except Exception as exc:  # noqa: BLE001
                discovered = None
                logger.warning("Could not auto-discover watch_dir: %s", exc)
            if discovered:
                watch_dir = Path(discovered).resolve()
                print(f"[stm-logger] auto-discovered watch dir from Nanonis: "
                      f"{watch_dir}")
            else:
                print("[stm-logger] WARNING: --watch-dir not given and Nanonis"
                      " did not report a session path; scan capture disabled."
                      " Set Nanonis -> File/Session save path or pass"
                      " --watch-dir explicitly.")
    except Exception as exc:  # noqa: BLE001
        print(f"[stm-logger] WARNING: could not pre-connect to Nanonis: {exc}")
        print("[stm-logger] session will still start; signals will retry until"
              " Nanonis becomes reachable.")

    session = SessionLogger(
        data_root=data_root,
        watch_dir=watch_dir,
        client=client,
        meta=meta,
        poll_hz=poll_hz,
    )

    stop_event = threading.Event()

    def _handle_signal(signum, _frame):
        logger.info("Received signal %s, shutting down", signum)
        stop_event.set()

    # SIGINT works on Windows; SIGTERM is registered for completeness on POSIX.
    signal.signal(signal.SIGINT, _handle_signal)
    if hasattr(signal, "SIGTERM"):
        try:
            signal.signal(signal.SIGTERM, _handle_signal)
        except (ValueError, OSError):
            pass

    session.start()
    print(f"[stm-logger] session started: {session.session_id}")
    print(f"[stm-logger] data root: {data_root}")
    if watch_dir:
        print(f"[stm-logger] watching: {watch_dir}")
    print("[stm-logger] press Ctrl+C to stop.")

    try:
        while not stop_event.is_set():
            stop_event.wait(timeout=1.0)
    finally:
        session.stop()
        print(f"[stm-logger] session stopped: {session.session_id}")
    return 0


def cmd_annotate_serve(args) -> int:
    """Launch the offline annotation web UI against an existing data_root."""
    from .annotation.server import serve as annotation_serve

    defaults = _load_runtime_defaults()
    runtime = defaults["runtime"]
    data_root = Path(args.data_root or runtime.get("data_root", "data")).resolve()
    annotation_serve(
        data_root,
        host=args.host or "127.0.0.1",
        port=int(args.port or 8765),
        open_browser=not args.no_browser,
    )
    return 0


def cmd_annotate_stats(args) -> int:
    """Print labelling progress for a data_root (no server)."""
    from .annotation.store import AnnotationStore

    defaults = _load_runtime_defaults()
    runtime = defaults["runtime"]
    data_root = Path(args.data_root or runtime.get("data_root", "data")).resolve()
    store = AnnotationStore(data_root)
    try:
        out = {
            "data_root": str(data_root),
            "global": store.stats(),
        }
        if args.annotator:
            out["annotator"] = args.annotator
            out["per_annotator"] = store.stats(args.annotator)
    finally:
        store.close()
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="stm-logger",
                                description="STM Experimenter Agent passive logger")
    p.add_argument("--log-level", default=None, help="DEBUG/INFO/WARNING/ERROR")
    sub = p.add_subparsers(dest="cmd", required=True)

    pp = sub.add_parser("probe", help="Health-check Nanonis TCP and print version info")
    pp.add_argument("--host", default=None)
    pp.add_argument("--port", type=int, default=None)
    pp.set_defaults(func=cmd_probe)

    ps = sub.add_parser("start", help="Start a passive logging session")
    ps.add_argument("--host", default=None)
    ps.add_argument("--port", type=int, default=None)
    ps.add_argument("--data-root", default=None)
    ps.add_argument("--watch-dir", default=None,
                    help="Nanonis save directory to watch for new .sxm files")
    ps.add_argument("--poll-hz", type=float, default=None)
    ps.add_argument("--operator", default=None)
    ps.add_argument("--sample", default=None)
    ps.add_argument("--tip", default=None)
    ps.add_argument("--material", default=None)
    ps.add_argument("--notes", default=None)
    ps.set_defaults(func=cmd_start)

    pa = sub.add_parser("annotate-serve",
                        help="Launch the offline annotation web UI")
    pa.add_argument("--data-root", default=None,
                    help="Data root produced by `stm-logger start`")
    pa.add_argument("--host", default="127.0.0.1")
    pa.add_argument("--port", type=int, default=8765)
    pa.add_argument("--no-browser", action="store_true",
                    help="Do not auto-open the browser")
    pa.set_defaults(func=cmd_annotate_serve)

    pst = sub.add_parser("annotate-stats",
                         help="Print labelling progress for a data_root")
    pst.add_argument("--data-root", default=None)
    pst.add_argument("--annotator", default=None,
                     help="If given, also show per-annotator counts")
    pst.set_defaults(func=cmd_annotate_stats)
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if not getattr(args, "func", None):
        return 1
    if args.cmd != "start":
        _setup_logging(args.log_level or "WARNING")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
