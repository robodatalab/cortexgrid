"""Head-role pipeline composition + entry points.

The pipeline is assembled once and used for both setup and teardown —
teardown just runs it in reverse.
"""

import argparse
import getpass
import logging

from dotenv import load_dotenv

from k8s.seed import util
from k8s.seed.operators import (
    AwaitArgo,
    BootstrapSecrets,
    ControlPlaneDetails,
    EnvSecrets,
    InstallPrereqs,
    K3sServer,
    Kubeconfig,
    LocalPath,
    NodeLabel,
    WorkerLabelsReconciler,
)
from k8s.seed.pipeline import Context, Pipeline


log = logging.getLogger("k8s.seed.head")


def build_pipeline() -> Pipeline:
    return Pipeline([
        InstallPrereqs(),
        K3sServer(),
        Kubeconfig(),
        AwaitArgo(),
        LocalPath(),
        EnvSecrets(),
        BootstrapSecrets(),
        ControlPlaneDetails(),
        NodeLabel(role="head", strict=True),
        WorkerLabelsReconciler(),
    ])


def setup(args: argparse.Namespace, cfg: dict) -> None:
    load_dotenv(util.ENV_FILE)
    sudo_pw = getpass.getpass("Node password (SSH + sudo): ")
    ctx = Context(args=args, cfg=cfg)
    with util.connect(args.ssh_user, args.ip, sudo_pw) as c:
        ctx.connection = c
        build_pipeline().setup(ctx)
    log.info(
        f"\nHead seeded.\n  Argo UI: http://{args.ip}:30080 (auth disabled, via Tailscale)"
    )


def teardown(args: argparse.Namespace, entry: dict) -> None:
    load_dotenv(util.ENV_FILE)
    sudo_pw = getpass.getpass("Node password (SSH + sudo): ")
    ctx = Context(args=args, entry=entry)
    with util.connect(args.ssh_user, args.ip, sudo_pw) as c:
        ctx.connection = c
        build_pipeline().teardown(ctx)
    log.info("Head teardown complete.")
