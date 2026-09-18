from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import requests

import cortexgrid
from cortexgrid.model_serving import _app_name
from cortexgrid.ray_util import get_serve_details

from tests.integration.cortexgrid._ray_run import (
    experiment_name,
    get_logger,
    run,
)
from tests.integration.stubs.serving import (
    AddConstantServeApp,
    contact_deployment,
    write_weights,
)


log = get_logger(__name__)


class TestModelServing(unittest.TestCase):
    def setUp(self) -> None:
        cortexgrid.Experiment.close()
        self.addCleanup(cortexgrid.Experiment.close)
        self.name = experiment_name(self)
        self.addCleanup(cortexgrid.delete_experiment, self.name)
        self.exp = cortexgrid.Experiment.init(self.name)
        self.run_name = self.exp.run_name()

    def _save_stub(self, family: str, suffix: str, constant: int) -> None:
        with tempfile.TemporaryDirectory() as d:
            write_weights(Path(d), constant)
            cortexgrid.save_model(
                Path(d), AddConstantServeApp, family=family, suffix=suffix
            )

    def test_deployed_model_returns_inference_result(self) -> None:
        family, suffix = "it-deploy", "stub"
        self._save_stub(family, suffix, constant=15)

        deployed = cortexgrid.deploy_model(family, suffix, self.run_name, wait=True)
        try:
            response = requests.post(
                f"{deployed.url}/add", json={"x": 27}, timeout=60
            )
            response.raise_for_status()
            self.assertEqual(response.json()["result"], 42)
        finally:
            cortexgrid.undeploy_model(family, suffix, self.run_name)

    def _last_deployed_time(self, family: str, suffix: str) -> float:
        name = _app_name(family, suffix, self.run_name)
        return get_serve_details()["applications"][name]["last_deployed_time_s"]

    def test_redeploying_an_unchanged_model_leaves_the_serve_app_untouched(
        self,
    ) -> None:
        # `deploy_model` skips the PUT when the spec it built is already the
        # app's target. The controller restamps last_deployed_time_s on every
        # PUT, so a stamp that has not moved is the cluster-side proof that
        # nothing was re-applied - and that a build in flight would have been
        # left alone.
        family, suffix = "it-redeploy", "stub"
        self._save_stub(family, suffix, constant=3)

        cortexgrid.deploy_model(family, suffix, self.run_name, wait=True)
        try:
            before = self._last_deployed_time(family, suffix)

            cortexgrid.deploy_model(family, suffix, self.run_name)

            self.assertEqual(self._last_deployed_time(family, suffix), before)
        finally:
            cortexgrid.undeploy_model(family, suffix, self.run_name)

    def test_deployed_model_can_be_contacted_from_a_job(self) -> None:
        family, suffix = "it-deploy-from-job", "stub"
        self._save_stub(family, suffix, constant=10)

        cortexgrid.deploy_model(family, suffix, self.run_name, wait=True)
        try:
            run(
                "remote", log, contact_deployment,
                family, suffix, self.run_name, 5, 15,
            )
        finally:
            cortexgrid.undeploy_model(family, suffix, self.run_name)


if __name__ == "__main__":
    unittest.main()
