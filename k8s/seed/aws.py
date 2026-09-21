"""The AWS platform stack (terraform/platform): the EC2 head, its VPC, S3 and RDS.

`./cg add head --aws` applies it before seeding the head; `./cg del head`
destroys it after tearing the head down.
"""

import logging
import os
import subprocess
import sys

from dotenv import dotenv_values

from k8s.seed import update_ssh_config, util


PLATFORM_DIR = util.REPO_ROOT / "terraform" / "platform"

log = logging.getLogger("k8s.seed.aws")


def apply() -> str:
    """Apply the stack; returns the head's Tailscale IP once it joins the tailnet."""
    auth_key = dotenv_values(util.ENV_FILE).get("TAILSCALE_AUTH_KEY")
    if not auth_key:
        sys.exit(f"Error: TAILSCALE_AUTH_KEY missing from {util.ENV_FILE.name}.")
    _terraform("init")
    _terraform("apply", "-auto-approve", auth_key=auth_key)
    return update_ssh_config.upsert()


def destroy() -> None:
    _terraform("init")
    # destroy never reads the key, but the variable has no default.
    _terraform("destroy", "-auto-approve", auth_key="_")
    update_ssh_config.remove()


def _terraform(*args: str, auth_key: str | None = None) -> None:
    env = dict(os.environ)
    if auth_key is not None:
        env["TF_VAR_tailscale_auth_key"] = auth_key
    log.info(f"terraform {' '.join(args)} in {PLATFORM_DIR}...")
    subprocess.run(["terraform", *args], cwd=PLATFORM_DIR, env=env, check=True)
