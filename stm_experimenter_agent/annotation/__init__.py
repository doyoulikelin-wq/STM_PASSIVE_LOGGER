"""Offline image annotation subsystem (V1).

Used asynchronously after data has been collected by the passive logger.
The annotation server opens the same ``session.sqlite`` produced by the
logger, reads the ``scans`` table, serves PNG previews, and writes
labels + reviews back into the ``labels`` table.
"""
from __future__ import annotations
