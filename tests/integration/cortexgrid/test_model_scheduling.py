from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import requests

import cortexgrid
from cortexgrid.model_serving import _VRAM_LABEL, _app_name, _phase
from cortexgrid.ray_util import get_ray_nodes, get_serve_details

from tests.integration.cortexgrid._ray_run import experiment_name
from tests.integration.stubs.serving import AddConstantServeApp, write_weights


_SUFFIX = "stub"
_REQUEST_TIMEOUT_WHILE_RESUMING_S = 300


class TestModelScheduling(unittest.TestCase):
    def setUp(self) -> None:
        cortexgrid.Experiment.close()
        self.addCleanup(cortexgrid.Experiment.close)
        self.name = experiment_name(self)
        self.addCleanup(cortexgrid.delete_experiment, self.name)
        self.exp = cortexgrid.Experiment.init(self.name)
        self.run_name = self.exp.run_name()

    def test_two_models_that_each_need_the_whole_gpu_take_turns_on_it(self) -> None:
        whole_gpu = self._requirements_filling_the_only_largest_gpu()
        first = self._save_and_deploy_stub("it-sched-first", 1, whole_gpu)
        second = self._save_and_deploy_stub("it-sched-second", 2, whole_gpu)
        self.assertEqual(self._phase_of("it-sched-first"), "paused")

        self.assertEqual(_add(first, 10), 11)
        self.assertEqual(_add(second, 10), 12)
        self.assertEqual(_add(first, 10), 11)

        self.assertEqual(self._phase_of("it-sched-first"), "running")
        self.assertEqual(self._phase_of("it-sched-second"), "paused")

    def _requirements_filling_the_only_largest_gpu(
        self,
    ) -> cortexgrid.ModelRequirements:
        gpu_sizes_mib = [
            int(node["labels"][_VRAM_LABEL])
            for node in get_ray_nodes()
            if node.get("state") == "ALIVE"
            and _VRAM_LABEL in (node.get("labels") or {})
        ]
        if not gpu_sizes_mib:
            self.skipTest("the cluster has no GPU")
        largest_gpu_mib = max(gpu_sizes_mib)
        if gpu_sizes_mib.count(largest_gpu_mib) > 1:
            self.skipTest(
                f"{gpu_sizes_mib.count(largest_gpu_mib)} GPUs of {largest_gpu_mib} MiB "
                "would host both models at once"
            )
        return cortexgrid.ModelRequirements(
            num_gpus=1, vram_gb=largest_gpu_mib / 1024
        )

    def _save_and_deploy_stub(
        self, family: str, constant: int, requirements: cortexgrid.ModelRequirements
    ) -> cortexgrid.Deployment:
        with tempfile.TemporaryDirectory() as d:
            write_weights(Path(d), constant)
            cortexgrid.save_model(
                Path(d),
                AddConstantServeApp,
                family=family,
                suffix=_SUFFIX,
                requirements=requirements,
            )
        deployed = cortexgrid.deploy_model(family, _SUFFIX, self.run_name, wait=True)
        self.addCleanup(cortexgrid.undeploy_model, family, _SUFFIX, self.run_name)
        return deployed

    def _phase_of(self, family: str) -> str:
        name = _app_name(family, _SUFFIX, self.run_name)
        return _phase(get_serve_details()["applications"][name])


def _add(deployed: cortexgrid.Deployment, x: int) -> int:
    response = requests.post(
        f"{deployed.url}/add", json={"x": x}, timeout=_REQUEST_TIMEOUT_WHILE_RESUMING_S
    )
    response.raise_for_status()
    return response.json()["result"]


if __name__ == "__main__":
    unittest.main()
