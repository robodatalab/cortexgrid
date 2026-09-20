"""Unit tests run against fakes. Nothing here may touch a live cluster.

Every route out of this process starts at the head's secret store: the
MLflow tracking URI, the Ray job server URI, the S3 credentials and the
notes database URL are all `get_secret` calls answered over HTTP by the
head. `CORTEXGRID_HEAD_URL` is exported in a developer's shell, so a test
that forgot to patch a boundary did not fail — it reached the real
cluster and quietly passed, with production data in its assertions. CI
never saw it, because there the variable is unset and the call raises for
an unrelated reason.

The line drawn is this machine: a test that stands up its own server on
127.0.0.1 and talks to it is hermetic and allowed; anything addressed
elsewhere is the cluster.

The guards below are installed once, when discovery imports this package,
before any test module is loaded. A test that wants a boundary stubbed
patches it as usual: `patch` replaces the guard and puts it back
afterwards.
"""

from __future__ import annotations

from typing import Any, NoReturn
from urllib.parse import urlparse

import requests

import cortexgrid.secrets


def _refuse(what: str) -> NoReturn:
    raise AssertionError(
        f"A unit test reached for the live cluster ({what}). Patch the "
        "boundary the code under test goes through — e.g. "
        "cortexgrid.experiment.get_mlflow_tracking_uri, "
        "cortexgrid.ray_util.get_ray_job_submission_client, "
        "cortexgrid.s3_util.get_s3_client, or cortexgrid.infra.get_secret — "
        "and hand it a fake from tests/fakes.py."
    )


_THIS_MACHINE = {"localhost", "127.0.0.1", "::1", ""}


class _LocalOnlyHTTP:
    """Stands in for `requests` inside cortexgrid.secrets.

    A unit test that starts its own secrets server on 127.0.0.1 is still
    hermetic, so those calls go through to the real `requests`; anything
    addressed off this machine is the cluster and is refused.

    Named methods rather than __getattr__, so that patching one of them
    in a test that exercises the secrets client behaves exactly the way
    patching the real module does.
    """

    @staticmethod
    def _off_this_machine(url: str) -> bool:
        return (urlparse(url).hostname or "") not in _THIS_MACHINE

    def get(self, url: str, **kwargs: Any) -> Any:
        if self._off_this_machine(url):
            _refuse(f"GET {url}")
        return requests.get(url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> Any:
        if self._off_this_machine(url):
            _refuse(f"PUT {url}")
        return requests.put(url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> Any:
        if self._off_this_machine(url):
            _refuse(f"DELETE {url}")
        return requests.delete(url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> Any:
        if self._off_this_machine(url):
            _refuse(f"POST {url}")
        return requests.post(url, **kwargs)


cortexgrid.secrets.requests = _LocalOnlyHTTP()


# The Kubernetes API is the one path that does not go through the secret
# store: it reads a kubeconfig. Only the UI's dependency group installs
# the client, so guarding it is best effort.
try:
    from kubernetes import config as _kube_config
except ImportError:
    pass
else:
    _kube_config.load_incluster_config = lambda *a, **k: _refuse("kubeconfig")
    _kube_config.load_kube_config = lambda *a, **k: _refuse("kubeconfig")
