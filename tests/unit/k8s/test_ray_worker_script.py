"""Tests for the GPU ray-worker's startup script, run as the shell runs it.

The script turns what `nvidia-smi` reports into the `vram_mib` resource the
worker advertises, and a value it cannot read has taken a node out of the
cluster before (a GPU with unified memory reports "[N/A]", which is not a
number). So the script is lifted out of the chart template and executed here
against a fake `nvidia-smi`, rather than asserted on as text.

Two substitutions make it runnable off-cluster: the Helm expressions become
literals, and `/proc/meminfo` becomes a fixture (macOS has no /proc, and a
Linux runner's own RAM would make the expectation machine-dependent).
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = (
    REPO_ROOT / "k8s/charts/cortexgrid/templates/ray/worker_daemonset.yaml"
)

MEMINFO = "MemTotal:       127014400 kB\n"  # 121 GiB, as on the DGX Spark
MEMINFO_MIB = 124037


def gpu_worker_script() -> str:
    """The GPU flavour's `args:` block, dedented, with Helm expressions
    replaced by literals."""
    template = TEMPLATE.read_text()
    block = re.search(r"args:\n            - \|\n(.*?)\n          \{\{- else \}\}",
                      template, re.DOTALL)
    assert block is not None, f"no GPU args block in {TEMPLATE}"
    script = "\n".join(line[14:] for line in block.group(1).splitlines())
    return re.sub(r"\{\{[^}]*\}\}", "head:6379", script)


class RayWorkerScriptTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "meminfo").write_text(MEMINFO)
        self.script = self.tmp / "worker.sh"
        self.script.write_text(
            gpu_worker_script().replace("/proc/meminfo", str(self.tmp / "meminfo"))
        )
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        # `ray start` is the last line of the script; this fake reports the
        # arguments it was given instead of joining a cluster.
        self._fake("ray", 'echo "ray $@"')

    def _fake(self, name: str, body: str) -> None:
        path = self.bin / name
        path.write_text(f"#!/bin/sh\n{body}\n")
        path.chmod(0o755)

    def run_script(self, nvidia_smi: str) -> subprocess.CompletedProcess[str]:
        """Run the worker script with `nvidia-smi` faked to `nvidia_smi`."""
        self._fake("nvidia-smi", nvidia_smi)
        return subprocess.run(
            ["sh", str(self.script)],
            capture_output=True,
            text=True,
            env={**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}"},
        )

    def test_advertises_the_memory_of_one_card(self) -> None:
        result = self.run_script("echo 12282")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--resources={"vram_mib": 12282}', result.stdout)

    def test_advertises_the_memory_of_every_card_it_was_given(self) -> None:
        result = self.run_script('printf "12282\\n24564\\n"')

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--resources={"vram_mib": 36846}', result.stdout)

    def test_a_gpu_with_unified_memory_advertises_the_host_memory(self) -> None:
        # nvidia-smi reports "[N/A]" for e.g. the DGX Spark's GB10: the GPU has
        # no memory of its own, it shares the host's.
        result = self.run_script('echo "[N/A]"')

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f'--resources={{"vram_mib": {MEMINFO_MIB}}}', result.stdout)
        self.assertIn("shares with the host", result.stderr)

    def test_the_node_label_always_matches_the_resource(self) -> None:
        """The label names the card's size and the resource reserves from it.
        cortexgrid ranks the tiers on the label and reserves on the resource
        (see `_placement_options`), so if the two ever disagreed a replica
        would be offered a card the reservation then refuses."""
        for nvidia_smi in (
            "echo 12282",
            'printf "12282\\n24564\\n"',
            'echo "[N/A]"',
        ):
            with self.subTest(nvidia_smi=nvidia_smi):
                result = self.run_script(nvidia_smi)

                self.assertEqual(result.returncode, 0, result.stderr)
                advertised = re.search(
                    r'--resources=\{"vram_mib": (\d+)\}', result.stdout
                )
                assert advertised is not None, result.stdout
                self.assertIn(
                    f"--labels=vram_mib={advertised.group(1)}", result.stdout
                )

    def test_no_gpu_at_all_fails_instead_of_joining(self) -> None:
        # A worker without a usable GPU must not take GPU work.
        result = self.run_script('echo "NVIDIA-SMI has failed" >&2; exit 9')

        self.assertEqual(result.returncode, 1)
        self.assertIn("no GPU", result.stderr)
        self.assertNotIn("ray", result.stdout)


if __name__ == "__main__":
    unittest.main()
