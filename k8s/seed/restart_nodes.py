"""Restart every node listed in infra-config.yaml.

Snapshots the registered nodes, tears each one down, then seeds it again.
Workers are torn down before the head and seeded after it, so the cluster
stays in a valid topology at each step.

Uses head.build() / worker.build() and pipeline.setup / pipeline.teardown
directly -- no subprocesses.
"""

import getpass
import logging
import os
import sys
from typing import Callable

import requests  # type: ignore
from tqdm import tqdm

from cortexgrid.secrets import get_secret, list_secrets
from k8s.seed import head, util, worker


BOOTSTRAP_FILE = util.REPO_ROOT / "k8s" / "argocd.yaml"

log = logging.getLogger("k8s.seed.restart_nodes")


def _password_callbacks(
    user: str, ip: str
) -> tuple[Callable[[], str], Callable[[], str]]:
    def ssh_pw() -> str:
        return getpass.getpass(f"SSH password for {user}@{ip}: ")

    def sudo_pw() -> str:
        return getpass.getpass(f"Sudo password for {user}@{ip}: ")

    return ssh_pw, sudo_pw


def _run(
    pipeline,
    deps: dict,
    *,
    direction: str,
    ip: str,
    desc: str,
) -> None:
    with tqdm(total=len(pipeline.operators), desc=desc) as bar:

        def on_step_done(name: str) -> None:
            util.checkpoint_step_done(ip, name, direction)
            bar.set_postfix_str(name)
            bar.update(1)

        pipeline.on_step_done = on_step_done
        if direction == "setup":
            pipeline.setup(deps)
        else:
            pipeline.teardown(deps)


def _teardown_head(entry: dict) -> None:
    ip = entry["ip"]
    user = util.ssh_user_for_ip(ip) or getpass.getuser()
    ssh_pw, sudo_pw = _password_callbacks(user, ip)
    # Match what teardown_node.main passes when invoked by hand: the workers
    # list comes from the current state of infra-config.yaml, not a snapshot.
    cfg = util.load_config()
    workers = [n for n in cfg.get("nodes", []) if n["role"] == "worker"]
    pipeline = head.build()
    with util.connect(user, ip, ssh_pw, sudo_pw) as c:
        _run(
            pipeline,
            {
                "connection": c,
                "node_ip": ip,
                "bootstrap_file": BOOTSTRAP_FILE,
                "env_file": util.ENV_FILE,
                "storage_path": entry["storage_path"],
                "workers": workers,
                "profile": entry["profile"],
            },
            direction="teardown",
            ip=ip,
            desc=f"Head teardown {ip}",
        )


def _teardown_worker(entry: dict) -> None:
    ip = entry["ip"]
    user = util.ssh_user_for_ip(ip) or getpass.getuser()
    ssh_pw, sudo_pw = _password_callbacks(user, ip)
    # Mode is irrelevant for teardown -- JoinCluster.teardown runs both cleanups.
    pipeline = worker.build(mode="direct")
    with util.connect(user, ip, ssh_pw, sudo_pw) as c:
        _run(
            pipeline,
            {"connection": c, "node_ip": ip},
            direction="teardown",
            ip=ip,
            desc=f"Worker teardown {ip}",
        )


def _setup_head(entry: dict, env: dict[str, str]) -> None:
    ip = entry["ip"]
    user = util.ssh_user_for_ip(ip) or getpass.getuser()
    ssh_pw, sudo_pw = _password_callbacks(user, ip)
    # Match what setup_node.main passes when invoked by hand: the workers
    # list comes from the current state of infra-config.yaml, not a snapshot.
    cfg = util.load_config()
    workers = [n for n in cfg.get("nodes", []) if n["role"] == "worker"]
    pipeline = head.build()
    with util.connect(user, ip, ssh_pw, sudo_pw) as c:
        _run(
            pipeline,
            {
                "connection": c,
                "node_ip": ip,
                "bootstrap_file": BOOTSTRAP_FILE,
                "env_file": util.ENV_FILE,
                "storage_path": entry["storage_path"],
                "workers": workers,
                "profile": entry["profile"],
                "github_token": env["GH_TOKEN"],
            },
            direction="setup",
            ip=ip,
            desc=f"Head setup {ip}",
        )


def _setup_worker(entry: dict) -> None:
    ip = entry["ip"]
    user = util.ssh_user_for_ip(ip) or getpass.getuser()
    ssh_pw, sudo_pw = _password_callbacks(user, ip)
    try:
        available = set(list_secrets())
    except requests.RequestException:
        available = set()
    head_ready = (
        util.SECRET_K3S_TOKEN in available
        and util.SECRET_CONTROL_PLANE_IP in available
    )
    pipeline = worker.build(mode="direct" if head_ready else "deferred")
    deps: dict = {"connection": None, "node_ip": ip, "profile": entry["profile"]}
    if head_ready:
        deps["head_ip"] = get_secret(util.SECRET_CONTROL_PLANE_IP)
        deps["head_token"] = get_secret(util.SECRET_K3S_TOKEN)
    else:
        deps["head_url"] = os.environ["CORTEXGRID_HEAD_URL"]
    with util.connect(user, ip, ssh_pw, sudo_pw) as c:
        deps["connection"] = c
        _run(
            pipeline,
            deps,
            direction="setup",
            ip=ip,
            desc=f"Worker setup {ip}",
        )


def _config_remove(ip: str) -> None:
    cfg = util.load_config()
    cfg["nodes"] = [n for n in cfg["nodes"] if n["ip"] != ip]
    util.save_config(cfg)


def _config_add(entry: dict) -> None:
    cfg = util.load_config()
    fresh = {"ip": entry["ip"], "role": entry["role"], "profile": entry["profile"]}
    if entry["role"] == "head":
        fresh["storage_path"] = entry["storage_path"]
    cfg.setdefault("nodes", []).append(fresh)
    util.save_config(cfg)


def main() -> None:
    cfg = util.load_config()
    snapshot = list(cfg.get("nodes", []))
    if not snapshot:
        sys.exit("Error: no nodes in infra-config.yaml -- nothing to restart.")

    head_entry = next((n for n in snapshot if n["role"] == "head"), None)
    worker_entries = [n for n in snapshot if n["role"] == "worker"]

    # Validate .env.head before tearing anything down.
    env = util.load_head_env() if head_entry else {}
    # cortexgrid.secrets (used by the operators) talks to $CORTEXGRID_HEAD_URL.
    os.environ["CORTEXGRID_HEAD_URL"] = util.head_url(cfg)

    log.info(
        "Restarting %d node(s): head=%s, workers=%s",
        len(snapshot),
        head_entry["ip"] if head_entry else "(none)",
        [w["ip"] for w in worker_entries] or "(none)",
    )

    for w in worker_entries:
        _teardown_worker(w)
        _config_remove(w["ip"])
    if head_entry is not None:
        _teardown_head(head_entry)
        _config_remove(head_entry["ip"])

    if head_entry is not None:
        _config_add(head_entry)
        _setup_head(head_entry, env)
    for w in worker_entries:
        _config_add(w)
        _setup_worker(w)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    main()
