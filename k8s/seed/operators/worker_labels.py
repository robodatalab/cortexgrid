"""WorkerLabels — labels each already-joined worker by IP; unlabels on teardown.

Required deps: workers (list of dicts, each with at least {"ip": str, "role": ...}).
"""

import json
import logging
import subprocess

from k8s.seed import util
from k8s.seed.pipeline import Operator


log = logging.getLogger("k8s.seed.operators.worker_labels")


class WorkerLabels(Operator):
    def setup(self, deps: dict) -> None:
        for name in self._resolve_node_names(deps["workers"]):
            util.kubectl(
                "label", "node", name, "role=worker", "--overwrite",
                capture=False, check=False,
            )

    def teardown(self, deps: dict) -> None:
        for name in self._resolve_node_names(deps["workers"]):
            util.kubectl(
                "label", "node", name, "role-",
                capture=False, check=False,
            )

    def _resolve_node_names(self, workers: list[dict]) -> list[str]:
        if not workers:
            return []
        info = subprocess.run(
            ["kubectl", "get", "nodes", "-o", "json"],
            capture_output=True,
            text=True,
        )
        if info.returncode != 0:
            log.info("Cluster unreachable; skipping worker labels.")
            return []
        data = json.loads(info.stdout)
        ips = {n["ip"] for n in workers}
        names = []
        for item in data["items"]:
            addrs = {a["address"] for a in item["status"]["addresses"]}
            if addrs & ips:
                names.append(item["metadata"]["name"])
        return names
