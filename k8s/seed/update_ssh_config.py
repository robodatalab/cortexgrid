"""Manage the `Host robolab-aws` block in ~/.ssh/config.

Default mode: poll `tailscale ip robolab-head` for up to TIMEOUT_S seconds, then
upsert a delimited block pointing at the discovered Tailscale IP.

`--remove`: strip the block.
"""

import argparse
import logging
import re
import socket
import sys
import time
from pathlib import Path


SSH_CONFIG = Path.home() / ".ssh" / "config"
START_MARKER = "# >>> robolab-aws (managed) >>>"
END_MARKER = "# <<< robolab-aws (managed) <<<"
HOSTNAME = "robolab-head"
TIMEOUT_S = 120
POLL_S = 2

log = logging.getLogger("k8s.seed.update_ssh_config")


def _poll_for_ip() -> str:
    log.info(f"Waiting for {HOSTNAME!r} to resolve via MagicDNS (up to {TIMEOUT_S}s)...")
    deadline = time.time() + TIMEOUT_S
    while time.time() < deadline:
        try:
            return socket.gethostbyname(HOSTNAME)
        except socket.gaierror:
            time.sleep(POLL_S)
    sys.exit(
        f"{HOSTNAME!r} did not resolve via MagicDNS within {TIMEOUT_S}s. "
        "Is cloud-init still running on the EC2? Is MagicDNS enabled on your tailnet?"
    )


def _strip_block(text: str) -> str:
    pattern = re.compile(
        rf"\n*{re.escape(START_MARKER)}.*?{re.escape(END_MARKER)}\n*",
        re.DOTALL,
    )
    return pattern.sub("\n", text)


def _upsert(ip: str) -> None:
    block = (
        f"{START_MARKER}\n"
        f"Host robolab-aws {ip}\n"
        f"    HostName {ip}\n"
        f"    User ubuntu\n"
        f"    IdentityFile ~/.ssh/id_rsa\n"
        f"{END_MARKER}\n"
    )
    SSH_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    text = SSH_CONFIG.read_text() if SSH_CONFIG.exists() else ""
    text = _strip_block(text).rstrip()
    text = (text + "\n\n" if text else "") + block
    SSH_CONFIG.write_text(text)
    log.info(f"~/.ssh/config: robolab-aws -> {ip}")


def _remove() -> None:
    if not SSH_CONFIG.exists():
        return
    text = SSH_CONFIG.read_text()
    new_text = _strip_block(text).rstrip()
    if new_text:
        new_text += "\n"
    if new_text != text:
        SSH_CONFIG.write_text(new_text)
        log.info("Removed robolab-aws entry from ~/.ssh/config")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remove", action="store_true", help="Strip the block")
    args = parser.parse_args()

    if args.remove:
        _remove()
    else:
        _upsert(_poll_for_ip())


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    main()
