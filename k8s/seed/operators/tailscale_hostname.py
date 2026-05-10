"""TailscaleHostname -- manages this cluster's identities on the tailnet.

Setup: names the head's Tailscale machine robolab-head.
    The terraform/tailnet-dns module CNAMEs every public subdomain
    (argo, mlflow, cortexflow, ...) at robolab-head.<tailnet>, so any
    head joining the tailnet must register under that name for DNS to
    resolve. AWS EC2 sets it via cloud-init; on-prem boxes default to
    their machine hostname.

    If some other device in the tailnet already holds the name (typically
    a previous head whose admin-panel entry wasn't cleaned up), the
    rename is skipped -- Tailscale would otherwise auto-suffix this
    machine to robolab-head-1 and break the DNS CNAMEs. The user has to
    resolve the conflict manually before re-running setup.

Teardown: deletes every device tagged `tag:k8s` from the tailnet.
    The k8s tailscale operator registers itself and a proxy per
    `tailscale.com/expose: true` Service (ray, etc.) with `tag:k8s`.
    Teardown wipes /var/lib/rancher (k3s state) and STORAGE_PATH/*
    (local-path PVCs), so the next setup's operator authenticates fresh
    and registers new devices. Without cleanup the stale admin-panel
    entries collide with the fresh registrations and get auto-suffixed
    to ray-1 / tailscale-operator-1.

    robolab-head is NOT touched on teardown -- it's the head's only
    tailnet entry point and the user reaches the host through it.

Required deps (setup): connection.
Required deps (teardown): env_file.
"""

import json
import logging

import requests  # type: ignore
from dotenv import dotenv_values

from k8s.seed.pipeline import Operator


log = logging.getLogger("k8s.seed.operators.tailscale_hostname")


_HOSTNAME = "robolab-head"
_K8S_TAG = "tag:k8s"
_API_BASE = "https://api.tailscale.com/api/v2"


def _tailscale_api_token(client_id: str, client_secret: str) -> str:
    resp = requests.post(
        f"{_API_BASE}/oauth/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _tailscale_list_devices(token: str) -> list[dict]:
    resp = requests.get(
        f"{_API_BASE}/tailnet/-/devices",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["devices"]


def _tailscale_delete_device(token: str, device_id: str) -> None:
    resp = requests.delete(
        f"{_API_BASE}/device/{device_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    resp.raise_for_status()


class TailscaleHostname(Operator):
    def setup(self, deps: dict) -> None:
        c = deps["connection"]
        status = json.loads(c.sudo("tailscale status --json", hide=True).stdout)

        self_name = status["Self"]["HostName"]
        if self_name == _HOSTNAME:
            log.info(f"Tailscale hostname is already {_HOSTNAME} on {c.host}.")
            return

        for peer in (status.get("Peer") or {}).values():
            if peer.get("HostName") == _HOSTNAME:
                log.warning(
                    f"Tailscale name '{_HOSTNAME}' is already held by another "
                    f"device (id={peer.get('ID')}, OS={peer.get('OS')}, "
                    f"online={peer.get('Online')}). Leaving this machine as "
                    f"'{self_name}' so we don't get auto-suffixed to "
                    f"{_HOSTNAME}-1. Remove the stale entry in the Tailscale "
                    f"admin panel and re-run setup."
                )
                return

        log.info(f"Setting Tailscale hostname to {_HOSTNAME} on {c.host}...")
        c.sudo(f"tailscale set --hostname={_HOSTNAME}", hide=True)

    def teardown(self, deps: dict) -> None:
        env = dotenv_values(deps["env_file"])
        client_id = env.get("TS_OAUTH_CLIENT_ID")
        client_secret = env.get("TS_OAUTH_SECRET")
        if not client_id or not client_secret:
            log.warning(
                "TS_OAUTH_CLIENT_ID/TS_OAUTH_SECRET missing from .env -- "
                "skipping tailnet device cleanup. Stale k8s-tagged devices "
                "may collide with the next setup."
            )
            return

        try:
            token = _tailscale_api_token(client_id, client_secret)
            devices = _tailscale_list_devices(token)
        except requests.RequestException as e:
            log.warning(
                f"Tailscale API unreachable ({e}) -- skipping device cleanup. "
                f"Stale k8s-tagged devices may collide with the next setup."
            )
            return

        targets = [d for d in devices if _K8S_TAG in (d.get("tags") or [])]
        if not targets:
            log.info("No tailnet devices tagged tag:k8s -- nothing to clean up.")
            return

        for device in targets:
            short_name = device["name"].split(".", 1)[0]
            try:
                _tailscale_delete_device(token, device["id"])
                log.info(f"Deleted tailnet device '{short_name}' (id={device['id']}).")
            except requests.RequestException as e:
                log.warning(
                    f"Failed to delete tailnet device '{short_name}' "
                    f"(id={device['id']}): {e}. Leaving for manual cleanup."
                )
