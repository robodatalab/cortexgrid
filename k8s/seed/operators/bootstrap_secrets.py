"""BootstrapSecrets — seeds Argo repo creds + ESO SM creds into k8s.

Required deps (setup): github_token, sm_access_key_id, sm_secret_access_key.
Required deps (teardown): (none — deletion is by name).

Assumes the argocd namespace already exists — that's K3sServer's responsibility
(its setup waits for the bootstrap manifest to be applied before returning).
"""

import logging
import textwrap

from k8s.seed import util
from k8s.seed.pipeline import Operator


log = logging.getLogger("k8s.seed.operators.bootstrap_secrets")


class BootstrapSecrets(Operator):
    def setup(self, deps: dict) -> None:
        github_token = deps["github_token"]
        sm_access_key_id = deps["sm_access_key_id"]
        sm_secret_access_key = deps["sm_secret_access_key"]

        log.info("Bootstrap: GitHub repo credentials in argocd namespace...")
        github_secret = textwrap.dedent(f"""\
            apiVersion: v1
            kind: Secret
            metadata:
              name: argo-github-repo
              namespace: argocd
              labels:
                argocd.argoproj.io/secret-type: repository
            type: Opaque
            stringData:
              type: git
              url: https://github.com/robodatalab/cortexgrid.git
              username: x-access-token
              password: "{github_token}"
        """)
        util.kubectl("apply", "-f", "-", input=github_secret, capture=False)

        log.info("Seeding SM bootstrap credentials for ESO...")
        sm_bootstrap_secret = textwrap.dedent(f"""\
            apiVersion: v1
            kind: Namespace
            metadata:
              name: external-secrets
            ---
            apiVersion: v1
            kind: Secret
            metadata:
              name: sm-bootstrap-creds
              namespace: external-secrets
            type: Opaque
            stringData:
              SM_ACCESS_KEY_ID: "{sm_access_key_id}"
              SM_SECRET_ACCESS_KEY: "{sm_secret_access_key}"
        """)
        util.kubectl("apply", "-f", "-", input=sm_bootstrap_secret, capture=False)

    def teardown(self, deps: dict) -> None:
        util.kubectl(
            "-n", "argocd", "delete", "secret", "argo-github-repo",
            "--ignore-not-found", check=False, capture=False,
        )
        # --wait=false: don't block on finalizers (e.g. ESO CRDs). The node is
        # about to be destroyed anyway, so a stuck Terminating namespace doesn't
        # matter — only that this teardown step returns promptly.
        util.kubectl(
            "delete", "namespace", "external-secrets",
            "--ignore-not-found", "--wait=false", check=False, capture=False,
        )
