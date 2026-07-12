"""Regression test: the bundler must ship a first-party dependency whole.

`ingest` is defined in `remote_fixture`, a package git-installed into
site-packages (not this repo's own source tree -- see the image build). Its
__init__ eagerly imports a sibling, its `core` imports another sibling, and it
reads a package data file at runtime. That is exactly the shape that used to
reach the Ray worker as a hollow shell and die with ModuleNotFoundError.

Submitting `ingest` as a remote job and requiring it to FINISH -- with the
values it logged from the sibling and the data file -- proves the whole package
travelled with the job across the real worker sys.path boundary.
"""

from __future__ import annotations

import unittest

import cortexflow
from remote_fixture import ingest

from tests.integration.cortexflow._ray_run import (
    _schedule_and_wait,
    experiment_name,
    get_logger,
)

log = get_logger(__name__)


class TestRemoteInstalledFirstPartyPackage(unittest.TestCase):
    def setUp(self) -> None:
        cortexflow.Experiment.close()
        self.addCleanup(cortexflow.Experiment.close)

    def test_entry_in_installed_package_runs_remotely(self) -> None:
        name = experiment_name(self)
        self.addCleanup(cortexflow.delete_experiment, name)
        exp = cortexflow.Experiment.init(name)

        _schedule_and_wait(ingest)

        params = cortexflow.list_run_params(exp.run_id)
        # Proves manifest.json (package data) shipped.
        self.assertEqual(params.get("fixture_source"), "huggingface")
        # Proves weights.py (sibling reached only via the import chain) shipped.
        self.assertEqual(params.get("weight_count"), "7")


if __name__ == "__main__":
    unittest.main()
