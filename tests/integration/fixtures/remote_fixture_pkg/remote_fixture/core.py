"""The remote entry function. It reaches a sibling module and a package data
file, both of which must travel with the job for it to run on a bare worker."""

import json
from pathlib import Path

import cortexflow

from remote_fixture.weights import weight_count


def ingest() -> None:
    manifest = json.loads((Path(__file__).parent / "manifest.json").read_text())
    cortexflow.log_params(
        {"fixture_source": manifest["source"], "weight_count": str(weight_count())}
    )
