"""Raw Nanonis TCP Programming Interface helpers.

Reason: ``nanonis_spm.Util_VersionGet`` on Mimea V5e currently raises
``bad char in struct format`` for some firmware builds. We need a minimal
raw-protocol implementation so health-check and version probe work even
when the high-level wrapper cannot parse the reply.

Protocol summary (per SPECS Nanonis TCP doc):

* Each message starts with a fixed 40-byte header:
  - 32 bytes: ASCII command name, null-padded.
  - 4 bytes:  big-endian int32 body length (excluding header).
  - 2 bytes:  big-endian int16 "send response" flag (0/1).
  - 2 bytes:  reserved (zeros).
* Then ``body_length`` bytes of body. Numeric types are big-endian.
* String fields in bodies are encoded as ``int32 length`` + ``utf-8 bytes``.
* Replies use the same header followed by body + 8-byte error trailer
  (``int32 status`` + ``int32 description length``) + optional description.

Only operations strictly needed by the passive logger are implemented here.
This module never sends any ``*Set`` or actuator commands.
"""
from __future__ import annotations

import socket
import struct
from dataclasses import dataclass
from typing import Optional, Tuple

_HEADER_FMT = ">32sIHH"   # name, body_len, send_response, reserved
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)

# Defensive allow-list: only read/probe commands may pass through this module.
_ALLOWED_RAW_COMMANDS = frozenset({
    "Util.VersionGet",
    "Util.SessionPathGet",
    "Util.RTFreqGet",
    "Util.AcqPeriodGet",
    "Bias.Get",
    "Current.Get",
    "ZCtrl.ZPosGet",
    "ZCtrl.OnOffGet",
    "Scan.StatusGet",
    "Scan.FrameDataGrab",        # read scan buffer, no actuation
    "Scan.BufferGet",
    "Scan.PropsGet",
})


class RawNanonisError(RuntimeError):
    """Raised when the Nanonis server returns a non-zero error status."""

    def __init__(self, command: str, status: int, description: str) -> None:
        super().__init__(f"{command} failed: status={status} desc={description!r}")
        self.command = command
        self.status = status
        self.description = description


@dataclass(frozen=True)
class _Reply:
    body: bytes
    status: int
    description: str


def _encode_command_name(name: str) -> bytes:
    if len(name) > 32:
        raise ValueError(f"Command name too long: {name!r}")
    return name.encode("ascii").ljust(32, b"\x00")


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    chunks = []
    remaining = n
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ConnectionError(
                f"Nanonis closed connection while reading {n - remaining}/{n} bytes"
            )
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


class RawNanonisProtocol:
    """Thin context-managed TCP client for read-only probes.

    Intentionally not reused for the high-level driver: we only want this
    when ``nanonis_spm`` cannot decode a reply or before we trust the
    Python wrapper to be initialised.
    """

    def __init__(self, host: str, port: int, timeout: float = 5.0) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: Optional[socket.socket] = None

    # -- context management --------------------------------------------------
    def __enter__(self) -> "RawNanonisProtocol":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def connect(self) -> None:
        if self._sock is not None:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect((self.host, self.port))
        self._sock = sock

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    # -- core send/recv ------------------------------------------------------
    def _send_command(self, command: str, body: bytes = b"") -> _Reply:
        if command not in _ALLOWED_RAW_COMMANDS:
            # Defence in depth: refuse anything not on the read-only allow-list.
            raise PermissionError(
                f"Raw command {command!r} is not in the read-only allow-list"
            )
        if self._sock is None:
            raise RuntimeError("RawNanonisProtocol used before connect()")

        header = struct.pack(
            _HEADER_FMT,
            _encode_command_name(command),
            len(body),
            1,  # always ask for a response
            0,
        )
        self._sock.sendall(header + body)

        reply_header = _recv_exact(self._sock, _HEADER_SIZE)
        _, body_len, _, _ = struct.unpack(_HEADER_FMT, reply_header)
        payload = _recv_exact(self._sock, body_len) if body_len else b""

        # Error trailer is the last 8 bytes + optional description.
        if len(payload) < 8:
            return _Reply(body=payload, status=0, description="")
        status, desc_len = struct.unpack(">II", payload[-8:])
        if desc_len:
            # Description sits immediately before the 8-byte error trailer.
            desc_start = len(payload) - 8 - desc_len
            if desc_start < 0:
                description = ""
            else:
                description = payload[desc_start:desc_start + desc_len].decode(
                    "utf-8", errors="replace"
                )
            body_only = payload[: desc_start if desc_start >= 0 else 0]
        else:
            description = ""
            body_only = payload[:-8]

        if status != 0:
            raise RawNanonisError(command, status, description)
        return _Reply(body=body_only, status=status, description=description)

    # -- specific read-only commands ----------------------------------------
    def util_version_get(self) -> Tuple[str, str, str, str, str, str]:
        """Return six version strings: (app, controller, rt, fpga, signals, dsp)."""
        reply = self._send_command("Util.VersionGet")
        return _decode_six_strings(reply.body)


def _decode_six_strings(body: bytes) -> Tuple[str, str, str, str, str, str]:
    """Decode six length-prefixed strings from a Nanonis reply body."""
    out = []
    offset = 0
    for _ in range(6):
        if offset + 4 > len(body):
            out.append("")
            continue
        (length,) = struct.unpack(">I", body[offset:offset + 4])
        offset += 4
        if length < 0 or offset + length > len(body):
            out.append("")
            break
        out.append(body[offset:offset + length].decode("utf-8", errors="replace"))
        offset += length
    while len(out) < 6:
        out.append("")
    return tuple(out)  # type: ignore[return-value]


def util_version_get(host: str, port: int, timeout: float = 5.0) -> dict:
    """Convenience health-check that returns a dict, never raising on parse."""
    try:
        with RawNanonisProtocol(host, port, timeout=timeout) as proto:
            app, controller, rt, fpga, signals, dsp = proto.util_version_get()
        return {
            "ok": True,
            "app": app,
            "controller": controller,
            "rt_engine": rt,
            "fpga": fpga,
            "signals_format": signals,
            "dsp": dsp,
        }
    except (OSError, RawNanonisError, ValueError) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
