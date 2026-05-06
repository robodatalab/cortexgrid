"""Head-role pipeline construction.

Returns a Pipeline of operators. Each operator pulls what it needs out of the
deps dict at setup(deps) / teardown(deps) time — the caller (setup_node /
teardown_node) is responsible for populating deps.
"""

from k8s.seed import operators
from k8s.seed.pipeline import Pipeline


def build() -> Pipeline:
    return Pipeline([
        operators.InstallPrereqs(),
        operators.K3sServer(),
        operators.Kubeconfig(),
        operators.LocalPath(),
        operators.EnvSecrets(),
        operators.PlatformConfig(),
        operators.PostgresCredentials(),
        operators.BootstrapSecrets(),
        operators.ControlPlaneDetails(),
        operators.NodeLabel(role="head", strict=True),
        operators.WorkerLabels(),
        operators.ArgoReady(),
    ])
