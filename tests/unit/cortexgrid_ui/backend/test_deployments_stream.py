from __future__ import annotations

import unittest
from unittest.mock import patch

from cortexgrid.model_serving import Deployment

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
            family="Qwen2",
            suffix="instruct",
            run_name="boogey-46",
            url="http://ray/r/Qwen2/instruct/boogey-46",
            phase="running",
            bundle_fingerprint="",
            replaced_bundle_fingerprint="",
        )
        with patch(
            "cortexgrid_ui.backend.streams.deployments_stream.list_deployed_models",
            return_value=[d],
        ):
            result = deployments_stream.poll_deployments(None)

        self.assertEqual(list(result.keys()), ["Qwen2/instruct/boogey-46"])
        self.assertIs(result["Qwen2/instruct/boogey-46"], d)

    def test_preserves_every_deployment_in_input(self) -> None:
        deployments = [
            Deployment(
                family="Qwen2",
                suffix="instruct",
                run_name="boogey-46",
                url="http://ray/r/Qwen2/instruct/boogey-46",
                phase="running",
                bundle_fingerprint="",
                replaced_bundle_fingerprint="",
            ),
            Deployment(
                family="DeepSeek3",
                suffix="chat",
                run_name="snake-12",
                url="http://ray/r/DeepSeek3/chat/snake-12",
                phase="failed",
                bundle_fingerprint="",
                replaced_bundle_fingerprint="",
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
