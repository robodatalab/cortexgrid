from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from pathlib import Path

import cortexflow

from tests.integration.cortexflow._ray_run import (
    experiment_name,
    get_logger,
    run,
)
from tests.integration.cortexflow._serving_stub import (
    CheckpointReadingStub,
    contact_deployment,
    wait_for_endpoint,
)


log = get_logger(__name__)


class TestModelServing(unittest.TestCase):
    def setUp(self) -> None:
        cortexflow.Experiment.close()
        self.addCleanup(cortexflow.Experiment.close)
        self.name = experiment_name(self)
        self.addCleanup(cortexflow.delete_experiment, self.name)
        self.exp = cortexflow.Experiment.init(self.name)
        self.run_name = self.exp.run_name()

    def _save_marker_model(self, family: str, suffix: str) -> str:
        marker = uuid.uuid4().hex
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "marker.json").write_text(json.dumps({"marker": marker}))
            cortexflow.save_model(d, suffix=suffix, family=family)
        return marker

    def test_deployed_model_can_be_contacted_locally(self) -> None:
        family, suffix = "it-deploy", "stub"
        marker = self._save_marker_model(family, suffix)

        deployment = cortexflow.deploy_model(
            CheckpointReadingStub, family, suffix, self.run_name
        )
        try:
            response = wait_for_endpoint(f"{deployment.url}/marker")
            self.assertEqual(response.json(), {"marker": marker})
        finally:
            cortexflow.undeploy_model(family, suffix, self.run_name)

    def test_deployed_model_can_be_contacted_from_a_job(self) -> None:
        family, suffix = "it-deploy-from-job", "stub"
        marker = self._save_marker_model(family, suffix)

        deployment = cortexflow.deploy_model(
            CheckpointReadingStub, family, suffix, self.run_name
        )
        try:
            run("remote", log, contact_deployment, deployment.url, marker)
        finally:
            cortexflow.undeploy_model(family, suffix, self.run_name)


if __name__ == "__main__":
    unittest.main()
