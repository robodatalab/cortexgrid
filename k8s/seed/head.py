"""Head-role pipeline construction.

Returns a Pipeline of operators. Each operator pulls what it needs out of the
deps dict at setup(deps) / teardown(deps) time — the caller (setup_node /
teardown_node) is responsible for populating deps.
"""

from k8s.seed import operators
from k8s.seed.pipeline import ConditionalOperator, Pipeline


def _on_onprem(deps: dict) -> bool:
    return deps["profile"] == "onprem"


def _on_aws(deps: dict) -> bool:
    return deps["profile"] == "aws"


def build() -> Pipeline:
    return Pipeline([
        operators.InstallPrereqs(),
        operators.TailscaleHostname(),
        # HeadServer, EnvSecrets and TerraformOutputs run before K3sServer so
        # every secret is in the head store by the time Argo starts syncing
        # the External Secrets Operator.
        operators.HeadServer(),
        operators.EnvSecrets(),
        ConditionalOperator(operators.TerraformOutputs(), _on_aws),
        operators.K3sServer(),
        operators.Kubeconfig(),
        operators.LocalPath(),
        ConditionalOperator(operators.PostgresCredentials(), _on_onprem),
        # MinioCredentials is the on-prem analog of TerraformOutputs: the
        # *infrastructure layer* publishes the MinIO/S3 endpoint URL +
        # credentials into the head secrets store so every consumer (mlflow,
        # cortexgrid library on a laptop, CI runners, ray workers) reads a
        # single profile-agnostic key and gets a tailnet-reachable URL.
        #
        # On-prem only because:
        #   - On AWS, TerraformOutputs already publishes the same keys
        #     (S3_ENDPOINT_URL = regional public S3 URL, S3_REGION,
        #     S3_BUCKET_NAME = the real S3 bucket, S3_ACCESS_KEY_ID/SECRET =
        #     the dgx IAM user). Running this operator on AWS would clobber
        #     them with values that point at a MinIO that isn't even deployed
        #     (the minio Argo App is excluded from the AWS bootstrap).
        #   - On-prem has no terraform layer; the seed pipeline IS the
        #     infrastructure layer. It's the only place that knows the head's
        #     tailscale IP, which is required to compose a NodePort URL that
        #     works for both in-cluster pods AND tailnet clients.
        ConditionalOperator(operators.MinioCredentials(), _on_onprem),
        operators.BootstrapSecrets(),
        operators.ControlPlaneDetails(),
        operators.NodeLabel(role="head", strict=True),
        operators.WorkerLabels(),
        operators.ArgoReady(),
    ])
