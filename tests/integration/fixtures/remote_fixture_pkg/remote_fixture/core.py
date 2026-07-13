"""The remote entry function. It reaches a sibling module that the entry never
imports directly -- only the package __init__ / this module's import chain does.
That sibling must travel with the job for it to run on a bare worker."""

import cortexflow

from remote_fixture.weights import weight_count


def ingest() -> None:
    cortexflow.log_params({"weight_count": str(weight_count())})
