from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest
from typing import Any
from unittest.mock import patch

from fastapi import FastAPI
from ray import serve as ray_serve
from ray._private.runtime_env.packaging import unzip_package
from ray.serve.schema import ServeApplicationSchema, ServeDeploySchema

from cortexgrid._bundle import BundleDesc
from cortexgrid.model_serving import (
    BundleMetadata,
    DeploymentKey,
    ModelDeployFailed,
    ModelNotDeployed,
    ModelRequirements,
    ServeBundle,
    vram_tiers,
    build_bundle,
    bundle_class,
    bundle_fingerprint_from_url,
    deploy_model,
    list_deployed_models,
    metadata_to_tags,
    model_replica_placements,
    model_serving_messages,
    model_serving_status,
    observe_deployments,
    redeploy_model,
    requirements_from_tags,
    requirements_to_tags,
    undeploy_model,
    wait_for_model_serving,
)
from cortexgrid.model_serving.application_spec import build_application_spec
from cortexgrid.model_serving.placement import _placement_options
from cortexgrid.model_serving.registry_tags import load_deploy_metadata
from cortexgrid.model_serving.status import _phase

from tests.fakes import FakeState


_FAKE_META = BundleMetadata(
    bundle_url="s3://bucket/stub.zip",
    class_import_path="stub:Stub",
)

_GPU_REQUIREMENTS = ModelRequirements(num_gpus=1, ram_gb=16.0, vram_gb=24.0)

_QWEN2_DEPLOYMENT = DeploymentKey("Qwen2", "instruct", "boogey-46")
_FAM_DEPLOYMENT = DeploymentKey("fam", "suf", "run")

_OLD_FINGERPRINT = "a" * 64
_NEW_FINGERPRINT = "b" * 64


def _bundle_with_fingerprint(fingerprint: str) -> BundleMetadata:
    return BundleMetadata(
        bundle_url=f"s3://bucket/serve-bundles/boogey-46/Qwen2__instruct/{fingerprint}.zip",
        class_import_path="stub:Stub",
        fingerprint=fingerprint,
    )

# The GPU size classes a cluster reports: a 12 GiB card and a 128 GiB one, the
# shape the smallest-device placement exists for.
_SMALL_TIER = 12282
_BIG_TIER = 131072
_TIERS = [_SMALL_TIER, _BIG_TIER]


class FakeServeState:
    """In-memory stand-in for Ray Serve's declarative app registry."""

    def __init__(self) -> None:
        self.apps: dict[str, dict[str, Any]] = {}
        self.status: str = "RUNNING"
        self.message: str = ""

    def get_details(self) -> dict[str, Any]:
        return {
            "applications": {
                name: {
                    "status": self.status,
                    "message": self.message,
                    "deployed_app_config": spec,
                }
                for name, spec in self.apps.items()
            }
        }

    def put(self, applications: list[dict[str, Any]]) -> None:
        self.apps = {a["name"]: a for a in applications}


def _seed_saved_model(
    records: FakeState,
    family: str,
    suffix: str,
    run_name: str,
    requirements: ModelRequirements = _GPU_REQUIREMENTS,
    bundle: BundleMetadata = _FAKE_META,
) -> None:
    """Register the model as `save_model` does: its bundle and requirements as
    tags on the registry entry."""
    records.seed_model(
        family,
        suffix,
        run_name,
        "s3://bucket/weights",
        {**metadata_to_tags(bundle), **requirements_to_tags(requirements)},
    )


def _seed_deployment(
    records: FakeState, family: str, suffix: str, run_name: str, **fields: Any
) -> None:
    """Record a deployment as `deploy_model` leaves one it saw RUNNING, with
    `fields` overriding what it holds."""
    records.put(
        "deployments",
        family,
        suffix,
        run_name,
        body={
            "spec": {"name": f"{family}__{suffix}__{run_name}"},
            "tiers": _TIERS,
            "url": f"http://ray:30000/r/{family}/{suffix}/{run_name}",
            "phase": "running",
            # An app with no message is recorded with its raw status.
            "message": "RUNNING",
            "replicas": [],
            "replaced_bundle_fingerprint": "",
            **fields,
        },
    )


class TestModelServing(unittest.TestCase):
    def setUp(self) -> None:
        self.state = FakeServeState()
        self.records = FakeState().install(self)
        _seed_saved_model(self.records, "Qwen2", "instruct", "boogey-46")
        patches = [
            patch(
                "cortexgrid.model_serving.lifecycle.get_serve_details",
                side_effect=self.state.get_details,
            ),
            patch(
                "cortexgrid.model_serving.status.get_serve_details",
                side_effect=self.state.get_details,
            ),
            patch(
                "cortexgrid.model_serving.lifecycle.put_serve_applications",
                side_effect=self.state.put,
            ),
            patch(
                "cortexgrid.model_serving.lifecycle.get_ray_serve_uri",
                return_value="http://ray:30000",
            ),
            patch(
                "cortexgrid.model_serving.lifecycle.vram_tiers",
                return_value=_TIERS,
            ),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        build_spec = patch(
            "cortexgrid.model_serving.lifecycle.build_application_spec",
            wraps=build_application_spec,
        )
        self.build_spec = build_spec.start()
        self.addCleanup(build_spec.stop)

    def _record(self) -> dict[str, Any]:
        return self.records.deployments[("Qwen2", "instruct", "boogey-46", "")]

    def test_deploy_builds_the_spec_from_the_stored_requirements(self) -> None:
        deploy_model("Qwen2", "instruct", "boogey-46", num_replicas=3)

        self.build_spec.assert_called_once_with(
            _QWEN2_DEPLOYMENT,
            _FAKE_META,
            _GPU_REQUIREMENTS,
            3,
            _TIERS,
        )

    def test_deploy_runs_one_replica_by_default(self) -> None:
        deploy_model("Qwen2", "instruct", "boogey-46")

        self.assertEqual(self.build_spec.call_args.args[-2], 1)

    def test_deploy_records_the_spec_it_put_and_the_tiers_it_built_against(
        self,
    ) -> None:
        deploy_model("Qwen2", "instruct", "boogey-46")

        self.assertEqual(
            self._record()["spec"], self.state.apps["Qwen2__instruct__boogey-46"]
        )
        self.assertEqual(self._record()["tiers"], _TIERS)

    def test_deployed_model_appears_in_listings(self) -> None:
        deploy_model("Qwen2", "instruct", "boogey-46")

        listed = list_deployed_models()

        self.assertEqual(
            [(d.key.family, d.key.suffix, d.key.run_name) for d in listed],
            [("Qwen2", "instruct", "boogey-46")],
        )

    def test_undeployed_model_disappears_from_listings(self) -> None:
        deploy_model("Qwen2", "instruct", "boogey-46")
        undeploy_model(_QWEN2_DEPLOYMENT)

        self.assertEqual(list_deployed_models(), [])

    def test_undeploy_drops_the_deployment_record(self) -> None:
        deploy_model("Qwen2", "instruct", "boogey-46")
        undeploy_model(_QWEN2_DEPLOYMENT)

        self.assertEqual(self.records.deployments, {})

    def test_deployment_url_combines_serve_uri_and_route(self) -> None:
        d = deploy_model("Qwen2", "instruct", "boogey-46")

        self.assertEqual(d.url, "http://ray:30000/r/Qwen2/instruct/boogey-46")

    def test_serve_apps_without_a_deployment_record_are_not_listed(self) -> None:
        deploy_model("Qwen2", "instruct", "boogey-46")
        self.state.apps["unrelated-app"] = {"name": "unrelated-app"}
        self.state.apps["only__two"] = {"name": "only__two"}
        observe_deployments()

        listed = list_deployed_models()

        self.assertEqual([d.key.family for d in listed], ["Qwen2"])

    def test_model_whose_app_is_gone_is_not_listed(self) -> None:
        deploy_model("Qwen2", "instruct", "boogey-46")
        self.state.apps.clear()
        observe_deployments()

        self.assertEqual(list_deployed_models(), [])

    def test_list_returns_empty_when_nothing_deployed(self) -> None:
        self.assertEqual(list_deployed_models(), [])

    def test_deployment_runs_its_bundle_until_redeployed_after_a_reimport(
        self,
    ) -> None:
        _seed_saved_model(
            self.records, "Qwen2", "instruct", "boogey-46",
            bundle=_bundle_with_fingerprint(_OLD_FINGERPRINT),
        )
        deploy_model("Qwen2", "instruct", "boogey-46")
        _seed_saved_model(
            self.records, "Qwen2", "instruct", "boogey-46",
            bundle=_bundle_with_fingerprint(_NEW_FINGERPRINT),
        )

        running_before_redeploy = [d.bundle_fingerprint for d in list_deployed_models()]
        redeployed = deploy_model("Qwen2", "instruct", "boogey-46")

        self.assertEqual(running_before_redeploy, [_OLD_FINGERPRINT])
        self.assertEqual(redeployed.bundle_fingerprint, _NEW_FINGERPRINT)
        self.assertEqual(
            [d.bundle_fingerprint for d in list_deployed_models()], [_NEW_FINGERPRINT]
        )

    def test_deployment_of_a_bundle_saved_before_fingerprints_has_none(
        self,
    ) -> None:
        deploy_model("Qwen2", "instruct", "boogey-46")

        self.assertEqual([d.bundle_fingerprint for d in list_deployed_models()], [""])

    def test_redeploy_runs_the_registry_code_on_as_many_replicas_as_before(
        self,
    ) -> None:
        _seed_saved_model(
            self.records, "Qwen2", "instruct", "boogey-46",
            bundle=_bundle_with_fingerprint(_OLD_FINGERPRINT),
        )
        deploy_model("Qwen2", "instruct", "boogey-46", num_replicas=3)
        _seed_saved_model(
            self.records, "Qwen2", "instruct", "boogey-46",
            bundle=_bundle_with_fingerprint(_NEW_FINGERPRINT),
        )

        redeployed = redeploy_model(_QWEN2_DEPLOYMENT)

        self.assertEqual(redeployed.bundle_fingerprint, _NEW_FINGERPRINT)
        self.assertEqual(self._record()["spec"]["args"]["num_replicas"], 3)

    def test_redeploy_of_a_model_that_is_not_deployed_raises(self) -> None:
        with self.assertRaises(ModelNotDeployed):
            redeploy_model(_QWEN2_DEPLOYMENT)

    def _deploy_bundle(self, fingerprint: str) -> None:
        _seed_saved_model(
            self.records, "Qwen2", "instruct", "boogey-46",
            bundle=_bundle_with_fingerprint(fingerprint),
        )
        deploy_model("Qwen2", "instruct", "boogey-46")

    def _replaced_bundle_fingerprints(self) -> list[str]:
        return [d.replaced_bundle_fingerprint for d in list_deployed_models()]

    def test_redeploy_with_new_code_records_the_code_it_replaces(self) -> None:
        self._deploy_bundle(_OLD_FINGERPRINT)
        self.state.status = "DEPLOYING"

        self._deploy_bundle(_NEW_FINGERPRINT)

        self.assertEqual(self._replaced_bundle_fingerprints(), [_OLD_FINGERPRINT])

    def test_replaced_code_is_kept_while_the_new_code_is_deploying(self) -> None:
        self._deploy_bundle(_OLD_FINGERPRINT)
        self.state.status = "DEPLOYING"
        self._deploy_bundle(_NEW_FINGERPRINT)

        observe_deployments()

        self.assertEqual(self._replaced_bundle_fingerprints(), [_OLD_FINGERPRINT])

    def test_replaced_code_is_forgotten_once_the_new_code_runs(self) -> None:
        self._deploy_bundle(_OLD_FINGERPRINT)
        self.state.status = "DEPLOYING"
        self._deploy_bundle(_NEW_FINGERPRINT)
        self.state.status = "RUNNING"

        observe_deployments()

        self.assertEqual(self._replaced_bundle_fingerprints(), [""])

    def test_redeploy_during_a_rollout_keeps_the_code_the_rollout_started_from(
        self,
    ) -> None:
        self._deploy_bundle(_OLD_FINGERPRINT)
        self.state.status = "DEPLOYING"
        self._deploy_bundle(_NEW_FINGERPRINT)

        self._deploy_bundle("c" * 64)

        self.assertEqual(self._replaced_bundle_fingerprints(), [_OLD_FINGERPRINT])

    def test_each_config_of_a_model_is_its_own_deployment(self) -> None:
        thinking = deploy_model(
            "Qwen2", "instruct", "boogey-46", config={"thinking": "true"}
        )
        not_thinking = deploy_model(
            "Qwen2", "instruct", "boogey-46", config={"thinking": "false"}
        )

        self.assertNotEqual(thinking.key, not_thinking.key)
        self.assertNotEqual(thinking.url, not_thinking.url)
        self.assertEqual(
            sorted(d.config["thinking"] for d in list_deployed_models()),
            ["false", "true"],
        )
        self.assertEqual(len(self.state.apps), 2)

    def test_a_deployment_without_config_keeps_the_models_app_name_and_route(
        self,
    ) -> None:
        deployment = deploy_model("Qwen2", "instruct", "boogey-46")

        self.assertEqual(list(self.state.apps), ["Qwen2__instruct__boogey-46"])
        self.assertEqual(deployment.url, "http://ray:30000/r/Qwen2/instruct/boogey-46")

    def test_deploying_new_code_updates_only_the_deployment_with_that_config(
        self,
    ) -> None:
        self._deploy_bundle(_OLD_FINGERPRINT)
        deploy_model("Qwen2", "instruct", "boogey-46", config={"thinking": "true"})
        deploy_model("Qwen2", "instruct", "boogey-46", config={"thinking": "false"})
        _seed_saved_model(
            self.records, "Qwen2", "instruct", "boogey-46",
            bundle=_bundle_with_fingerprint(_NEW_FINGERPRINT),
        )

        deploy_model("Qwen2", "instruct", "boogey-46", config={"thinking": "true"})

        fingerprint_by_thinking = {
            d.config.get("thinking"): d.bundle_fingerprint
            for d in list_deployed_models()
        }
        self.assertEqual(
            fingerprint_by_thinking,
            {None: _OLD_FINGERPRINT, "true": _NEW_FINGERPRINT, "false": _OLD_FINGERPRINT},
        )

    def test_redeploy_keeps_the_deployments_config(self) -> None:
        deployment = deploy_model(
            "Qwen2", "instruct", "boogey-46", config={"thinking": "false"}
        )

        redeployed = redeploy_model(deployment.key)

        self.assertEqual(redeployed.key, deployment.key)
        self.assertEqual(redeployed.config, {"thinking": "false"})

    def test_undeploy_removes_only_the_deployment_it_names(self) -> None:
        thinking = deploy_model(
            "Qwen2", "instruct", "boogey-46", config={"thinking": "true"}
        )
        deploy_model("Qwen2", "instruct", "boogey-46", config={"thinking": "false"})

        undeploy_model(thinking.key)

        self.assertEqual(
            [d.config for d in list_deployed_models()], [{"thinking": "false"}]
        )

    def test_first_deploy_replaces_no_code(self) -> None:
        self.state.status = "DEPLOYING"

        self._deploy_bundle(_NEW_FINGERPRINT)

        self.assertEqual(self._replaced_bundle_fingerprints(), [""])

    def test_redeploying_same_triple_replaces_prior_spec(self) -> None:
        deploy_model("Qwen2", "instruct", "boogey-46")
        deploy_model("Qwen2", "instruct", "boogey-46")

        self.assertEqual(len(self.state.apps), 1)

    def test_deploy_model_with_wait_returns_deployment_pointing_at_app_url(self) -> None:
        self.state.status = "RUNNING"

        d = deploy_model("Qwen2", "instruct", "boogey-46", wait=True)

        self.assertEqual(d.url, "http://ray:30000/r/Qwen2/instruct/boogey-46")

    def test_deploy_model_with_wait_raises_on_deploy_failed(self) -> None:
        self.state.status = "DEPLOY_FAILED"
        self.state.message = "replica died on import"

        with patch("cortexgrid.model_serving.lifecycle.time.sleep"):
            with self.assertRaises(ModelDeployFailed) as ctx:
                deploy_model("Qwen2", "instruct", "boogey-46", wait=True)

        self.assertIn("DEPLOY_FAILED", str(ctx.exception))
        self.assertIn("replica died on import", str(ctx.exception))

    def test_redeploying_a_failed_app_removes_it_before_putting_it_back(self) -> None:
        name = "Qwen2__instruct__boogey-46"
        self.state.apps[name] = {"name": name}
        self.state.status = "DEPLOY_FAILED"
        puts: list[list[str]] = []

        def put(applications: list[dict[str, Any]]) -> None:
            puts.append([a["name"] for a in applications])
            self.state.put(applications)

        with patch(
            "cortexgrid.model_serving.lifecycle.put_serve_applications", side_effect=put
        ):
            deploy_model("Qwen2", "instruct", "boogey-46")

        self.assertEqual(puts, [[], [name]])

    def test_redeploying_waits_until_a_deleting_app_is_gone(self) -> None:
        name = "Qwen2__instruct__boogey-46"
        self.state.apps[name] = {"name": name}
        self.state.status = "DELETING"
        polls_until_gone = 3
        still_deleting_at_put: list[bool] = []

        def get_details() -> dict[str, Any]:
            nonlocal polls_until_gone
            polls_until_gone -= 1
            if polls_until_gone == 0:
                self.state.apps.pop(name)
            return self.state.get_details()

        def put(applications: list[dict[str, Any]]) -> None:
            still_deleting_at_put.append(polls_until_gone > 0)
            self.state.put(applications)

        with (
            patch("cortexgrid.model_serving.lifecycle.time.sleep"),
            patch(
                "cortexgrid.model_serving.lifecycle.get_serve_details", side_effect=get_details
            ),
            patch(
                "cortexgrid.model_serving.lifecycle.put_serve_applications", side_effect=put
            ),
        ):
            deploy_model("Qwen2", "instruct", "boogey-46")

        self.assertEqual(still_deleting_at_put, [False])

    def test_redeploying_times_out_while_the_old_app_is_still_deleting(self) -> None:
        name = "Qwen2__instruct__boogey-46"
        self.state.apps[name] = {"name": name}
        self.state.status = "DELETING"

        with (
            patch("cortexgrid.model_serving.lifecycle.time.sleep"),
            self.assertRaises(TimeoutError),
        ):
            deploy_model("Qwen2", "instruct", "boogey-46", timeout=0.05)

        self.assertEqual(self.state.apps, {name: {"name": name}})

    def test_redeploying_a_deploying_app_with_a_changed_spec_puts_it_without_removing_it(
        self,
    ) -> None:
        name = "Qwen2__instruct__boogey-46"
        self.state.apps[name] = {"name": name}
        self.state.status = "DEPLOYING"
        puts: list[list[str]] = []

        def put(applications: list[dict[str, Any]]) -> None:
            puts.append([a["name"] for a in applications])
            self.state.put(applications)

        with patch(
            "cortexgrid.model_serving.lifecycle.put_serve_applications", side_effect=put
        ):
            deploy_model("Qwen2", "instruct", "boogey-46")

        self.assertEqual(puts, [[name]])

    def test_redeploying_an_unchanged_spec_without_a_record_skips_the_put(
        self,
    ) -> None:
        deploy_model("Qwen2", "instruct", "boogey-46")
        # A model deployed before deployment records existed has none, so the
        # redeploy asks Ray whether it already has the spec.
        self.records.deployments.clear()
        puts: list[list[str]] = []

        with patch(
            "cortexgrid.model_serving.lifecycle.put_serve_applications",
            side_effect=lambda apps: puts.append([a["name"] for a in apps]),
        ):
            deploy_model("Qwen2", "instruct", "boogey-46")

        self.assertEqual(puts, [])

    def test_redeploying_an_unchanged_spec_mid_build_leaves_the_build_alone(
        self,
    ) -> None:
        # A PUT arriving while Ray is building the app cancels that build and
        # restarts it, whatever the spec says, so an unchanged spec must not
        # be restated.
        self.state.status = "DEPLOYING"
        deploy_model("Qwen2", "instruct", "boogey-46")
        puts: list[list[str]] = []

        with patch(
            "cortexgrid.model_serving.lifecycle.put_serve_applications",
            side_effect=lambda apps: puts.append([a["name"] for a in apps]),
        ):
            deploy_model("Qwen2", "instruct", "boogey-46")

        self.assertEqual(puts, [])

    def test_redeploying_an_unchanged_spec_makes_no_ray_calls(self) -> None:
        # The record shows the model live with this spec, so the redeploy is
        # answered from it: not even the GPU tiers are read.
        deploy_model("Qwen2", "instruct", "boogey-46")

        with (
            patch("cortexgrid.model_serving.lifecycle.vram_tiers") as tiers,
            patch("cortexgrid.model_serving.lifecycle.get_serve_details") as details,
            patch("cortexgrid.model_serving.lifecycle.put_serve_applications") as put,
        ):
            deploy_model("Qwen2", "instruct", "boogey-46")

        tiers.assert_not_called()
        details.assert_not_called()
        put.assert_not_called()

    def test_redeploying_an_unchanged_spec_reports_the_recorded_phase(self) -> None:
        # The phase is what the control plane last observed, not a fresh read.
        self.state.status = "DEPLOYING"
        deploy_model("Qwen2", "instruct", "boogey-46")
        self.state.status = "RUNNING"

        d = deploy_model("Qwen2", "instruct", "boogey-46")

        self.assertEqual(d.phase, "deploying")

    def test_redeploying_an_unchanged_spec_with_wait_reports_it_running(
        self,
    ) -> None:
        self.state.status = "DEPLOYING"
        deploy_model("Qwen2", "instruct", "boogey-46")
        self.state.status = "RUNNING"

        d = deploy_model("Qwen2", "instruct", "boogey-46", wait=True)

        self.assertEqual(d.phase, "running")

    def test_redeploying_with_changed_requirements_puts_the_new_spec(self) -> None:
        name = "Qwen2__instruct__boogey-46"
        deploy_model("Qwen2", "instruct", "boogey-46")
        # Re-saved asking for twice the RAM.
        _seed_saved_model(
            self.records,
            "Qwen2",
            "instruct",
            "boogey-46",
            ModelRequirements(num_gpus=1, ram_gb=32.0, vram_gb=24.0),
        )
        puts: list[list[str]] = []

        def put(applications: list[dict[str, Any]]) -> None:
            puts.append([a["name"] for a in applications])
            self.state.put(applications)

        with patch("cortexgrid.model_serving.lifecycle.put_serve_applications", side_effect=put):
            deploy_model("Qwen2", "instruct", "boogey-46")

        self.assertEqual(puts, [[name]])
        self.assertEqual(
            self._record()["spec"]["args"]["ray_actor_options"]["memory"],
            32 * 1024**3,
        )

    def test_redeploying_with_more_replicas_puts_the_new_spec(self) -> None:
        name = "Qwen2__instruct__boogey-46"
        deploy_model("Qwen2", "instruct", "boogey-46", num_replicas=1)
        puts: list[list[str]] = []

        def put(applications: list[dict[str, Any]]) -> None:
            puts.append([a["name"] for a in applications])
            self.state.put(applications)

        with patch("cortexgrid.model_serving.lifecycle.put_serve_applications", side_effect=put):
            deploy_model("Qwen2", "instruct", "boogey-46", num_replicas=2)

        self.assertEqual(puts, [[name]])
        self.assertEqual(self._record()["spec"]["args"]["num_replicas"], 2)

    def test_redeploying_over_a_failed_record_removes_the_app_and_puts_it_back(
        self,
    ) -> None:
        name = "Qwen2__instruct__boogey-46"
        deploy_model("Qwen2", "instruct", "boogey-46")
        self.state.status = "DEPLOY_FAILED"
        observe_deployments()
        puts: list[list[str]] = []

        def put(applications: list[dict[str, Any]]) -> None:
            puts.append([a["name"] for a in applications])
            self.state.put(applications)
            # Ray starts the app afresh.
            self.state.status = "DEPLOYING"

        with patch("cortexgrid.model_serving.lifecycle.put_serve_applications", side_effect=put):
            deploy_model("Qwen2", "instruct", "boogey-46")

        self.assertEqual(puts, [[], [name]])
        self.assertEqual(self._record()["phase"], "deploying")



class TestWaitForModelServing(unittest.TestCase):
    def setUp(self) -> None:
        self.state = FakeServeState()
        self.state.apps["Qwen2__instruct__boogey-46"] = {
            "name": "Qwen2__instruct__boogey-46"
        }
        patches = [
            patch(
                "cortexgrid.model_serving.lifecycle.get_serve_details",
                side_effect=self.state.get_details,
            ),
            patch("cortexgrid.model_serving.lifecycle.time.sleep"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_times_out_when_status_never_reaches_running(self) -> None:
        self.state.status = "DEPLOYING"
        self.state.message = "still booting"

        with self.assertRaises(TimeoutError) as ctx:
            wait_for_model_serving(_QWEN2_DEPLOYMENT, timeout=0.05)

        self.assertIn("DEPLOYING", str(ctx.exception))
        self.assertIn("still booting", str(ctx.exception))

    def test_treats_unhealthy_as_transient_until_timeout(self) -> None:
        self.state.status = "UNHEALTHY"

        with self.assertRaises(TimeoutError):
            wait_for_model_serving(_QWEN2_DEPLOYMENT, timeout=0.05)

    def test_finite_timeout_elapsing_raises_timeout_error(self) -> None:
        self.state.status = "DEPLOYING"

        with self.assertRaises(TimeoutError):
            wait_for_model_serving(_QWEN2_DEPLOYMENT, timeout=0.05)

    def test_unbounded_timeout_returns_once_status_reaches_running(self) -> None:
        statuses = ["DEPLOYING", "DEPLOYING", "RUNNING"]

        def get_details() -> dict[str, Any]:
            self.state.status = statuses.pop(0)
            return self.state.get_details()

        with patch(
            "cortexgrid.model_serving.lifecycle.get_serve_details", side_effect=get_details
        ):
            wait_for_model_serving(_QWEN2_DEPLOYMENT, timeout=None)

        self.assertEqual(statuses, [])

    def test_deploy_failed_raises_regardless_of_unbounded_timeout(self) -> None:
        self.state.status = "DEPLOY_FAILED"
        self.state.message = "replica died on import"

        with self.assertRaises(ModelDeployFailed) as ctx:
            wait_for_model_serving(_QWEN2_DEPLOYMENT, timeout=None)

        self.assertIn("DEPLOY_FAILED", str(ctx.exception))

    def test_missing_app_raises_regardless_of_unbounded_timeout(self) -> None:
        self.state.apps.clear()

        with self.assertRaises(ModelDeployFailed) as ctx:
            wait_for_model_serving(_QWEN2_DEPLOYMENT, timeout=None)

        self.assertIn("does not exist", str(ctx.exception))

    def test_app_removed_while_deploying_raises(self) -> None:
        self.state.status = "DEPLOYING"
        polls = 0

        def get_details() -> dict[str, Any]:
            nonlocal polls
            polls += 1
            if polls == 3:
                self.state.apps.clear()
            return self.state.get_details()

        with (
            patch(
                "cortexgrid.model_serving.lifecycle.get_serve_details", side_effect=get_details
            ),
            self.assertRaises(ModelDeployFailed),
        ):
            wait_for_model_serving(_QWEN2_DEPLOYMENT, timeout=None)

        self.assertEqual(polls, 3)


class TestModelServingStatus(unittest.TestCase):
    def setUp(self) -> None:
        self.state = FakeServeState()
        self.records = FakeState().install(self)
        _seed_saved_model(self.records, "fam", "suf", "run")
        patches = [
            patch(
                "cortexgrid.model_serving.lifecycle.get_serve_details",
                side_effect=self.state.get_details,
            ),
            patch(
                "cortexgrid.model_serving.status.get_serve_details",
                side_effect=self.state.get_details,
            ),
            patch(
                "cortexgrid.model_serving.lifecycle.put_serve_applications",
                side_effect=self.state.put,
            ),
            patch(
                "cortexgrid.model_serving.lifecycle.get_ray_serve_uri",
                return_value="http://ray:30000",
            ),
            patch(
                "cortexgrid.model_serving.lifecycle.vram_tiers",
                return_value=_TIERS,
            ),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _serve_app(self, status: str, message: str = "") -> None:
        """Deploy the model, then have the controller report `status` for it
        and the control plane observe that."""
        deploy_model("fam", "suf", "run")
        self.state.status = status
        self.state.message = message
        observe_deployments()

    def test_not_deployed_when_no_serve_app(self) -> None:
        s = model_serving_status(_FAM_DEPLOYMENT)
        self.assertEqual(s.phase, "not_deployed")
        self.assertIsNone(s.url)

    def test_not_deployed_once_the_app_is_gone(self) -> None:
        deploy_model("fam", "suf", "run")
        self.state.apps.clear()
        observe_deployments()

        s = model_serving_status(_FAM_DEPLOYMENT)
        self.assertEqual(s.phase, "not_deployed")
        self.assertIsNone(s.url)

    def test_not_started_is_reported_distinctly(self) -> None:
        self._serve_app("NOT_STARTED")
        self.assertEqual(
            model_serving_status(_FAM_DEPLOYMENT).phase, "not_started"
        )

    def test_deploying_reflects_controller_status_and_message(self) -> None:
        self._serve_app("DEPLOYING", "pulling weights")
        s = model_serving_status(_FAM_DEPLOYMENT)
        self.assertEqual(s.phase, "deploying")
        self.assertEqual(s.message, "pulling weights")

    def test_running_carries_the_route_url(self) -> None:
        self._serve_app("RUNNING")
        s = model_serving_status(_FAM_DEPLOYMENT)
        self.assertEqual(s.phase, "running")
        self.assertEqual(s.url, "http://ray:30000/r/fam/suf/run")

    def test_unhealthy_maps_to_unhealthy(self) -> None:
        self._serve_app("UNHEALTHY")
        self.assertEqual(
            model_serving_status(_FAM_DEPLOYMENT).phase, "unhealthy"
        )

    def test_deleting_maps_to_deleting(self) -> None:
        self._serve_app("DELETING")
        self.assertEqual(
            model_serving_status(_FAM_DEPLOYMENT).phase, "deleting"
        )

    def test_deploy_failed_maps_to_failed(self) -> None:
        self._serve_app("DEPLOY_FAILED", "oom")
        self.assertEqual(
            model_serving_status(_FAM_DEPLOYMENT).phase, "failed"
        )


class TestModelServingMessages(unittest.TestCase):
    def _messages(self, applications: dict[str, Any]) -> list[tuple[str, str, str]]:
        with patch(
            "cortexgrid.model_serving.status.get_serve_details",
            return_value={"applications": applications},
        ):
            return [
                (m.source, m.status, m.message)
                for m in model_serving_messages(_FAM_DEPLOYMENT)
            ]

    def test_empty_when_no_serve_app(self) -> None:
        self.assertEqual(self._messages({}), [])

    def test_application_message_precedes_deployment_messages(self) -> None:
        app = {
            "status": "DEPLOY_FAILED",
            "message": "app failed",
            "deployments": {
                "Model": {"status": "DEPLOY_FAILED", "message": "replica crashed"},
            },
        }
        self.assertEqual(
            self._messages({"fam__suf__run": app}),
            [
                ("application", "DEPLOY_FAILED", "app failed"),
                ("Model", "DEPLOY_FAILED", "replica crashed"),
            ],
        )

    def test_skips_sources_without_a_message(self) -> None:
        app = {
            "status": "UNHEALTHY",
            "message": "",
            "deployments": {
                "Healthy": {"status": "HEALTHY", "message": ""},
                "Sick": {"status": "UNHEALTHY", "message": "health check failed"},
            },
        }
        self.assertEqual(
            self._messages({"fam__suf__run": app}),
            [("Sick", "UNHEALTHY", "health check failed")],
        )

    def test_strips_ansi_color_escapes(self) -> None:
        app = {"status": "DEPLOY_FAILED", "message": "\x1b[31m!!! FAIL\x1b[39m pickle"}
        self.assertEqual(
            self._messages({"fam__suf__run": app}),
            [("application", "DEPLOY_FAILED", "!!! FAIL pickle")],
        )


class _ServeApp:
    pass


@ray_serve.ingress(FastAPI())
class _RayIngressServeApp:
    pass


class TestServeDependencies(unittest.TestCase):
    def test_bundle_class_records_pip_requirements_the_worker_lacks(self) -> None:
        desc = BundleDesc(
            local_files={Path(__file__).resolve()},
            tp_deps={"tqdm": "4.67.3", "ray": "2.55.1"},
        )
        with (
            patch("cortexgrid.model_serving.serve_bundle.bundle", return_value=desc),
            patch(
                "cortexgrid.model_serving.serve_bundle.worker_provides",
                return_value=frozenset({"ray"}),
            ),
            patch("cortexgrid.model_serving.serve_bundle.upload", return_value="s3://b/x.zip"),
        ):
            meta = bundle_class(_ServeApp, "fam", "suf", "run")

        self.assertEqual(meta.pip_requirements, ["tqdm==4.67.3"])

    def test_bundle_class_rejects_a_class_wrapped_by_ray_ingress(self) -> None:
        with (
            patch("cortexgrid.model_serving.serve_bundle.bundle") as bundle,
            self.assertRaisesRegex(ValueError, "cortexgrid.serve.ingress"),
        ):
            bundle_class(_RayIngressServeApp, "fam", "suf", "run")

        bundle.assert_not_called()

    def test_bundle_class_uploads_the_zip_of_a_dotted_model_name(self) -> None:
        uploaded: list[tuple[str, bool]] = []

        def fake_upload(local_path: str, dest_path: str) -> str:
            # the temp dir is gone after bundle_class returns; check it now
            uploaded.append((Path(local_path).name, Path(local_path).is_file()))
            return "s3://b/x.zip"

        desc = BundleDesc(local_files={Path(__file__).resolve()}, tp_deps={})
        with (
            patch("cortexgrid.model_serving.serve_bundle.bundle", return_value=desc),
            patch("cortexgrid.model_serving.serve_bundle.upload", side_effect=fake_upload),
        ):
            bundle_class(_ServeApp, "Qwen2.5-0.5B", "Instruct", "run")

        self.assertEqual(uploaded, [("Qwen2.5-0.5B__Instruct.zip", True)])

    def test_bundle_class_uploads_under_its_fingerprint(self) -> None:
        # Ray reuses a working_dir it has downloaded for the same URL, so new
        # code must land at a new URL.
        dest_paths: list[str] = []

        def fake_upload(local_path: str, dest_path: str) -> str:
            dest_paths.append(dest_path)
            return f"s3://b/{dest_path}"

        desc = BundleDesc(local_files={Path(__file__).resolve()}, tp_deps={})
        with (
            patch("cortexgrid.model_serving.serve_bundle.bundle", return_value=desc),
            patch("cortexgrid.model_serving.serve_bundle.upload", side_effect=fake_upload),
        ):
            meta = bundle_class(_ServeApp, "fam", "suf", "run")

        self.assertEqual(
            dest_paths, [f"serve-bundles/run/fam__suf/{meta.fingerprint}.zip"]
        )
        self.assertEqual(meta.bundle_url, f"s3://b/{dest_paths[0]}")

    def test_bundle_url_gives_back_the_fingerprint_it_was_uploaded_under(
        self,
    ) -> None:
        desc = BundleDesc(local_files={Path(__file__).resolve()}, tp_deps={})
        with (
            patch("cortexgrid.model_serving.serve_bundle.bundle", return_value=desc),
            patch(
                "cortexgrid.model_serving.serve_bundle.upload",
                side_effect=lambda local_path, dest_path: f"s3://b/{dest_path}",
            ),
        ):
            meta = bundle_class(_ServeApp, "fam", "suf", "run")

        self.assertEqual(bundle_fingerprint_from_url(meta.bundle_url), meta.fingerprint)

    def test_bundle_url_from_before_fingerprints_gives_none(self) -> None:
        self.assertEqual(
            bundle_fingerprint_from_url("s3://b/serve-bundles/run/fam__suf.zip"), ""
        )

    def _build(self, desc: BundleDesc) -> ServeBundle:
        with (
            patch("cortexgrid.model_serving.serve_bundle.bundle", return_value=desc),
            patch(
                "cortexgrid.model_serving.serve_bundle.worker_provides",
                return_value=frozenset(),
            ),
        ):
            return build_bundle(_ServeApp)

    def test_build_bundle_fingerprint_is_stable_for_the_same_code(self) -> None:
        desc = BundleDesc(
            local_files={Path(__file__).resolve()}, tp_deps={"tqdm": "4.67.3"}
        )

        self.assertEqual(self._build(desc).fingerprint, self._build(desc).fingerprint)

    def test_build_bundle_fingerprint_changes_with_the_code(self) -> None:
        root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, root, True)
        app = root / "app.py"
        app.write_text("x = 1\n")
        desc = BundleDesc(local_files={app}, tp_deps={})
        before = self._build(desc)

        app.write_text("x = 2\n")

        self.assertNotEqual(before.fingerprint, self._build(desc).fingerprint)

    def test_build_bundle_fingerprint_changes_with_the_pip_requirements(
        self,
    ) -> None:
        files = {Path(__file__).resolve()}
        before = self._build(BundleDesc(local_files=files, tp_deps={"tqdm": "4.67.3"}))

        after = self._build(BundleDesc(local_files=files, tp_deps={"tqdm": "4.67.4"}))

        self.assertNotEqual(before.fingerprint, after.fingerprint)

    def test_bundle_class_zip_unpacks_on_ray_with_packages_at_the_root(self) -> None:
        # Ray strips the single top-level directory of a remote working_dir zip;
        # a bundle of one package must still unpack with that package intact.
        root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, root, True)
        package = root / "src" / "pkg"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("")
        (package / "app.py").write_text("")
        working_dir = root / "working_dir"

        def fake_upload(local_path: str, dest_path: str) -> str:
            unzip_package(
                package_path=local_path,
                target_dir=str(working_dir),
                remove_top_level_directory=True,  # what Ray does for s3:// URIs
                unlink_zip=False,
            )
            return "s3://b/x.zip"

        desc = BundleDesc(
            local_files={package / "__init__.py", package / "app.py"}, tp_deps={}
        )
        with (
            patch("cortexgrid.model_serving.serve_bundle.bundle", return_value=desc),
            patch("cortexgrid.model_serving.serve_bundle.upload", side_effect=fake_upload),
        ):
            bundle_class(_ServeApp, "fam", "suf", "run")

        unpacked = {
            f.relative_to(working_dir).as_posix()
            for f in working_dir.rglob("*")
            if f.is_file()
        }
        self.assertEqual(unpacked, {"pkg/__init__.py", "pkg/app.py"})

    def test_spec_installs_pip_requirements(self) -> None:
        meta = BundleMetadata(
            bundle_url="s3://b/x.zip",
            class_import_path="stub:Stub",
            pip_requirements=["tqdm==4.67.3"],
        )

        spec = build_application_spec(
            _FAM_DEPLOYMENT, meta, ModelRequirements(), 1, _TIERS
        )

        self.assertEqual(
            spec["runtime_env"],
            {"working_dir": "s3://b/x.zip", "pip": ["tqdm==4.67.3"]},
        )

    def test_spec_without_pip_requirements_has_no_pip_key(self) -> None:
        # A pip key, even an empty one, makes Ray build a virtualenv.
        spec = build_application_spec(
            _FAM_DEPLOYMENT, _FAKE_META, ModelRequirements(), 1, _TIERS
        )

        self.assertEqual(spec["runtime_env"], {"working_dir": _FAKE_META.bundle_url})

    def test_spec_requests_the_requirements_from_ray(self) -> None:
        spec = build_application_spec(
            _FAM_DEPLOYMENT, _FAKE_META, _GPU_REQUIREMENTS, 2, _TIERS
        )

        self.assertEqual(spec["args"]["num_replicas"], 2)
        self.assertEqual(
            spec["args"]["ray_actor_options"],
            {
                "num_gpus": 1,
                "memory": 16 * 1024**3,
                "resources": {"vram_mib": 24 * 1024},
                # 24 GiB does not fit the 12 GiB tier, so the big one is the
                # only candidate and the small one is what the catch-all bars.
                "label_selector": {"vram_mib": str(_BIG_TIER)},
                "fallback_strategy": [
                    {"label_selector": {"vram_mib": f"!in({_SMALL_TIER})"}}
                ],
            },
        )

    def test_spec_without_requirements_requests_no_resources(self) -> None:
        spec = build_application_spec(
            _FAM_DEPLOYMENT, _FAKE_META, ModelRequirements(), 1, _TIERS
        )

        self.assertEqual(spec["args"]["ray_actor_options"], {"num_gpus": 0})

    def _load_with_tags(
        self, tags: dict[str, str]
    ) -> tuple[BundleMetadata, ModelRequirements]:
        records = FakeState().install(self)
        records.seed_model("fam", "suf", "run", "s3://bucket/weights", tags)
        return load_deploy_metadata("fam", "suf", "run")

    def test_bundle_metadata_round_trips_through_tags(self) -> None:
        meta = BundleMetadata(
            bundle_url="s3://b/x.zip",
            class_import_path="stub:Stub",
            pip_requirements=["haikunator==2.1.0", "tqdm==4.67.3"],
            fingerprint="abc123",
        )

        self.assertEqual(self._load_with_tags(metadata_to_tags(meta))[0], meta)

    def test_requirements_load_from_the_same_registry_entry(self) -> None:
        tags = {
            **metadata_to_tags(_FAKE_META),
            **requirements_to_tags(_GPU_REQUIREMENTS),
        }

        self.assertEqual(self._load_with_tags(tags)[1], _GPU_REQUIREMENTS)

    def test_model_saved_without_pip_tag_loads_with_no_requirements(self) -> None:
        tags = {"serve_bundle_url": "s3://b/x.zip", "class_import_path": "stub:Stub"}

        self.assertEqual(self._load_with_tags(tags)[0].pip_requirements, [])

    def test_model_saved_without_fingerprint_tag_loads_with_no_fingerprint(
        self,
    ) -> None:
        tags = {"serve_bundle_url": "s3://b/x.zip", "class_import_path": "stub:Stub"}

        self.assertEqual(self._load_with_tags(tags)[0].fingerprint, "")


class TestSpecRoundTripsThroughRay(unittest.TestCase):
    """`deploy_model` skips a redundant PUT by comparing the spec it just built
    against the `deployed_app_config` Ray reports back, so the two have to stay
    identical. Should a Ray upgrade fill in a default or rename a field, the
    comparison would silently stop matching and every deploy would PUT again -
    losing the guard without failing anything. These tests fail instead.
    """

    def _round_trip(self, spec: dict[str, Any]) -> dict[str, Any]:
        """Replay what Ray does to a PUT spec before handing it back under
        `deployed_app_config`: validate the deploy request, checkpoint the app
        config, re-validate it out of the checkpoint, serialize it into the
        details response. The details dump is taken on the app config alone;
        the enclosing `ServeInstanceDetails` only strips internal fields under
        `deployments`, which our specs never set."""
        deploy = ServeDeploySchema.model_validate({"applications": [spec]})
        checkpointed = deploy.applications[0].model_dump(exclude_unset=True)
        restored = ServeApplicationSchema.model_validate(checkpointed)
        return json.loads(json.dumps(restored.model_dump(exclude_unset=True)))

    def test_gpu_spec_survives_the_round_trip_unchanged(self) -> None:
        spec = build_application_spec(
            _FAM_DEPLOYMENT, _FAKE_META, _GPU_REQUIREMENTS, 2, _TIERS
        )

        self.assertEqual(self._round_trip(spec), spec)

    def test_spec_with_pip_requirements_survives_the_round_trip_unchanged(self) -> None:
        meta = BundleMetadata(
            bundle_url="s3://b/x.zip",
            class_import_path="stub:Stub",
            pip_requirements=["tqdm==4.67.3"],
        )

        spec = build_application_spec(
            _FAM_DEPLOYMENT, meta, ModelRequirements(), 1, _TIERS
        )

        self.assertEqual(self._round_trip(spec), spec)


class TestReplicaPlacements(unittest.TestCase):
    """`model_replica_placements` reports which worker each replica landed on -
    what the requirements and the size-class preferences resolved to - as the
    control plane last observed it."""

    def _placements(self, app: dict[str, Any] | None) -> list[Any]:
        records = FakeState().install(self)
        _seed_deployment(records, "fam", "suf", "run")
        details = {"applications": {"fam__suf__run": app} if app else {}}
        with patch(
            "cortexgrid.model_serving.status.get_serve_details", return_value=details
        ):
            observe_deployments()
        return model_replica_placements(_FAM_DEPLOYMENT)

    def test_reports_the_worker_each_replica_runs_on(self) -> None:
        placements = self._placements(
            {
                "deployments": {
                    "Model": {
                        "replicas": [
                            {
                                "replica_id": "r1",
                                "state": "RUNNING",
                                "node_id": "n1",
                                "node_ip": "10.0.0.7",
                            }
                        ]
                    }
                }
            }
        )

        self.assertEqual(
            [(p.replica_id, p.state, p.node_ip) for p in placements],
            [("r1", "RUNNING", "10.0.0.7")],
        )

    def test_reports_every_replica_across_deployments(self) -> None:
        placements = self._placements(
            {
                "deployments": {
                    "A": {"replicas": [{"replica_id": "r1"}, {"replica_id": "r2"}]},
                    "B": {"replicas": [{"replica_id": "r3"}]},
                }
            }
        )

        self.assertEqual([p.replica_id for p in placements], ["r1", "r2", "r3"])

    def test_a_replica_not_placed_yet_has_no_node(self) -> None:
        # The controller has the replica but Ray has not found it a device.
        placements = self._placements(
            {"deployments": {"Model": {"replicas": [{"replica_id": "r1"}]}}}
        )

        self.assertIsNone(placements[0].node_ip)

    def test_a_model_that_is_not_deployed_has_no_placements(self) -> None:
        self.assertEqual(self._placements(None), [])

    def test_a_model_never_deployed_has_no_placements(self) -> None:
        FakeState().install(self)

        self.assertEqual(model_replica_placements(_FAM_DEPLOYMENT), [])


class TestObserveDeployments(unittest.TestCase):
    """`observe_deployments` brings every deployment record up to date with
    one read of the Serve controller, writing only what changed."""

    _REPLICA = {
        "replica_id": "r1",
        "state": "RUNNING",
        "node_id": "n1",
        "node_ip": "10.0.0.7",
    }

    def setUp(self) -> None:
        self.records = FakeState().install(self)
        _seed_deployment(self.records, "fam", "suf", "run")

    def _observe(self, applications: dict[str, Any]) -> dict[str, Any]:
        with patch(
            "cortexgrid.model_serving.status.get_serve_details",
            return_value={"applications": applications},
        ):
            observe_deployments()
        return self.records.deployments[("fam", "suf", "run", "")]

    def test_records_the_phase_and_message_the_controller_reports(self) -> None:
        record = self._observe(
            {"fam__suf__run": {"status": "UNHEALTHY", "message": "health check failed"}}
        )

        self.assertEqual(
            (record["phase"], record["message"]), ("unhealthy", "health check failed")
        )

    def test_records_where_the_replicas_run(self) -> None:
        record = self._observe(
            {
                "fam__suf__run": {
                    "status": "RUNNING",
                    "deployments": {"Model": {"replicas": [self._REPLICA]}},
                }
            }
        )

        self.assertEqual(record["replicas"], [self._REPLICA])

    def test_an_app_gone_from_ray_is_marked_not_deployed(self) -> None:
        _seed_deployment(
            self.records, "fam", "suf", "run", replicas=[self._REPLICA]
        )

        record = self._observe({})

        self.assertEqual(
            (record["phase"], record["message"], record["replicas"]),
            ("not_deployed", "", []),
        )

    def test_an_unchanged_observation_writes_nothing(self) -> None:
        # The control plane observes on every poll cycle; a steady app must not
        # cost a write each time.
        with patch("cortexgrid.state.patch") as write:
            self._observe({"fam__suf__run": {"status": "RUNNING"}})

        write.assert_not_called()


class TestSmallestDevicePlacement(unittest.TestCase):
    """`_placement_options` ranks the cluster's GPU size classes for one model.

    It only orders the candidates - the `vram_mib` resource is still what
    reserves the memory - so every case here is about which tier Ray is asked
    for first, and about what remains placeable when the cluster changes under
    a deploy.
    """

    def _options(self, vram_gb: float, tiers: list[int]) -> dict[str, Any]:
        return _placement_options(
            ModelRequirements(num_gpus=1, vram_gb=vram_gb), tiers
        )

    def _order(self, vram_gb: float, tiers: list[int]) -> list[str]:
        """The tiers Ray is offered, best first, as bare selector values."""
        options = self._options(vram_gb, tiers)
        if not options:
            return []
        chain = [options["label_selector"]] + [
            f["label_selector"] for f in options.get("fallback_strategy", [])
        ]
        return [selector["vram_mib"] for selector in chain]

    def test_small_model_is_offered_the_smallest_card_first(self) -> None:
        # The whole point: 4 GiB fits both cards, and the 12 GiB one must be
        # asked for before the 128 GiB one.
        self.assertEqual(self._order(4.0, _TIERS), [str(_SMALL_TIER), str(_BIG_TIER)])

    def test_larger_cards_follow_in_ascending_order(self) -> None:
        self.assertEqual(
            self._order(4.0, [_BIG_TIER, 24564, _SMALL_TIER])[:3],
            [str(_SMALL_TIER), "24564", str(_BIG_TIER)],
        )

    def test_a_card_too_small_is_never_offered(self) -> None:
        self.assertNotIn(str(_SMALL_TIER), self._order(24.0, _TIERS))

    def test_a_model_no_card_fits_is_left_to_the_catch_all(self) -> None:
        # Nothing in the cluster is big enough today. Rather than name a tier
        # that cannot work, bar the ones that cannot and let a bigger card
        # joining later pick it up.
        self.assertEqual(
            self._order(512.0, _TIERS), [f"!in({_SMALL_TIER}, {_BIG_TIER})"]
        )

    def test_catch_all_is_dropped_when_every_card_fits(self) -> None:
        # With nothing too small to exclude the catch-all would say nothing.
        self.assertEqual(self._order(1.0, _TIERS), [str(_SMALL_TIER), str(_BIG_TIER)])

    def test_a_model_needing_no_vram_is_not_confined_to_a_gpu(self) -> None:
        # A CPU-only model must stay placeable on a node that has no GPU, and
        # so carries no vram_mib label at all.
        self.assertEqual(_placement_options(ModelRequirements(), _TIERS), {})

    def test_a_cluster_reporting_no_sizes_places_as_it_did_before(self) -> None:
        self.assertEqual(self._options(4.0, []), {})

    def test_the_request_is_rounded_the_same_way_as_the_reservation(self) -> None:
        # vram_gb 11.994 -> 12281 MiB, which the 12282 MiB card fits. Ranking
        # the tiers on a differently-rounded number than the resource request
        # would offer a card the reservation then rejects.
        self.assertEqual(self._order(11.994, _TIERS)[0], str(_SMALL_TIER))


class TestVramTiers(unittest.TestCase):
    """`vram_tiers` reduces the cluster's nodes to the distinct GPU sizes."""

    def _tiers(self, nodes: list[dict[str, Any]]) -> list[int]:
        with patch("cortexgrid.model_serving.placement.get_ray_nodes", return_value=nodes):
            return vram_tiers()

    @staticmethod
    def _node(state: str = "ALIVE", **labels: str) -> dict[str, Any]:
        return {"node_id": "n", "state": state, "labels": labels}

    def test_identical_cards_collapse_to_one_tier(self) -> None:
        nodes = [self._node(vram_mib="12282") for _ in range(3)]

        self.assertEqual(self._tiers(nodes), [_SMALL_TIER])

    def test_tiers_come_back_smallest_first(self) -> None:
        nodes = [self._node(vram_mib="131072"), self._node(vram_mib="12282")]

        self.assertEqual(self._tiers(nodes), [_SMALL_TIER, _BIG_TIER])

    def test_a_dead_node_is_not_a_tier(self) -> None:
        # Its card is gone, so offering it would strand the replica.
        nodes = [self._node(vram_mib="12282"), self._node("DEAD", vram_mib="131072")]

        self.assertEqual(self._tiers(nodes), [_SMALL_TIER])

    def test_nodes_without_a_gpu_contribute_nothing(self) -> None:
        self.assertEqual(self._tiers([self._node(), {"state": "ALIVE"}]), [])

    def test_a_hand_set_label_that_is_not_a_size_is_skipped(self) -> None:
        nodes = [self._node(vram_mib="huge"), self._node(vram_mib="12282")]

        self.assertEqual(self._tiers(nodes), [_SMALL_TIER])


class TestModelRequirements(unittest.TestCase):
    def test_round_trips_through_tags(self) -> None:
        requirements = ModelRequirements(num_gpus=1, ram_gb=16.0, vram_gb=24.5)

        self.assertEqual(
            requirements_from_tags(requirements_to_tags(requirements)),
            requirements,
        )

    def test_model_saved_without_tags_has_no_requirements(self) -> None:
        self.assertEqual(requirements_from_tags({}), ModelRequirements())

    def test_rejects_negative_values(self) -> None:
        for kwargs in ({"num_gpus": -1}, {"ram_gb": -1.0}, {"vram_gb": -1.0}):
            with self.subTest(**kwargs), self.assertRaises(ValueError):
                ModelRequirements(**kwargs)

    def test_rejects_vram_without_a_gpu(self) -> None:
        with self.assertRaises(ValueError):
            ModelRequirements(num_gpus=0, vram_gb=8.0)

    def test_a_share_of_a_gpu_round_trips(self) -> None:
        # 0.25 of a card: four such replicas are served on one GPU, with
        # vram_gb keeping them from overcommitting its memory.
        shared = ModelRequirements(num_gpus=0.25, ram_gb=4.0, vram_gb=6.0)

        self.assertEqual(requirements_from_tags(requirements_to_tags(shared)), shared)

    def test_a_share_of_a_gpu_satisfies_the_vram_rule(self) -> None:
        self.assertEqual(ModelRequirements(num_gpus=0.5, vram_gb=6.0).num_gpus, 0.5)

    def test_a_whole_number_saved_before_sharing_still_reads(self) -> None:
        self.assertEqual(requirements_from_tags({"num_gpus": "1"}).num_gpus, 1.0)

    def test_a_share_is_requested_from_ray_as_it_was_stored(self) -> None:
        spec = build_application_spec(
            _FAM_DEPLOYMENT,
            _FAKE_META,
            ModelRequirements(num_gpus=0.25, vram_gb=6.0),
            1,
            _TIERS,
        )

        self.assertEqual(spec["args"]["ray_actor_options"]["num_gpus"], 0.25)


def _running_app(target_num_replicas: int, replica_states: list[str]) -> dict[str, Any]:
    return {
        "status": "RUNNING",
        "deployments": {
            "Model": {
                "target_num_replicas": target_num_replicas,
                "replicas": [{"state": state} for state in replica_states],
            }
        },
    }


class TestPhase(unittest.TestCase):
    def test_running_app_scaled_to_zero_is_paused(self) -> None:
        self.assertEqual(_phase(_running_app(0, [])), "paused")

    def test_running_app_still_stopping_its_replica_is_paused(self) -> None:
        self.assertEqual(_phase(_running_app(0, ["STOPPING"])), "paused")

    def test_running_app_resuming_without_a_running_replica_is_deploying(self) -> None:
        self.assertEqual(_phase(_running_app(1, ["STARTING"])), "deploying")

    def test_running_app_with_a_running_replica_is_running(self) -> None:
        self.assertEqual(_phase(_running_app(1, ["RUNNING"])), "running")

    def test_failed_app_stays_failed_at_zero_replicas(self) -> None:
        app = {**_running_app(0, []), "status": "DEPLOY_FAILED"}
        self.assertEqual(_phase(app), "failed")


if __name__ == "__main__":
    unittest.main()
