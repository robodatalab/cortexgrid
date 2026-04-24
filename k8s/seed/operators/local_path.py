"""LocalPath — points local-path-provisioner at a specific HDD path on the head.

Setup patches the local-path-config configmap.
Teardown wipes the HDD contents (with an explicit confirmation prompt).
"""

import json
import logging
import shlex
import subprocess
import textwrap
import time

import yaml  # type: ignore

from k8s.seed import util
from k8s.seed.pipeline import Context, Operator


log = logging.getLogger("k8s.seed.operators.local_path")


class LocalPath(Operator):
    def setup(self, ctx: Context) -> None:
        node_ip = ctx.args.ip
        storage_path = ctx.args.storage_path
        log.info(
            f"Configuring local-path-provisioner to use {storage_path} on {node_ip}..."
        )
        while True:
            result = subprocess.run(
                ["kubectl", "-n", "kube-system", "get", "cm", "local-path-config"],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                break
            time.sleep(2)
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
            "-n",
            "kube-system",
            "patch",
            "cm",
            "local-path-config",
            "--type=merge",
            "--patch",
            patch,
            capture=False,
        )

    def teardown(self, ctx: Context) -> None:
        storage_path = (ctx.entry or {}).get("storage_path")
        if not storage_path:
            return
        c = ctx.connection
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
