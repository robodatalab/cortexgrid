"""Per-run notes stream.

Polls run_notes for one run every NOTES_STREAM_POLL_INTERVAL_SEC seconds
while at least one WebSocket subscriber is watching that run. Emits
per-item diff events (added/updated/removed) via KeyedDiffStream.
"""
from __future__ import annotations

from cortexflow_ui.backend.config import NOTES_STREAM_POLL_INTERVAL_SEC
from cortexflow_ui.backend.keyed_stream import KeyedDiffStream
from cortexflow_ui.backend.notes import list_run_notes


stream = KeyedDiffStream(
    name="run_notes_stream",
    list_fn=list_run_notes,
    id_fn=lambda note: note["id"],
    poll_interval_sec=NOTES_STREAM_POLL_INTERVAL_SEC,
)
