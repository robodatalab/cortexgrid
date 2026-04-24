"""WorkerLabelsReconciler — on head setup, labels any already-joined worker nodes.

Setup-only: workers self-deregister via their own NodeLabel.teardown.
"""

import json
import subprocess

from k8s.seed import util
from k8s.seed.pipeline import Context, Operator


class WorkerLabelsReconciler(Operator):
    def setup(self, ctx: Context) -> None:
        for node in ctx.cfg.get("nodes", []):
            if node["role"] != "worker":
                continue
            info = subprocess.run(
                ["kubectl", "get", "nodes", "-o", "json"],
                capture_output=True,
                text=True,
            )
            if info.returncode != 0:
                return
            data = json.loads(info.stdout)
            for item in data["items"]:
                if any(a["address"] == node["ip"] for a in item["status"]["addresses"]):
                    util.kubectl(
                        "label",
                        "node",
                        item["metadata"]["name"],
                        "role=worker",
                        "--overwrite",
                        capture=False,
                    )

    def teardown(self, ctx: Context) -> None:
        pass  # workers self-deregister via their own NodeLabel.teardown
