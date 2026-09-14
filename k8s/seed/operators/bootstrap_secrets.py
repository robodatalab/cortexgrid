"""BootstrapSecrets — seeds Argo repo creds + the in-cluster route to the head server.

- argo-github-repo: Argo CD's repo credentials, needed before the External
  Secrets Operator exists to manage them.
- cortexgrid-head Service: selector-less, with an EndpointSlice pointing at the
  head secrets server (HeadServer) on the head's host, so pods and the External
  Secrets Operator reach it at a fixed in-cluster address.

Required deps (setup): github_token, node_ip.
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
        node_ip = deps["node_ip"]

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

        log.info("Bootstrap: in-cluster Service for the head secrets server...")
        head_service = textwrap.dedent(f"""\
            apiVersion: v1
            kind: Service
            metadata:
              name: {util.HEAD_SERVICE_NAME}
              namespace: {util.HEAD_SERVICE_NAMESPACE}
            spec:
              ports:
                - name: http
                  port: {util.HEAD_SERVER_PORT}
                  targetPort: {util.HEAD_SERVER_PORT}
                  protocol: TCP
            ---
            apiVersion: discovery.k8s.io/v1
            kind: EndpointSlice
            metadata:
              name: {util.HEAD_SERVICE_NAME}
              namespace: {util.HEAD_SERVICE_NAMESPACE}
              labels:
                kubernetes.io/service-name: {util.HEAD_SERVICE_NAME}
            addressType: IPv4
            ports:
              - name: http
                port: {util.HEAD_SERVER_PORT}
                protocol: TCP
            endpoints:
              - addresses:
                  - "{node_ip}"
        """)
        util.kubectl("apply", "-f", "-", input=head_service, capture=False)

    def teardown(self, deps: dict) -> None:
        util.kubectl(
            "-n", "argocd", "delete", "secret", "argo-github-repo",
            "--ignore-not-found", check=False, capture=False,
        )
        util.kubectl(
            "-n", util.HEAD_SERVICE_NAMESPACE, "delete",
            "service,endpointslice", util.HEAD_SERVICE_NAME,
            "--ignore-not-found", check=False, capture=False,
        )
        # --wait=false: don't block on finalizers (e.g. ESO CRDs). The node is
        # about to be destroyed anyway, so a stuck Terminating namespace doesn't
        # matter — only that this teardown step returns promptly.
        util.kubectl(
            "delete", "namespace", "external-secrets",
            "--ignore-not-found", "--wait=false", check=False, capture=False,
        )
