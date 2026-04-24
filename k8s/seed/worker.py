"""Worker-role pipeline composition + entry points.

The pipeline is assembled once and used for both setup and teardown —
teardown just runs it in reverse.
"""

import argparse
import getpass
import logging

from dotenv import load_dotenv

from cortexflow.secrets import get_secret, list_secrets
from k8s.seed import util
from k8s.seed.operators import (
    DeferredJoin,
    InstallPrereqs,
    K3sAgentDirect,
    NodeLabel,
)
from k8s.seed.pipeline import Context, Pipeline


log = logging.getLogger("k8s.seed.worker")


def build_pipeline() -> Pipeline:
    return Pipeline([
        InstallPrereqs(),
        K3sAgentDirect(),
        DeferredJoin(),
        NodeLabel(role="worker", strict=False),
    ])


def _populate_head_credentials(ctx: Context) -> bool:
    """Fill ctx.head_token + ctx.head_ip from AWS SM if the head is ready.
    Returns True if head creds are present."""
    available = set(list_secrets())
    if util.SECRET_K3S_TOKEN not in available or util.SECRET_CONTROL_PLANE_IP not in available:
        return False
    ctx.head_token = get_secret(util.SECRET_K3S_TOKEN)
    ctx.head_ip = get_secret(util.SECRET_CONTROL_PLANE_IP)
    return True


def setup(args: argparse.Namespace, cfg: dict) -> None:
    load_dotenv(util.ENV_FILE)
    sudo_pw = getpass.getpass("Node password (SSH + sudo): ")
    ctx = Context(args=args, cfg=cfg)
    head_ready = _populate_head_credentials(ctx)

    with util.connect(args.ssh_user, args.ip, sudo_pw) as c:
        ctx.connection = c
        build_pipeline().setup(ctx)

    if head_ready:
        log.info(f"\nWorker seeded and joined cluster at {ctx.head_ip}.")
    else:
        log.info(
            f"\nWorker prerequisites installed and deferred-join timer active on {args.ip}. "
            f"The worker will join the cluster automatically within ~60s of the head being seeded."
        )


def teardown(args: argparse.Namespace, entry: dict) -> None:
    load_dotenv(util.ENV_FILE)
    sudo_pw = getpass.getpass("Node password (SSH + sudo): ")
    ctx = Context(args=args, entry=entry)
    with util.connect(args.ssh_user, args.ip, sudo_pw) as c:
        ctx.connection = c
        build_pipeline().teardown(ctx)
    log.info("Worker teardown complete.")
