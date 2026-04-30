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
from cortexflow_ui.backend.utils.keyed_stream import KeyedDiffStream


def _list_combined_notes(experiment_name: str) -> list[ExperimentNote | RunNote]:
    items: list[ExperimentNote | RunNote] = []
    for run_id in runs_for_experiment(experiment_name):
        items.extend(list_run_notes(run_id))
    items.extend(list_experiment_notes(experiment_name))
    return items


stream = KeyedDiffStream(
    name="experiment_notes_stream",
    list_fn=_list_combined_notes,
    id_fn=lambda item: item.id,
    poll_interval_sec=NOTES_STREAM_POLL_INTERVAL_SEC,
)
