"""Signal poller thread.

Polls the read-only Nanonis client at a fixed frequency and pushes
snapshots into the :class:`DatasetWriter`. Designed to never block the
main thread or corrupt the dataset on crash.

Engineering notes
-----------------
* The poll loop sleeps using monotonic clock to avoid drift.
* Per-iteration exceptions are logged but do not stop the thread; the
  underlying client will auto-reconnect on its next call.
* Buffering: small batches reduce SQLite commit pressure but we still
  flush at least once per second so the on-disk log is near real time.
* Stopping is cooperative via :meth:`stop` + thread join with timeout.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import List

from ..nanonis_driver.client import NanonisReadOnlyClient
from .dataset_writer import DatasetWriter

logger = logging.getLogger(__name__)


class SignalLogger:
    def __init__(self, *, client: NanonisReadOnlyClient, writer: DatasetWriter,
                 session_id: str, poll_hz: float = 1.0,
                 batch_size: int = 50, flush_interval_s: float = 1.0) -> None:
        if poll_hz <= 0:
            raise ValueError("poll_hz must be > 0")
        self.client = client
        self.writer = writer
        self.session_id = session_id
        self.poll_period = 1.0 / poll_hz
        self.batch_size = max(1, batch_size)
        self.flush_interval_s = max(0.1, flush_interval_s)

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="stm-signal-logger", daemon=True
        )
        self._thread.start()
        logger.info("SignalLogger started: %.2f Hz", 1.0 / self.poll_period)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
        logger.info("SignalLogger stopped")

    def _run(self) -> None:
        buffer: List[dict] = []
        last_flush = time.monotonic()
        next_tick = time.monotonic()
        consecutive_failures = 0

        while not self._stop.is_set():
            try:
                snap = self.client.snapshot()
                buffer.append(snap.to_dict())
                consecutive_failures = 0
            except Exception as exc:  # noqa: BLE001 - driver/network/etc.
                consecutive_failures += 1
                logger.warning("snapshot failed (%d in a row): %s",
                               consecutive_failures, exc)
                # On repeated failures, drop the connection so the next poll
                # forces a reconnect.
                if consecutive_failures >= 5:
                    try:
                        self.client.close()
                    except Exception:  # noqa: BLE001
                        logger.debug("client close failed", exc_info=True)
                    consecutive_failures = 0

            now = time.monotonic()
            should_flush = (
                len(buffer) >= self.batch_size
                or (buffer and (now - last_flush) >= self.flush_interval_s)
            )
            if should_flush:
                try:
                    self.writer.insert_signals(self.session_id, buffer)
                except Exception:  # noqa: BLE001
                    logger.exception("Failed to flush signal batch (%d rows)", len(buffer))
                buffer.clear()
                last_flush = now

            # Drift-free pacing
            next_tick += self.poll_period
            sleep_for = next_tick - time.monotonic()
            if sleep_for > 0:
                self._stop.wait(timeout=sleep_for)
            else:
                # We are behind schedule; resync to "now" to avoid catch-up storm.
                next_tick = time.monotonic()

        # Final flush
        if buffer:
            try:
                self.writer.insert_signals(self.session_id, buffer)
            except Exception:  # noqa: BLE001
                logger.exception("Final signal flush failed")
