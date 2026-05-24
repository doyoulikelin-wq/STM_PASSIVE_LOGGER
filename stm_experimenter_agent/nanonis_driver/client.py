"""Read-only Nanonis client wrapper.

The class never exposes any ``*_Set`` or actuator entry point. Any attempt to
call one through this object raises :class:`WriteOperationNotAllowed`.
Internally we wrap :mod:`nanonis_spm` for reads we trust, and fall back to
:mod:`raw_protocol` when the Python wrapper cannot decode a reply.
"""
from __future__ import annotations

import logging
import socket
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Iterable, Optional

from .raw_protocol import RawNanonisProtocol, util_version_get

logger = logging.getLogger(__name__)

# Methods we are willing to call on the underlying nanonis_spm instance.
# Everything else - particularly *_Set, *Pulse*, *Approach*, Motor*, TipShaper* -
# is rejected before it reaches the socket.
_READ_METHOD_ALLOW_LIST = frozenset({
    "Bias_Get",
    "Current_Get",
    "ZCtrl_ZPosGet",
    "ZCtrl_OnOffGet",
    "ZCtrl_SetpntGet",
    "ZCtrl_GainGet",
    "Scan_StatusGet",
    "Scan_FrameSet",          # only if explicitly opted-in; not used in V0
    "Scan_BufferGet",
    "Scan_PropsGet",
    "Signals_NamesGet",
    "Signals_ValGet",
    "Signals_ValsGet",
    "Util_RTFreqGet",
    "Util_SessionPathGet",
    "Util_VersionGet",
})

# These prefixes must never be called in the passive logger.
_FORBIDDEN_PREFIXES = (
    "_Set", "Set_", "Pulse", "Approach", "Motor", "TipShaper", "TipShape",
    "Withdraw", "AutoApproach", "Pattern", "Wave",
)


class WriteOperationNotAllowed(PermissionError):
    """Raised when code attempts a write/actuation through the read-only client."""


def _is_forbidden(method_name: str) -> bool:
    if any(token in method_name for token in _FORBIDDEN_PREFIXES):
        return True
    return False


@dataclass
class NanonisSnapshot:
    ts: float
    bias_V: Optional[float] = None
    current_A: Optional[float] = None
    z_m: Optional[float] = None
    z_controller_on: Optional[bool] = None
    scan_status: Optional[int] = None
    errors: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class NanonisReadOnlyClient:
    """Thread-safe read-only client. Lazy-connects on first call.

    Parameters
    ----------
    host, port:
        TCP Programming Interface endpoint.
    timeout:
        socket timeout in seconds.
    fallback_ports:
        Tried in order if ``port`` refuses connection.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 6501,
        timeout: float = 5.0,
        fallback_ports: Iterable[int] = (),
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.fallback_ports = tuple(fallback_ports)
        self._lock = threading.RLock()
        self._sock: Optional[socket.socket] = None
        self._spm = None  # type: ignore[assignment]
        self._connected_port: Optional[int] = None
        self._version_info_from_connect: Optional[Dict[str, Any]] = None
        self._port_probe_results: list[Dict[str, Any]] = []

    # -- connection ---------------------------------------------------------
    def connect(self) -> int:
        """Connect to ``host``. Returns the port actually used.

        A TCP handshake alone is not enough: some services accept the socket
        but do not answer Nanonis data commands. After connecting we therefore
        require the core read-only commands used by the logger to respond. If
        those probes fail, we close the socket and try the next fallback port.
        """
        with self._lock:
            if self._spm is not None:
                return self._connected_port  # type: ignore[return-value]

            import nanonis_spm  # local import: keep module import light

            last_err: Optional[BaseException] = None
            self._port_probe_results = []
            seen_ports: set[int] = set()
            candidates = []
            for candidate in (self.port, *self.fallback_ports):
                if candidate not in seen_ports:
                    candidates.append(candidate)
                    seen_ports.add(candidate)

            for candidate in candidates:
                sock: Optional[socket.socket] = None
                probe_result: Dict[str, Any] = {
                    "port": candidate,
                    "selected": False,
                }
                try:
                    probe_timeout = min(self.timeout, 2.0)
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    # Keep the first wrapper command short too; wrong ports
                    # must fail fast so fallback reaches the real PI port.
                    sock.settimeout(probe_timeout)
                    sock.connect((self.host, candidate))
                    spm_probe = nanonis_spm.Nanonis(sock)

                    core_probe: Dict[str, str] = {}
                    for method_name in (
                        "Bias_Get",
                        "Current_Get",
                        "Util_SessionPathGet",
                    ):
                        try:
                            getattr(spm_probe, method_name)()
                            core_probe[method_name] = "ok"
                        except Exception as exc:  # noqa: BLE001
                            core_probe[method_name] = (
                                f"{type(exc).__name__}: {exc}"
                            )
                    probe_result["core_probe"] = core_probe
                    if core_probe.get("Bias_Get") != "ok" or core_probe.get("Current_Get") != "ok":
                        raise TimeoutError(
                            "core data probe failed: "
                            f"Bias_Get={core_probe.get('Bias_Get')}; "
                            f"Current_Get={core_probe.get('Current_Get')}"
                        )

                    # Real session timeout once we know the port works.
                    sock.settimeout(self.timeout)
                    self._sock = sock
                    self._spm = spm_probe
                    self._connected_port = candidate
                    probe_result["wrapper_probe"] = "Bias_Get ok"
                    probe_result["selected"] = True
                    self._port_probe_results.append(probe_result)
                    logger.info("Nanonis read-only client connected to %s:%s",
                                self.host, candidate)
                    return candidate
                except (OSError, Exception) as exc:
                    last_err = exc
                    probe_result["error"] = f"{type(exc).__name__}: {exc}"
                    self._port_probe_results.append(probe_result)
                    logger.warning("Connect/probe %s:%s failed: %s",
                                   self.host, candidate, exc)
                    if sock is not None:
                        try:
                            sock.close()
                        except Exception:
                            pass
            raise ConnectionError(
                f"Could not reach a Nanonis Programming Interface on any of "
                f"{(self.port,) + self.fallback_ports}: {last_err}"
            )

    def close(self) -> None:
        with self._lock:
            if self._spm is not None:
                try:
                    self._spm.close()
                except Exception:  # noqa: BLE001 - best-effort close
                    logger.debug("nanonis_spm close() raised", exc_info=True)
                self._spm = None
            self._sock = None
            self._connected_port = None
            self._version_info_from_connect = None

    def port_probe_results(self) -> list[Dict[str, Any]]:
        """Return diagnostics from the most recent fallback-port scan."""
        return list(self._port_probe_results)

    def __enter__(self) -> "NanonisReadOnlyClient":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # -- safe method dispatch ----------------------------------------------
    def _call(self, method_name: str, *args, **kwargs):
        if _is_forbidden(method_name) or method_name not in _READ_METHOD_ALLOW_LIST:
            raise WriteOperationNotAllowed(
                f"Method {method_name!r} is not in the read-only allow-list"
            )
        with self._lock:
            if self._spm is None:
                self.connect()
            method = getattr(self._spm, method_name)
            return method(*args, **kwargs)

    # -- typed read accessors ----------------------------------------------
    @staticmethod
    def _first_scalar(result: Any) -> Any:
        """Pick the first scalar out of a ``nanonis_spm.quickSend`` return.

        Empirically nanonis_spm>=1.0.9 returns a 3-tuple
        ``(error_desc:str, raw_body:bytes, parsed_values:list)`` where
        ``parsed_values`` is what we want (the first element is the
        primary value). Older builds / mocks may instead return just
        ``(value,)``; we tolerate both.
        """
        if isinstance(result, tuple) and len(result) == 3 and isinstance(result[2], list):
            if not result[2]:
                raise ValueError("Empty parsed values from Nanonis")
            return result[2][0]
        if isinstance(result, (tuple, list)):
            if not result:
                raise ValueError("Empty response body from Nanonis")
            return result[0]
        return result

    @staticmethod
    def _parsed_values(result: Any) -> list:
        """Return the full parsed-values list from a quickSend result."""
        if isinstance(result, tuple) and len(result) == 3 and isinstance(result[2], list):
            return result[2]
        if isinstance(result, (tuple, list)):
            return list(result)
        return [result]

    def bias_V(self) -> float:
        return float(self._first_scalar(self._call("Bias_Get")))

    def current_A(self) -> float:
        return float(self._first_scalar(self._call("Current_Get")))

    def z_m(self) -> float:
        return float(self._first_scalar(self._call("ZCtrl_ZPosGet")))

    def z_controller_on(self) -> bool:
        # Nanonis returns uint32 0/1; convert via int() first so that
        # non-empty tuples never coerce to a misleading True.
        return bool(int(self._first_scalar(self._call("ZCtrl_OnOffGet"))))

    def scan_status(self) -> int:
        # Returns 0 (stopped) or 1 (running) on typical Nanonis builds.
        return int(self._first_scalar(self._call("Scan_StatusGet")))

    def session_path(self) -> Optional[str]:
        """Ask Nanonis where it saves files. Returns ``None`` if Nanonis
        does not expose a session path (rare; usually it returns an empty
        string in that case).
        """
        values = self._parsed_values(self._call("Util_SessionPathGet"))
        # Response is [length_int, path_string]; the string is the last
        # non-int element.
        for el in reversed(values):
            if isinstance(el, str):
                return el or None
        return None

    # -- snapshot ----------------------------------------------------------
    def snapshot(self) -> NanonisSnapshot:
        """Best-effort snapshot. Per-field exceptions are recorded, not raised."""
        snap = NanonisSnapshot(ts=time.time())
        for attr, fn in (
            ("bias_V", self.bias_V),
            ("current_A", self.current_A),
            ("z_m", self.z_m),
            ("z_controller_on", self.z_controller_on),
            ("scan_status", self.scan_status),
        ):
            try:
                setattr(snap, attr, fn())
            except WriteOperationNotAllowed:
                raise
            except Exception as exc:  # noqa: BLE001
                snap.errors[attr] = f"{type(exc).__name__}: {exc}"
        return snap

    # -- version / health --------------------------------------------------
    def version_info(self) -> Dict[str, Any]:
        """Best-effort version probe.

        Strategy:
        1. Try the high-level ``nanonis_spm.Util_VersionGet`` over the
           already-open socket. On modern builds this works.
        2. If that raises (the known ``bad char in struct format`` bug or
           similar), fall back to the raw protocol on a *separate* socket.
        3. If both fail, return ``{ok: False, error: ...}``; never raise.
        """
        if self._version_info_from_connect is not None:
            return dict(self._version_info_from_connect)
        try:
            values = self._parsed_values(self._call("Util_VersionGet"))
            # Six version strings, interleaved with the length ints used
            # by the wire format; keep only str entries to be robust.
            strings = [v for v in values if isinstance(v, str)]
            while len(strings) < 6:
                strings.append("")
            return {
                "ok": True,
                "app": strings[0],
                "controller": strings[1],
                "rt_engine": strings[2],
                "fpga": strings[3],
                "signals_format": strings[4],
                "dsp": strings[5],
            }
        except Exception as exc1:  # noqa: BLE001
            logger.debug("spm Util_VersionGet failed, falling back to raw: %s", exc1)
            try:
                port = self._connected_port or self.port
                # Short timeout: many Nanonis installs only accept a single
                # concurrent TCP client, so opening a second socket here may
                # block; we don't want to stall the session start.
                return util_version_get(self.host, port, timeout=min(self.timeout, 2.0))
            except Exception as exc2:  # noqa: BLE001
                return {"ok": False, "error": f"spm: {exc1}; raw: {exc2}"}


@contextmanager
def open_readonly(host: str, port: int, timeout: float = 5.0,
                  fallback_ports: Iterable[int] = ()):
    client = NanonisReadOnlyClient(host, port, timeout, fallback_ports)
    try:
        client.connect()
        yield client
    finally:
        client.close()
