"""Per-experiment combined notes stream.

Streams a single feed for an experiment that interleaves run-level notes
(for all runs in the experiment) with experiment-level meta-notes. Each
item carries a `kind` field and, for run notes, the `run_id` / `run_name`
so the UI can label each note's origin.

Run lookups piggyback on `experiments_stream.runs_cache`, which is kept
fresh by the experiments poll loop, so we don't re-hit MLflow here.
"""

from __future__ import annotations

from cortexflow_ui.backend.models.notes import (
    ExperimentNote,
    RunNote,
    list_experiment_notes,
    list_run_notes,
)
from cortexflow_ui.backend.streams.experiments_stream import runs_for_experiment
from cortexflow_ui.backend.streams.config import NOTES_STREAM_POLL_INTERVAL_SEC
from cortexflow_ui.backend.utils.keyed_stream import KeyedStream


ExperimentName = str
NoteId = str
CombinedNote = RunNote | ExperimentNote


def poll_experiment_notes(
    experiment_name: ExperimentName,
) -> dict[NoteId, CombinedNote]:
    items: dict[NoteId, CombinedNote] = {}
    for run_name in runs_for_experiment(experiment_name):
        for n in list_run_notes(run_name):
            items[n.id] = n
    for n in list_experiment_notes(experiment_name):
        items[n.id] = n
    return items


stream: KeyedStream[ExperimentName, NoteId, CombinedNote] = KeyedStream(
    name="experiment_notes_stream",
    poll_fn=poll_experiment_notes,
    poll_interval_sec=NOTES_STREAM_POLL_INTERVAL_SEC,
)
