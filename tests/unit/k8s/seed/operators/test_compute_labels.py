from __future__ import annotations

import unittest
from unittest import mock

from parameterized import parameterized  # type: ignore

from k8s.seed import util
from k8s.seed.operators.compute_labels import ComputeLabels


def _nvidia_smi_host(exited: int, stdout: str = "", stderr: str = "") -> mock.MagicMock:
    c = mock.MagicMock(host="spark")
    c.run.return_value = mock.MagicMock(
        ok=exited == 0, exited=exited, stdout=stdout, stderr=stderr
    )
    return c


class TestHasGpu(unittest.TestCase):
    """A host has a GPU iff `nvidia-smi -L` succeeds and lists one; it has none
    when nvidia-smi is missing or finds no devices; any other nvidia-smi failure
    exits rather than reporting a GPU host as CPU-only."""

    @parameterized.expand([
        ("gpu_listed", 0, "GPU 0: NVIDIA GB10 (UUID: GPU-1234)\n", True),
        ("not_installed", 127, "", False),
        ("no_devices", 6, "No devices were found\n", False),
    ])
    def test_has_gpu(self, _name: str, exited: int, stdout: str, expected: bool) -> None:
        c = _nvidia_smi_host(exited, stdout)

        self.assertEqual(util.has_gpu(c), expected)
        c.run.assert_called_once_with("nvidia-smi -L", hide=True, warn=True)

    def test_other_nvidia_smi_failure_exits(self) -> None:
        c = _nvidia_smi_host(
            18, stdout="Failed to initialize NVML: Driver/library version mismatch\n"
        )

        with self.assertRaises(SystemExit) as ctx:
            util.has_gpu(c)
        self.assertIn("exit code 18", str(ctx.exception))
        self.assertIn("Driver/library version mismatch", str(ctx.exception))


class TestComputeLabels(unittest.TestCase):
    """Setup labels a GPU host worker=true + gpu=true and a CPU-only host
    worker=true with any stale gpu label removed; teardown removes both."""

    @parameterized.expand([
        ("gpu", True, ["worker=true", "gpu=true"]),
        ("cpu", False, ["worker=true", "gpu-"]),
    ])
    def test_setup_labels_the_node_for_its_compute(
        self, _name: str, gpu: bool, labels: list[str]
    ) -> None:
        with (
            mock.patch.object(util, "resolve_node_name", return_value="spark-40f4"),
            mock.patch.object(util, "has_gpu", return_value=gpu),
            mock.patch.object(util, "kubectl") as kubectl,
        ):
            ComputeLabels().setup({"connection": mock.MagicMock(), "node_ip": "100.80.27.32"})

        kubectl.assert_called_once_with(
            "label", "node", "spark-40f4", *labels, "--overwrite", capture=False
        )

    def test_non_strict_skips_a_node_that_has_not_registered(self) -> None:
        with (
            mock.patch.object(util, "resolve_node_name", return_value=None),
            mock.patch.object(util, "has_gpu") as has_gpu,
            mock.patch.object(util, "kubectl") as kubectl,
        ):
            ComputeLabels(strict=False).setup(
                {"connection": mock.MagicMock(), "node_ip": "100.80.27.32"}
            )

        has_gpu.assert_not_called()
        kubectl.assert_not_called()

    def test_strict_fails_when_the_node_never_registers(self) -> None:
        with (
            mock.patch.object(util, "resolve_node_name", return_value=None),
            mock.patch.object(util, "kubectl") as kubectl,
            self.assertRaises(SystemExit),
        ):
            ComputeLabels().setup({"connection": mock.MagicMock(), "node_ip": "100.80.27.32"})

        kubectl.assert_not_called()

    def test_teardown_removes_the_compute_labels(self) -> None:
        with (
            mock.patch.object(util, "resolve_node_name", return_value="spark-40f4"),
            mock.patch.object(util, "kubectl") as kubectl,
        ):
            ComputeLabels().teardown({"node_ip": "100.80.27.32"})

        kubectl.assert_called_once_with(
            "label", "node", "spark-40f4", "worker-", "gpu-", capture=False, check=False
        )


if __name__ == "__main__":
    unittest.main()
