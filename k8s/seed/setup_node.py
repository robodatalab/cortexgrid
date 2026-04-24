"""Seed a robolab cluster node.

Usage:
    uv run python k8s/seed/setup-node.py --type=head --ip=X --storage-path=Y [--ssh-user=Z]
    uv run python k8s/seed/setup-node.py --type=worker --ip=X [--ssh-user=Z]

All operations are idempotent. Topology is recorded in ./infra-config.yaml at
the repo root and is read by other infra scripts (teardown-node, etc.).
Run teardown-node first to change a node's role or config.

Worker-before-head is supported: when the head has not been seeded yet, the
worker command installs prerequisites, drops a systemd timer on the worker
that polls AWS Secrets Manager for the cluster's K3S_NODE_TOKEN and control-
plane IP, and joins automatically once those appear. The command returns
immediately — no re-run needed.

This module is a dispatcher. Role-specific install steps live in head.py and
worker.py, whose setup() / teardown() functions are intentionally mirrored.
"""

import argparse
import getpass
import logging
import sys

from k8s.seed import head, util, worker


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

    if existing is not None:
        cfg["nodes"] = [entry if n["ip"] == args.ip else n for n in cfg["nodes"]]
    else:
        cfg["nodes"].append(entry)

    return cfg


def main() -> None:
    args = parse_args()
    cfg = util.load_config()
    cfg = validate_and_update(cfg, args)
    util.save_config(cfg)

    if args.type == "head":
        head.setup(args, cfg)
    else:
        worker.setup(args, cfg)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    main()
