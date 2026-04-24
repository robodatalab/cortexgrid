"""BootstrapSecrets — seeds Argo repo creds + ESO AWS creds into k8s on setup.

Setup-only: the k8s Secrets themselves die with k3s via K3sServer.teardown.
"""

import logging
import os
import textwrap

from k8s.seed import util
from k8s.seed.pipeline import Context, Operator


log = logging.getLogger("k8s.seed.operators.bootstrap_secrets")


class BootstrapSecrets(Operator):
    def setup(self, ctx: Context) -> None:
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
              url: https://github.com/paksas/robolab-infra.git
              username: x-access-token
              password: "{os.environ["GH_TOKEN"]}"
        """)
        util.kubectl("apply", "-f", "-", input=github_secret, capture=False)

        log.info("Seeding AWS bootstrap credentials for ESO...")
        aws_secret = textwrap.dedent(f"""\
            apiVersion: v1
            kind: Namespace
            metadata:
              name: external-secrets
            ---
            apiVersion: v1
            kind: Secret
            metadata:
              name: aws-bootstrap-creds
              namespace: external-secrets
            type: Opaque
            stringData:
              AWS_ACCESS_KEY_ID: "{os.environ["AWS_ACCESS_KEY_ID"]}"
              AWS_SECRET_ACCESS_KEY: "{os.environ["AWS_SECRET_ACCESS_KEY"]}"
        """)
        util.kubectl("apply", "-f", "-", input=aws_secret, capture=False)

    def teardown(self, ctx: Context) -> None:
        pass  # k8s Secrets die with k3s via K3sServer.teardown
