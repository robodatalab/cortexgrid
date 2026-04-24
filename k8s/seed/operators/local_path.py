"""LocalPath — points local-path-provisioner at a specific HDD path on the head.

Required deps: connection, node_ip, storage_path.

Setup patches the local-path-config configmap; teardown wipes the HDD contents
(with an explicit confirmation prompt, since it destroys workload data).
"""

import json
import logging
import shlex
import subprocess
import textwrap

import yaml  # type: ignore

from k8s.seed import util
from k8s.seed.pipeline import Operator


log = logging.getLogger("k8s.seed.operators.local_path")


class LocalPath(Operator):
    def setup(self, deps: dict) -> None:
        node_ip = deps["node_ip"]
        storage_path = deps["storage_path"]
        log.info(
            f"Configuring local-path-provisioner to use {storage_path} on {node_ip}..."
        )

        def _configmap_ready() -> tuple[bool, str]:
            result = subprocess.run(
                ["kubectl", "-n", "kube-system", "get", "cm", "local-path-config"],
                capture_output=True,
                text=True,
            )
            return result.returncode == 0, result.stderr

        util.poll_until(
            _configmap_ready,
            "local-path-config configmap in kube-system",
            timeout_s=60,
        )
        node_name = util.await_node(node_ip)
        config_json = json.dumps(
            {
                "nodePathMap": [
                    {
                        "node": "DEFAULT_PATH_FOR_NON_LISTED_NODES",
                        "paths": ["/var/lib/rancher/k3s/storage"],
                    },
                    {"node": node_name, "paths": [storage_path]},
                ]
            }
        )
        patch = yaml.safe_dump({"data": {"config.json": config_json}})
        util.kubectl(
            "-n", "kube-system", "patch", "cm", "local-path-config",
            "--type=merge", "--patch", patch, capture=False,
        )

    def teardown(self, deps: dict) -> None:
        c = deps["connection"]
        storage_path = deps["storage_path"]
        confirm = input(
            f"Wipe PVC contents under {storage_path} on {c.host}? This destroys "
            f"all workload data persisted to the HDD. [y/N] "
        )
        if confirm.strip().lower() != "y":
            log.info(f"Skipped wiping {storage_path}.")
            return
        util.sudo_script(
            c,
            textwrap.dedent(f"""\
            set -euo pipefail
            rm -rf {shlex.quote(storage_path)}/*
        """),
        )
        log.info(f"Wiped contents of {storage_path}.")
