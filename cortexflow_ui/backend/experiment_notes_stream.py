"""Per-experiment combined notes stream.

Streams a single feed for an experiment that interleaves run-level notes
(for all runs in the experiment) with experiment-level meta-notes. Each
item carries a `kind` field and, for run notes, the `run_id` / `run_name`
so the UI can label each note's origin.

Run lookups piggyback on `experiments_stream.runs_cache`, which is kept
fresh by the experiments poll loop, so we don't re-hit MLflow here.
"""
from __future__ import annotations

from cortexflow_ui.backend import experiments_stream
from cortexflow_ui.backend.config import NOTES_STREAM_POLL_INTERVAL_SEC
from cortexflow_ui.backend.keyed_stream import KeyedDiffStream
from cortexflow_ui.backend.notes import list_experiment_notes, list_run_notes


def _runs_for_experiment(experiment_name: str) -> list[tuple[str, str]]:
    return [
        (r["run_id"], r["run_name"])
        for r in experiments_stream.runs_cache.values()
        if r["experiment_name"] == experiment_name
    ]


def _to_item(kind: str, run_id: str | None, run_name: str | None, note: dict) -> dict:
    return {
        "id": note["id"],
        "kind": kind,
        "run_id": run_id,
        "run_name": run_name,
        "body": note["body"],
        "created_at": note["created_at"],
        "updated_at": note["updated_at"],
    }


def _list_combined_notes(experiment_name: str) -> list[dict]:
    items: list[dict] = []
    for run_id, run_name in _runs_for_experiment(experiment_name):
        for note in list_run_notes(run_id):
            items.append(_to_item("run", run_id, run_name, note))
    for note in list_experiment_notes(experiment_name):
        items.append(_to_item("experiment", None, None, note))
    return items


stream = KeyedDiffStream(
    name="experiment_notes_stream",
    list_fn=_list_combined_notes,
    id_fn=lambda item: item["id"],
    poll_interval_sec=NOTES_STREAM_POLL_INTERVAL_SEC,
)
