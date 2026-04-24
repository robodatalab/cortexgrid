"""Tear down a robolab cluster node.

Usage:
    uv run python k8s/seed/teardown-node.py --ip=X [--ssh-user=Z]

Looks the node up in infra-config.yaml, dispatches to the role's teardown()
flow (head.py or worker.py), and drops its entry from infra-config.yaml.
Each teardown() mirrors its setup() counterpart step-for-step.
"""

import argparse
import getpass
import logging
import sys

from k8s.seed import head, util, worker


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Tear down a robolab cluster node.")
    p.add_argument("--ip", required=True)
    p.add_argument("--ssh-user", default=None)
    args = p.parse_args()
    if args.ssh_user is None:
        args.ssh_user = util.ssh_user_for_ip(args.ip) or getpass.getuser()
    return args


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

    if entry["role"] == "head":
        head.teardown(args, entry)
    else:
        worker.teardown(args, entry)

    # Teardown has run — drop the node from infra-config.yaml.
    cfg["nodes"] = [n for n in cfg["nodes"] if n["ip"] != args.ip]
    util.save_config(cfg)
    logging.info(f"Removed {args.ip} from infra-config.yaml.")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    main()
