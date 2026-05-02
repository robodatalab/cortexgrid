"""Per-experiment notes stream.

Polls experiment-level notes for one experiment and emits per-item
diff events. Run-level notes for runs in the experiment are NOT
included here; the combined view exposed at the WebSocket layer
multiplexes this stream with the per-run notes streams.
"""

from __future__ import annotations

from cortexflow_ui.backend.models.notes import (
    ExperimentNote,
    list_experiment_notes,
)
from cortexflow_ui.backend.streams.config import NOTES_STREAM_POLL_INTERVAL_SEC
from cortexflow_ui.backend.utils.keyed_stream import KeyedCache, Refresher


ExperimentName = str
NoteId = str


def poll_experiment_notes(
    experiment_name: ExperimentName,
) -> dict[NoteId, ExperimentNote]:
    return {n.id: n for n in list_experiment_notes(experiment_name)}


cache: KeyedCache[ExperimentName, NoteId, ExperimentNote] = KeyedCache()

refresher: Refresher[ExperimentName, NoteId, ExperimentNote] = Refresher(
    name="experiment_notes_stream",
    cache=cache,
    poll_fn=poll_experiment_notes,
    poll_interval_sec=NOTES_STREAM_POLL_INTERVAL_SEC,
)
