"""Tear down a robolab cluster node.

Usage:
    uv run python k8s/seed/teardown-node.py --ip=X [--ssh-user=Z]

Dispatcher responsibilities:
  - Look the node up in infra-config.yaml.
  - Resolve every pipeline dependency into a single deps dict.
  - Open the SSH connection, call head.build() / worker.build(), then run
    pipeline.teardown(deps).
"""

import argparse
import getpass
import logging
import sys

from dotenv import load_dotenv
from tqdm import tqdm

from k8s.seed import head, util, worker


BOOTSTRAP_FILE = util.REPO_ROOT / "k8s" / "argocd.yaml"

log = logging.getLogger("k8s.seed.teardown_node")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Tear down a robolab cluster node.")
    p.add_argument("--ip", required=True)
    p.add_argument("--ssh-user", default=None)
    args = p.parse_args()
    if args.ssh_user is None:
        args.ssh_user = util.ssh_user_for_ip(args.ip) or getpass.getuser()
    return args


def _run_head(args: argparse.Namespace, entry: dict, cfg: dict, sudo_pw: str) -> None:
    workers = [n for n in cfg.get("nodes", []) if n["role"] == "worker"]
    pipeline = head.build()
    with util.connect(args.ssh_user, args.ip, sudo_pw) as c, tqdm(
        total=len(pipeline.operators), desc=f"Head teardown {args.ip}"
    ) as bar:
        def on_step_done(name: str) -> None:
            util.checkpoint_step_done(args.ip, name, "teardown")
            bar.set_postfix_str(name)
            bar.update(1)

        pipeline.on_step_done = on_step_done
        deps = {
            "connection": c,
            "node_ip": args.ip,
            "bootstrap_file": BOOTSTRAP_FILE,
            "env_file": util.ENV_FILE,
            "storage_path": entry["storage_path"],
            "workers": workers,
        }
        pipeline.teardown(deps)
    log.info("Head teardown complete.")


def _run_worker(args: argparse.Namespace, sudo_pw: str) -> None:
    # Mode is irrelevant for teardown — JoinCluster.teardown runs both cleanups.
    # We still need to pick *some* valid mode to construct the pipeline.
    pipeline = worker.build(mode="direct")
    with util.connect(args.ssh_user, args.ip, sudo_pw) as c, tqdm(
        total=len(pipeline.operators), desc=f"Worker teardown {args.ip}"
    ) as bar:
        def on_step_done(name: str) -> None:
            util.checkpoint_step_done(args.ip, name, "teardown")
            bar.set_postfix_str(name)
            bar.update(1)

        pipeline.on_step_done = on_step_done
        deps = {
            "connection": c,
            "node_ip": args.ip,
        }
        pipeline.teardown(deps)
    log.info("Worker teardown complete.")


def main() -> None:
    args = parse_args()
    cfg = util.load_config()
    entry = next((n for n in cfg["nodes"] if n["ip"] == args.ip), None)
    if entry is None:
        sys.exit(
            f"Error: {args.ip} is not in infra-config.yaml. "
            f"Nothing to tear down — refusing to touch an untracked node."
        )

    confirm = input(
        f"This will wipe the {entry['role']} node at {args.ip} "
        f"(k3s, toolkit, any deferred-join timer). Continue? [y/N] "
    )
    if confirm.strip().lower() != "y":
        sys.exit(0)

    load_dotenv(util.ENV_FILE)
    sudo_pw = getpass.getpass("Node password (SSH + sudo): ")

    if entry["role"] == "head":
        _run_head(args, entry, cfg, sudo_pw)
    else:
        _run_worker(args, sudo_pw)

    cfg["nodes"] = [n for n in cfg["nodes"] if n["ip"] != args.ip]
    util.save_config(cfg)
    logging.info(f"Removed {args.ip} from infra-config.yaml.")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    main()
