"""./cg - add and delete the roles a robolab cluster is made of.

    ./cg add head --onprem --storage=PATH IP   seed the head on the host at IP
    ./cg add head --aws                        apply terraform/platform, then seed its EC2 host
    ./cg add worker --alias=NAME IP            join the host at IP as worker NAME; on the
                                               head's IP, the head runs Ray workers too
    ./cg del worker NAME                       remove worker NAME
    ./cg del head                              remove the head (on AWS, destroy the stack
                                               too); refused while workers remain
    ./cg del --all                             remove every worker, then the head
    ./cg restart                               tear every role down, then add it back

infra-config.yaml records the head and the workers by alias, each with the
setup steps it has `done`: a step is added once it completes and removed once
it is torn down, so after a failure the entry shows where it stopped. Adding a
role again with the same parameters re-runs its setup.

add and del take --ssh-user; it defaults to the user ~/.ssh/config gives the
IP, else the local user.
"""

import argparse
import getpass
import logging
import os
import sys
from typing import Callable, Literal

import requests  # type: ignore
from tqdm import tqdm

from cortexgrid.secrets import get_secret, list_secrets
from k8s.seed import aws, head, util, worker
from k8s.seed.pipeline import Pipeline


BOOTSTRAP_FILE = util.REPO_ROOT / "k8s" / "argocd.yaml"
# terraform/platform/head/cloud-init.yaml mounts the EC2 head's data volume here.
AWS_STORAGE_PATH = "/storage"

Direction = Literal["setup", "teardown"]

log = logging.getLogger("k8s.seed.cli")


# Commands


def add_head(ip: str, profile: str, storage: str, ssh_user: str | None) -> None:
    # Validate .env.head before touching infra-config.yaml.
    env = util.load_head_env()
    cfg = util.load_config()
    cfg["head"] = validated_head(cfg, ip, profile, storage)
    util.save_config(cfg)
    os.environ["CORTEXGRID_HEAD_URL"] = util.head_url(cfg)
    _setup_head(cfg, env, ssh_user)


def add_aws_head(ssh_user: str | None) -> None:
    """Apply the AWS stack, then add its EC2 host as the head."""
    # Refuse before terraform creates anything.
    util.load_head_env()
    _refuse_other_profile(util.load_config(), "aws")
    add_head(aws.apply(), "aws", AWS_STORAGE_PATH, ssh_user)


def add_worker(alias: str, ip: str, ssh_user: str | None) -> None:
    cfg = util.load_config()
    cfg["workers"][alias] = validated_worker(cfg, alias, ip)
    util.save_config(cfg)
    os.environ["CORTEXGRID_HEAD_URL"] = util.head_url(cfg)
    _setup_worker(cfg, alias, ssh_user)


def del_worker(alias: str, ssh_user: str | None) -> None:
    cfg = util.load_config()
    if alias not in cfg["workers"]:
        known = ", ".join(cfg["workers"]) or "none"
        sys.exit(f"Error: no worker {alias} in infra-config.yaml (workers: {known}).")
    os.environ["CORTEXGRID_HEAD_URL"] = util.head_url(cfg)
    _teardown_worker(cfg, alias, ssh_user)
    _forget(alias)


def del_head(ssh_user: str | None) -> None:
    cfg = util.load_config()
    entry = cfg.get("head")
    if entry is None:
        sys.exit("Error: infra-config.yaml has no head.")
    if cfg["workers"]:
        sys.exit(_workers_remain(cfg["workers"]))
    os.environ["CORTEXGRID_HEAD_URL"] = util.head_url(cfg)
    _teardown_head(cfg, ssh_user)
    if entry["profile"] == "aws":
        aws.destroy()
    _forget(None)


def del_all() -> None:
    cfg = util.load_config()
    if cfg.get("head") is None and not cfg["workers"]:
        sys.exit("Error: infra-config.yaml has no head and no workers.")
    for alias in list(cfg["workers"]):
        del_worker(alias, None)
    if cfg.get("head") is not None:
        del_head(None)


def restart() -> None:
    """Tear every role down, workers first, then add them back, head first.
    Leaves the AWS stack in place."""
    cfg = util.load_config()
    head_entry, workers = cfg.get("head"), dict(cfg["workers"])
    if head_entry is None and not workers:
        sys.exit("Error: infra-config.yaml has no head and no workers.")
    # Validate .env.head before tearing anything down.
    env = util.load_head_env() if head_entry is not None else {}
    os.environ["CORTEXGRID_HEAD_URL"] = util.head_url(cfg)
    log.info(
        "Restarting head=%s, workers=%s",
        head_entry["ip"] if head_entry else "(none)",
        list(workers) or "(none)",
    )

    for alias in workers:
        _teardown_worker(util.load_config(), alias, None)
        _forget(alias)
    if head_entry is not None:
        _teardown_head(util.load_config(), None)
        _forget(None)

        cfg = util.load_config()
        cfg["head"] = {**head_entry, "done": []}
        util.save_config(cfg)
        _setup_head(cfg, env, None)
    for alias, entry in workers.items():
        cfg = util.load_config()
        cfg["workers"][alias] = {"ip": entry["ip"], "done": []}
        util.save_config(cfg)
        _setup_worker(cfg, alias, None)


# infra-config.yaml


def validated_head(cfg: dict, ip: str, profile: str, storage: str) -> dict:
    """The entry `add head` records; exits if the cluster does not allow it."""
    _refuse_other_profile(cfg, profile)
    current = cfg.get("head")
    if current is not None:
        if current["ip"] != ip:
            sys.exit(
                f"Error: the cluster already has a head at {current['ip']}. "
                f"Run ./cg del head first."
            )
        if current["storage"] != storage:
            sys.exit(
                f"Error: head {ip} was added with --storage={current['storage']}. "
                f"Run ./cg del head first to change it."
            )
        return current
    on_ip = _workers_at(cfg, ip)
    if on_ip:
        sys.exit(f"Error: {ip} is worker {on_ip[0]}. Run ./cg del worker {on_ip[0]} first.")
    return {"ip": ip, "profile": profile, "storage": storage, "done": []}


def validated_worker(cfg: dict, alias: str, ip: str) -> dict:
    """The entry `add worker` records; exits if the cluster does not allow it."""
    current = cfg["workers"].get(alias)
    if current is not None and current["ip"] != ip:
        sys.exit(
            f"Error: worker {alias} is at {current['ip']}. "
            f"Run ./cg del worker {alias} first."
        )
    others = [a for a in _workers_at(cfg, ip) if a != alias]
    if others:
        sys.exit(f"Error: {ip} is already worker {others[0]}.")
    return current or {"ip": ip, "done": []}


def _refuse_other_profile(cfg: dict, profile: str) -> None:
    current = cfg.get("head")
    if current is not None and current["profile"] != profile:
        sys.exit(
            f"Error: the cluster already has a --{current['profile']} head at "
            f"{current['ip']}. Run ./cg del head first."
        )


def _workers_at(cfg: dict, ip: str) -> list[str]:
    return [alias for alias, entry in cfg["workers"].items() if entry["ip"] == ip]


def _workers_remain(workers: dict) -> str:
    return "\n".join([
        f"Error: the head still has workers: {', '.join(workers)}. Delete them first:",
        *(f"  ./cg del worker {alias}" for alias in workers),
        "or delete everything:",
        "  ./cg del --all",
    ])


def _entry(cfg: dict, alias: str | None) -> dict | None:
    """The head's entry (alias None) or worker `alias`'s."""
    return cfg.get("head") if alias is None else cfg["workers"].get(alias)


def _forget(alias: str | None) -> None:
    cfg = util.load_config()
    if alias is None:
        cfg.pop("head", None)
    else:
        cfg["workers"].pop(alias, None)
    util.save_config(cfg)


def record_step(alias: str | None, step: str, direction: Direction) -> None:
    """Add a completed setup step to the entry's `done`, or remove one torn down."""
    cfg = util.load_config()
    entry = _entry(cfg, alias)
    if entry is None:
        return
    done = entry["done"]
    if direction == "setup" and step not in done:
        done.append(step)
    elif direction == "teardown" and step in done:
        done.remove(step)
    util.save_config(cfg)


# Pipelines


def _setup_head(cfg: dict, env: dict[str, str], ssh_user: str | None) -> None:
    ip = cfg["head"]["ip"]
    user = _ssh_user(ip, ssh_user)
    with util.connect(user, ip, *_passwords(user, ip)) as c:
        deps = {**_head_deps(cfg, c), "github_token": env["GH_TOKEN"]}
        _run(head.build(), deps, "setup", None, f"Head setup {ip}")
    log.info(f"\nHead seeded.\n  Argo UI: http://{ip}:30080 (auth disabled, via Tailscale)")


def _teardown_head(cfg: dict, ssh_user: str | None) -> None:
    ip = cfg["head"]["ip"]
    user = _ssh_user(ip, ssh_user)
    with util.connect(user, ip, *_passwords(user, ip)) as c:
        _run(head.build(), _head_deps(cfg, c), "teardown", None, f"Head teardown {ip}")
    log.info("Head teardown complete.")


def _head_deps(cfg: dict, connection) -> dict:
    entry = cfg["head"]
    return {
        "connection": connection,
        "node_ip": entry["ip"],
        "bootstrap_file": BOOTSTRAP_FILE,
        "env_file": util.ENV_FILE,
        "storage_path": entry["storage"],
        # The workers joined to the head; the head's own worker role is not one.
        "workers": [w for w in cfg["workers"].values() if w["ip"] != entry["ip"]],
        "profile": entry["profile"],
    }


def _setup_worker(cfg: dict, alias: str, ssh_user: str | None) -> None:
    ip = cfg["workers"][alias]["ip"]
    user = _ssh_user(ip, ssh_user)
    if _on_head(cfg, ip):
        with util.connect(user, ip, *_passwords(user, ip)) as c:
            _run(
                worker.build_on_head(),
                {"connection": c, "node_ip": ip},
                "setup",
                alias,
                f"Worker {alias} setup on head {ip}",
            )
        log.info(f"\nHead {ip} is now also worker {alias}.")
        return

    head_token, head_ip = _lookup_head_creds()
    head_ready = head_token is not None and head_ip is not None
    deps: dict = {"node_ip": ip}
    if head_ready:
        deps["head_ip"] = head_ip
        deps["head_token"] = head_token
    else:
        deps["head_url"] = os.environ["CORTEXGRID_HEAD_URL"]
    with util.connect(user, ip, *_passwords(user, ip)) as c:
        _run(
            worker.build(mode="direct" if head_ready else "deferred"),
            {**deps, "connection": c},
            "setup",
            alias,
            f"Worker {alias} setup {ip}",
        )
    if head_ready:
        log.info(f"\nWorker {alias} seeded and joined the cluster at {head_ip}.")
    else:
        log.info(
            f"\nWorker {alias} prerequisites installed and deferred-join timer active "
            f"on {ip}. It joins the cluster within ~60s of the head being seeded."
        )


def _teardown_worker(cfg: dict, alias: str, ssh_user: str | None) -> None:
    ip = cfg["workers"][alias]["ip"]
    if _on_head(cfg, ip):
        # ComputeLabels.teardown only talks to the API server - no SSH connection.
        _run(
            worker.build_on_head(),
            {"node_ip": ip},
            "teardown",
            alias,
            f"Worker {alias} teardown on head {ip}",
        )
    else:
        user = _ssh_user(ip, ssh_user)
        with util.connect(user, ip, *_passwords(user, ip)) as c:
            # Mode is irrelevant for teardown - JoinCluster.teardown runs both cleanups.
            _run(
                worker.build(mode="direct"),
                {"connection": c, "node_ip": ip},
                "teardown",
                alias,
                f"Worker {alias} teardown {ip}",
            )
    log.info(f"Worker {alias} teardown complete.")


def _on_head(cfg: dict, ip: str) -> bool:
    return cfg.get("head") is not None and cfg["head"]["ip"] == ip


def _run(
    pipeline: Pipeline,
    deps: dict,
    direction: Direction,
    alias: str | None,
    desc: str,
) -> None:
    with tqdm(total=len(pipeline.steps(deps)), desc=desc) as bar:

        def on_step_done(step: str) -> None:
            record_step(alias, step, direction)
            bar.set_postfix_str(step)
            bar.update(1)

        pipeline.on_step_done = on_step_done
        if direction == "setup":
            pipeline.setup(deps)
        else:
            pipeline.teardown(deps)


def _lookup_head_creds() -> tuple[str | None, str | None]:
    try:
        available = set(list_secrets())
    except requests.RequestException:
        # Head server not up (or no head yet): the worker joins later.
        return None, None
    if util.SECRET_K3S_TOKEN in available and util.SECRET_CONTROL_PLANE_IP in available:
        return get_secret(util.SECRET_K3S_TOKEN), get_secret(
            util.SECRET_CONTROL_PLANE_IP
        )
    return None, None


def _ssh_user(ip: str, explicit: str | None) -> str:
    return explicit or util.ssh_user_for_ip(ip) or getpass.getuser()


def _passwords(user: str, ip: str) -> tuple[Callable[[], str], Callable[[], str]]:
    def ssh_pw() -> str:
        return getpass.getpass(f"SSH password for {user}@{ip}: ")

    def sudo_pw() -> str:
        return getpass.getpass(f"Sudo password for {user}@{ip}: ")

    return ssh_pw, sudo_pw


# Command line


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="./cg",
        description="Add and delete the roles of a robolab cluster.",
        epilog="See k8s/seed/cli.py.",
    )
    commands = p.add_subparsers(dest="command", required=True)

    add = commands.add_parser("add", help="add a role")
    add_roles = add.add_subparsers(dest="role", required=True)
    add_head_p = add_roles.add_parser("head", help="seed the head")
    profile = add_head_p.add_mutually_exclusive_group(required=True)
    profile.add_argument(
        "--onprem", dest="profile", action="store_const", const="onprem",
        help="on the host at IP",
    )
    profile.add_argument(
        "--aws", dest="profile", action="store_const", const="aws",
        help="apply terraform/platform, then seed its EC2 host",
    )
    add_head_p.add_argument(
        "--storage", help="--onprem: the head's disk for persistent volumes"
    )
    add_head_p.add_argument("ip", nargs="?", help="--onprem: the host's Tailscale IP")
    _ssh_user_arg(add_head_p)
    add_worker_p = add_roles.add_parser(
        "worker", help="join a worker; on the head's IP, the head runs Ray workers too"
    )
    add_worker_p.add_argument("--alias", required=True, help="the worker's name")
    add_worker_p.add_argument("ip", help="the host's Tailscale IP")
    _ssh_user_arg(add_worker_p)

    delete = commands.add_parser("del", help="delete a role")
    delete.add_argument(
        "--all", action="store_true", help="every worker, then the head"
    )
    del_roles = delete.add_subparsers(dest="role")
    del_head_p = del_roles.add_parser(
        "head", help="the head, once it has no workers (on AWS, with the stack)"
    )
    _ssh_user_arg(del_head_p)
    del_worker_p = del_roles.add_parser("worker", help="a worker")
    del_worker_p.add_argument("alias", help="the worker's name")
    _ssh_user_arg(del_worker_p)

    commands.add_parser("restart", help="tear every role down, then add it back")
    return p


def _ssh_user_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument("--ssh-user", default=None)


def main(argv: list[str] | None = None) -> None:
    p = parser()
    args = p.parse_args(argv)
    if args.command == "add" and args.role == "head":
        if args.profile == "aws":
            if args.ip or args.storage:
                p.error("add head --aws takes no IP or --storage: terraform creates the host")
            add_aws_head(args.ssh_user)
        else:
            if not (args.ip and args.storage):
                p.error("add head --onprem takes --storage=PATH and the host's IP")
            add_head(args.ip, "onprem", args.storage, args.ssh_user)
    elif args.command == "add":
        add_worker(args.alias, args.ip, args.ssh_user)
    elif args.command == "del":
        if args.all == (args.role is not None):
            p.error("del takes one of: head, worker NAME, --all")
        if args.all:
            del_all()
        elif args.role == "head":
            del_head(args.ssh_user)
        else:
            del_worker(args.alias, args.ssh_user)
    else:
        restart()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    main()
