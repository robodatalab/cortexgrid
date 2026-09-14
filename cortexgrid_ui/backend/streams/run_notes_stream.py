"""Per-run notes stream.

Polls run_notes for one run every NOTES_STREAM_POLL_INTERVAL_SEC seconds
while at least one WebSocket subscriber is watching that run. Emits
per-item diff events (added/updated/removed) via Refresher.
"""

from __future__ import annotations

from cortexgrid_ui.backend.models.notes import RunNote, list_run_notes
from cortexgrid_ui.backend.streams.config import NOTES_STREAM_POLL_INTERVAL_SEC
from cortexgrid_ui.backend.utils.keyed_stream import KeyedCache, Refresher

RunName = str
RunNoteId = str


def poll_run_notes(run_name: RunName) -> dict[RunNoteId, RunNote]:
    return {n.id: n for n in list_run_notes(run_name)}


cache: KeyedCache[RunName, RunNoteId, RunNote] = KeyedCache()

refresher: Refresher[RunName, RunNoteId, RunNote] = Refresher(
    name="run_notes_stream",
    cache=cache,
    poll_fn=poll_run_notes,
    poll_interval_sec=NOTES_STREAM_POLL_INTERVAL_SEC,
)
