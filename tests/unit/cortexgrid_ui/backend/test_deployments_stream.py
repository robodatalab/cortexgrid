from __future__ import annotations

import unittest
from unittest.mock import patch

from cortexgrid.model_serving import Deployment, DeploymentKey

from cortexgrid_ui.backend.streams import deployments_stream


class TestPollDeployments(unittest.TestCase):
    def test_returns_empty_when_no_deployments(self) -> None:
        with patch(
            "cortexgrid_ui.backend.streams.deployments_stream.list_deployed_models",
            return_value=[],
        ):
            self.assertEqual(deployments_stream.poll_deployments(None), {})

    def test_keys_by_family_suffix_run_name(self) -> None:
        d = Deployment(
            key=DeploymentKey("Qwen2", "instruct", "boogey-46"),
            config={},
            url="http://ray/r/Qwen2/instruct/boogey-46",
            phase="running",
            bundle_fingerprint="",
            replaced_bundle_fingerprint="",
            experiment_name="",
        )
        with patch(
            "cortexgrid_ui.backend.streams.deployments_stream.list_deployed_models",
            return_value=[d],
        ):
            result = deployments_stream.poll_deployments(None)

        self.assertEqual(list(result.keys()), ["Qwen2/instruct/boogey-46"])
        self.assertIs(result["Qwen2/instruct/boogey-46"], d)

    def test_keys_a_deployment_given_a_config_by_its_config_fingerprint_too(
        self,
    ) -> None:
        d = Deployment(
            key=DeploymentKey("Qwen3", "8B", "imported", "5f0c1d2e3a4b"),
            config={"thinking": "false"},
            url="http://ray/r/Qwen3/8B/imported/5f0c1d2e3a4b",
            phase="running",
            bundle_fingerprint="",
            replaced_bundle_fingerprint="",
            experiment_name="",
        )
        with patch(
            "cortexgrid_ui.backend.streams.deployments_stream.list_deployed_models",
            return_value=[d],
        ):
            result = deployments_stream.poll_deployments(None)

        self.assertEqual(list(result.keys()), ["Qwen3/8B/imported/5f0c1d2e3a4b"])

    def test_preserves_every_deployment_in_input(self) -> None:
        deployments = [
            Deployment(
                key=DeploymentKey("Qwen2", "instruct", "boogey-46"),
                config={},
                url="http://ray/r/Qwen2/instruct/boogey-46",
                phase="running",
                bundle_fingerprint="",
                replaced_bundle_fingerprint="",
                experiment_name="",
            ),
            Deployment(
                key=DeploymentKey("DeepSeek3", "chat", "snake-12"),
                config={},
                url="http://ray/r/DeepSeek3/chat/snake-12",
                phase="failed",
                bundle_fingerprint="",
                replaced_bundle_fingerprint="",
                experiment_name="",
            ),
        ]
        with patch(
            "cortexgrid_ui.backend.streams.deployments_stream.list_deployed_models",
            return_value=deployments,
        ):
            result = deployments_stream.poll_deployments(None)

        self.assertEqual(
            sorted(result.keys()),
            ["DeepSeek3/chat/snake-12", "Qwen2/instruct/boogey-46"],
        )
        self.assertEqual(result["DeepSeek3/chat/snake-12"].phase, "failed")
        self.assertEqual(result["Qwen2/instruct/boogey-46"].phase, "running")


if __name__ == "__main__":
    unittest.main()
