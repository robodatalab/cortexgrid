"""Seed a robolab cluster node.

Usage:
    uv run python k8s/seed/setup-node.py --type=head --ip=X --storage-path=Y [--ssh-user=Z]
    uv run python k8s/seed/setup-node.py --type=worker --ip=X [--ssh-user=Z]

Dispatcher responsibilities:
  - Parse args, validate and update infra-config.yaml.
  - Resolve every pipeline dependency (env vars, head creds from AWS SM, etc.)
    into a single deps dict.
  - Open the SSH connection, call head.build() / worker.build(), then run
    pipeline.setup(deps).
"""

import argparse
import getpass
import logging
import os
import sys
from typing import Literal

from dotenv import load_dotenv
from tqdm import tqdm

from cortexflow.secrets import get_secret, list_secrets
from k8s.seed import head, util, worker


BOOTSTRAP_FILE = util.REPO_ROOT / "k8s" / "argocd.yaml"

log = logging.getLogger("k8s.seed.setup_node")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Seed a robolab cluster node.")
    p.add_argument("--type", required=True, choices=["head", "worker"])
    p.add_argument("--ip", required=True)
    p.add_argument(
        "--storage-path", help="Required for --type=head; rejected for --type=worker"
    )
    p.add_argument("--ssh-user", default=None)
    args = p.parse_args()

    if args.type == "head" and not args.storage_path:
        p.error("--storage-path is required when --type=head")
    if args.type == "worker" and args.storage_path:
        p.error("--storage-path is only valid when --type=head")

    if args.ssh_user is None:
        args.ssh_user = util.ssh_user_for_ip(args.ip) or getpass.getuser()

    return args


def validate_and_update(cfg: dict, args: argparse.Namespace) -> dict:
    existing = next((n for n in cfg["nodes"] if n["ip"] == args.ip), None)

    if existing is not None:
        if existing["role"] != args.type:
            sys.exit(
                f"Error: node {args.ip} is already registered as role={existing['role']}. "
                f"Run teardown-node first to change its role."
            )
        if args.type == "head" and existing.get("storage_path") != args.storage_path:
            sys.exit(
                f"Error: node {args.ip} is already registered with "
                f"storage_path={existing.get('storage_path')}. "
                f"Run teardown-node first to change storage_path."
            )

    if args.type == "head":
        other_head = next(
            (n for n in cfg["nodes"] if n["role"] == "head" and n["ip"] != args.ip),
            None,
        )
        if other_head is not None:
            sys.exit(
                f"Error: cluster already has a head at {other_head['ip']}. "
                f"Only one head is allowed — run teardown-node on the existing head first."
            )

    entry = {"ip": args.ip, "role": args.type}
    if args.type == "head":
        entry["storage_path"] = args.storage_path
    if existing is not None and "progress" in existing:
        # Carry the checkpoint across re-runs so a prior failure's state is visible.
        entry["progress"] = existing["progress"]

    if existing is not None:
        cfg["nodes"] = [entry if n["ip"] == args.ip else n for n in cfg["nodes"]]
    else:
        cfg["nodes"].append(entry)

    return cfg


def _lookup_head_creds() -> tuple[str | None, str | None]:
    available = set(list_secrets())
    if util.SECRET_K3S_TOKEN in available and util.SECRET_CONTROL_PLANE_IP in available:
        return get_secret(util.SECRET_K3S_TOKEN), get_secret(
            util.SECRET_CONTROL_PLANE_IP
        )
    return None, None


def _run_head(args: argparse.Namespace, cfg: dict, sudo_pw: str) -> None:
    workers = [n for n in cfg.get("nodes", []) if n["role"] == "worker"]
    pipeline = head.build()
    with util.connect(args.ssh_user, args.ip, sudo_pw) as c, tqdm(
        total=len(pipeline.operators), desc=f"Head setup {args.ip}"
    ) as bar:
        def on_step_done(name: str) -> None:
            util.checkpoint_step_done(args.ip, name, "setup")
            bar.set_postfix_str(name)
            bar.update(1)

        pipeline.on_step_done = on_step_done
        deps = {
            "connection": c,
            "node_ip": args.ip,
            "bootstrap_file": BOOTSTRAP_FILE,
            "env_file": util.ENV_FILE,
            "storage_path": args.storage_path,
            "workers": workers,
            "github_token": os.environ["GH_TOKEN"],
            "aws_access_key_id": os.environ["AWS_ACCESS_KEY_ID"],
            "aws_secret_access_key": os.environ["AWS_SECRET_ACCESS_KEY"],
        }
        pipeline.setup(deps)
    log.info(
        f"\nHead seeded.\n  Argo UI: http://{args.ip}:30080 (auth disabled, via Tailscale)"
    )


def _run_worker(args: argparse.Namespace, sudo_pw: str) -> None:
    head_token, head_ip = _lookup_head_creds()
    head_ready = head_token is not None and head_ip is not None
    mode: Literal["direct", "deferred"] = "direct" if head_ready else "deferred"

    pipeline = worker.build(mode=mode)
    with util.connect(args.ssh_user, args.ip, sudo_pw) as c, tqdm(
        total=len(pipeline.operators), desc=f"Worker setup {args.ip}"
    ) as bar:
        def on_step_done(name: str) -> None:
            util.checkpoint_step_done(args.ip, name, "setup")
            bar.set_postfix_str(name)
            bar.update(1)

        pipeline.on_step_done = on_step_done
        deps: dict = {
            "connection": c,
            "node_ip": args.ip,
        }
        if head_ready:
            deps["head_ip"] = head_ip
            deps["head_token"] = head_token
        else:
            deps["aws_access_key_id"] = os.environ["AWS_ACCESS_KEY_ID"]
            deps["aws_secret_access_key"] = os.environ["AWS_SECRET_ACCESS_KEY"]
        pipeline.setup(deps)

    if head_ready:
        log.info(f"\nWorker seeded and joined cluster at {head_ip}.")
    else:
        log.info(
            f"\nWorker prerequisites installed and deferred-join timer active on {args.ip}. "
            f"The worker will join the cluster automatically within ~60s of the head being seeded."
        )


def main() -> None:
    args = parse_args()
    cfg = util.load_config()
    cfg = validate_and_update(cfg, args)
    util.save_config(cfg)

    load_dotenv(util.ENV_FILE)
    sudo_pw = getpass.getpass("Node password (SSH + sudo): ")

    if args.type == "head":
        _run_head(args, cfg, sudo_pw)
    else:
        _run_worker(args, sudo_pw)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    main()
