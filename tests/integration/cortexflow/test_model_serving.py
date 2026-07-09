from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import requests

import cortexflow

from tests.integration.cortexflow._ray_run import (
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
        cortexflow.Experiment.close()
        self.addCleanup(cortexflow.Experiment.close)
        self.name = experiment_name(self)
        self.addCleanup(cortexflow.delete_experiment, self.name)
        self.exp = cortexflow.Experiment.init(self.name)
        self.run_name = self.exp.run_name()

    def _save_stub(self, family: str, suffix: str, constant: int) -> None:
        with tempfile.TemporaryDirectory() as d:
            write_weights(Path(d), constant)
            cortexflow.save_model(
                Path(d), AddConstantServeApp, family=family, suffix=suffix
            )

    def test_deployed_model_returns_inference_result(self) -> None:
        family, suffix = "it-deploy", "stub"
        self._save_stub(family, suffix, constant=15)

        with self.assertLogs("cortexflow.model_serving", level="INFO") as captured:
            deployed = cortexflow.deploy_model(
                family, suffix, self.run_name, wait=True
            )
        pip_log = next(
            (
                r.getMessage()
                for r in captured.records
                if "runtime_env.pip" in r.getMessage()
            ),
            "",
        )
        self.assertTrue(pip_log, "deploy_model must log runtime_env.pip")
        offending = [
            line
            for line in pip_log.splitlines()
            if line.strip().startswith("torch==") or line.strip() == "torch"
        ]
        self.assertEqual(
            offending,
            [],
            f"torch must not be in runtime_env.pip (baked into ray image); log was:\n{pip_log}",
        )
        try:
            response = requests.post(
                f"{deployed.url}/add", json={"x": 27}, timeout=60
            )
            response.raise_for_status()
            self.assertEqual(response.json()["result"], 42)
        finally:
            cortexflow.undeploy_model(family, suffix, self.run_name)

    def test_deployed_model_can_be_contacted_from_a_job(self) -> None:
        family, suffix = "it-deploy-from-job", "stub"
        self._save_stub(family, suffix, constant=10)

        cortexflow.deploy_model(family, suffix, self.run_name, wait=True)
        try:
            run(
                "remote", log, contact_deployment,
                family, suffix, self.run_name, 5, 15,
            )
        finally:
            cortexflow.undeploy_model(family, suffix, self.run_name)


if __name__ == "__main__":
    unittest.main()
