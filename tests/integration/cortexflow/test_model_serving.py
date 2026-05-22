from __future__ import annotations

import unittest

import cortexflow

from tests.integration.cortexflow._ray_run import (
    experiment_name,
    get_logger,
    run,
)
from tests.integration.cortexflow._serving_stub import (
    AddConstantModel,
    contact_deployment,
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

    def test_deployed_model_returns_inference_result(self) -> None:
        family, suffix = "it-deploy", "stub"
        cortexflow.save_model(
            AddConstantModel(constant=15), family=family, suffix=suffix
        )

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
            self.assertEqual(deployed.infer(27), 42)
        finally:
            cortexflow.undeploy_model(family, suffix, self.run_name)

    def test_deployed_model_can_be_contacted_from_a_job(self) -> None:
        family, suffix = "it-deploy-from-job", "stub"
        cortexflow.save_model(
            AddConstantModel(constant=10), family=family, suffix=suffix
        )

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
