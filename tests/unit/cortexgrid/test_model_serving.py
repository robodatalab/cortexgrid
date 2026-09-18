from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from ray import serve as ray_serve
from ray._private.runtime_env.packaging import unzip_package
from ray.serve.schema import ServeApplicationSchema, ServeDeploySchema

from cortexgrid._bundle import BundleDesc
from cortexgrid.model_serving import (
    BundleMetadata,
    ModelDeployFailed,
    ModelRequirements,
    ServeBundle,
    _build_application_spec,
    _load_deploy_metadata,
    build_bundle,
    bundle_class,
    deploy_model,
    list_deployed_models,
    metadata_to_tags,
    model_serving_messages,
    model_serving_status,
    requirements_from_tags,
    requirements_to_tags,
    undeploy_model,
    wait_for_model_serving,
)


_FAKE_META = BundleMetadata(
    bundle_url="s3://bucket/stub.zip",
    class_import_path="stub:Stub",
)

_GPU_REQUIREMENTS = ModelRequirements(num_gpus=1, ram_gb=16.0, vram_gb=24.0)


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


def _stub_build_spec(
    family: str,
    suffix: str,
    run_name: str,
    meta: BundleMetadata,
    requirements: ModelRequirements,
    num_replicas: int,
) -> dict[str, Any]:
    return {
        "name": f"{family}__{suffix}__{run_name}",
        "route_prefix": f"/r/{family}/{suffix}/{run_name}",
        "import_path": "stub:Stub",
        "args": {"family": family, "suffix": suffix, "run_name": run_name},
        "runtime_env": {"working_dir": meta.bundle_url},
    }


class TestModelServing(unittest.TestCase):
    def setUp(self) -> None:
        self.state = FakeServeState()
        patches = [
            patch(
                "cortexgrid.model_serving.get_serve_details",
                side_effect=self.state.get_details,
            ),
            patch(
                "cortexgrid.model_serving.put_serve_applications",
                side_effect=self.state.put,
            ),
            patch(
                "cortexgrid.model_serving.get_ray_serve_uri",
                return_value="http://ray:30000",
            ),
            patch(
                "cortexgrid.model_serving._load_deploy_metadata",
                return_value=(_FAKE_META, _GPU_REQUIREMENTS),
            ),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        build_spec = patch(
            "cortexgrid.model_serving._build_application_spec",
            side_effect=_stub_build_spec,
        )
        self.build_spec = build_spec.start()
        self.addCleanup(build_spec.stop)

    def test_deploy_builds_the_spec_from_the_stored_requirements(self) -> None:
        deploy_model("Qwen2", "instruct", "boogey-46", num_replicas=3)

        self.build_spec.assert_called_once_with(
            "Qwen2", "instruct", "boogey-46", _FAKE_META, _GPU_REQUIREMENTS, 3
        )

    def test_deploy_runs_one_replica_by_default(self) -> None:
        deploy_model("Qwen2", "instruct", "boogey-46")

        self.assertEqual(self.build_spec.call_args.args[-1], 1)

    def test_deployed_model_appears_in_listings(self) -> None:
        deploy_model("Qwen2", "instruct", "boogey-46")

        listed = list_deployed_models()

        self.assertEqual(
            [(d.family, d.suffix, d.run_name) for d in listed],
            [("Qwen2", "instruct", "boogey-46")],
        )

    def test_undeployed_model_disappears_from_listings(self) -> None:
        deploy_model("Qwen2", "instruct", "boogey-46")
        undeploy_model("Qwen2", "instruct", "boogey-46")

        self.assertEqual(list_deployed_models(), [])

    def test_deployment_url_combines_serve_uri_and_route(self) -> None:
        d = deploy_model("Qwen2", "instruct", "boogey-46")

        self.assertEqual(d.url, "http://ray:30000/r/Qwen2/instruct/boogey-46")

    def test_apps_not_using_our_naming_scheme_excluded_from_listings(self) -> None:
        deploy_model("Qwen2", "instruct", "boogey-46")
        self.state.apps["unrelated-app"] = {"name": "unrelated-app"}
        self.state.apps["only__two"] = {"name": "only__two"}

        listed = list_deployed_models()

        self.assertEqual([d.family for d in listed], ["Qwen2"])

    def test_list_returns_empty_when_nothing_deployed(self) -> None:
        self.assertEqual(list_deployed_models(), [])

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

        with patch("cortexgrid.model_serving.time.sleep"):
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
            "cortexgrid.model_serving.put_serve_applications", side_effect=put
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
            patch("cortexgrid.model_serving.time.sleep"),
            patch(
                "cortexgrid.model_serving.get_serve_details", side_effect=get_details
            ),
            patch(
                "cortexgrid.model_serving.put_serve_applications", side_effect=put
            ),
        ):
            deploy_model("Qwen2", "instruct", "boogey-46")

        self.assertEqual(still_deleting_at_put, [False])

    def test_redeploying_times_out_while_the_old_app_is_still_deleting(self) -> None:
        name = "Qwen2__instruct__boogey-46"
        self.state.apps[name] = {"name": name}
        self.state.status = "DELETING"

        with (
            patch("cortexgrid.model_serving.time.sleep"),
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
            "cortexgrid.model_serving.put_serve_applications", side_effect=put
        ):
            deploy_model("Qwen2", "instruct", "boogey-46")

        self.assertEqual(puts, [[name]])

    def test_redeploying_an_unchanged_spec_skips_the_put(self) -> None:
        deploy_model("Qwen2", "instruct", "boogey-46")
        puts: list[list[str]] = []

        with patch(
            "cortexgrid.model_serving.put_serve_applications",
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
            "cortexgrid.model_serving.put_serve_applications",
            side_effect=lambda apps: puts.append([a["name"] for a in apps]),
        ):
            deploy_model("Qwen2", "instruct", "boogey-46")

        self.assertEqual(puts, [])

    def test_redeploying_a_changed_spec_puts_it_again(self) -> None:
        name = "Qwen2__instruct__boogey-46"
        deploy_model("Qwen2", "instruct", "boogey-46")
        self.state.apps[name]["runtime_env"] = {"working_dir": "s3://bundles/old.zip"}
        puts: list[list[str]] = []

        def put(applications: list[dict[str, Any]]) -> None:
            puts.append([a["name"] for a in applications])
            self.state.put(applications)

        with patch("cortexgrid.model_serving.put_serve_applications", side_effect=put):
            deploy_model("Qwen2", "instruct", "boogey-46")

        self.assertEqual(puts, [[name]])



class TestWaitForModelServing(unittest.TestCase):
    def setUp(self) -> None:
        self.state = FakeServeState()
        self.state.apps["Qwen2__instruct__boogey-46"] = {
            "name": "Qwen2__instruct__boogey-46"
        }
        patches = [
            patch(
                "cortexgrid.model_serving.get_serve_details",
                side_effect=self.state.get_details,
            ),
            patch("cortexgrid.model_serving.time.sleep"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_times_out_when_status_never_reaches_running(self) -> None:
        self.state.status = "DEPLOYING"
        self.state.message = "still booting"

        with self.assertRaises(TimeoutError) as ctx:
            wait_for_model_serving("Qwen2", "instruct", "boogey-46", timeout=0.05)

        self.assertIn("DEPLOYING", str(ctx.exception))
        self.assertIn("still booting", str(ctx.exception))

    def test_treats_unhealthy_as_transient_until_timeout(self) -> None:
        self.state.status = "UNHEALTHY"

        with self.assertRaises(TimeoutError):
            wait_for_model_serving("Qwen2", "instruct", "boogey-46", timeout=0.05)

    def test_finite_timeout_elapsing_raises_timeout_error(self) -> None:
        self.state.status = "DEPLOYING"

        with self.assertRaises(TimeoutError):
            wait_for_model_serving("Qwen2", "instruct", "boogey-46", timeout=0.05)

    def test_unbounded_timeout_returns_once_status_reaches_running(self) -> None:
        statuses = ["DEPLOYING", "DEPLOYING", "RUNNING"]

        def get_details() -> dict[str, Any]:
            self.state.status = statuses.pop(0)
            return self.state.get_details()

        with patch(
            "cortexgrid.model_serving.get_serve_details", side_effect=get_details
        ):
            wait_for_model_serving("Qwen2", "instruct", "boogey-46", timeout=None)

        self.assertEqual(statuses, [])

    def test_deploy_failed_raises_regardless_of_unbounded_timeout(self) -> None:
        self.state.status = "DEPLOY_FAILED"
        self.state.message = "replica died on import"

        with self.assertRaises(ModelDeployFailed) as ctx:
            wait_for_model_serving("Qwen2", "instruct", "boogey-46", timeout=None)

        self.assertIn("DEPLOY_FAILED", str(ctx.exception))

    def test_missing_app_raises_regardless_of_unbounded_timeout(self) -> None:
        self.state.apps.clear()

        with self.assertRaises(ModelDeployFailed) as ctx:
            wait_for_model_serving("Qwen2", "instruct", "boogey-46", timeout=None)

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
                "cortexgrid.model_serving.get_serve_details", side_effect=get_details
            ),
            self.assertRaises(ModelDeployFailed),
        ):
            wait_for_model_serving("Qwen2", "instruct", "boogey-46", timeout=None)

        self.assertEqual(polls, 3)


class TestModelServingStatus(unittest.TestCase):
    def setUp(self) -> None:
        self.state = FakeServeState()
        patches = [
            patch(
                "cortexgrid.model_serving.get_serve_details",
                side_effect=self.state.get_details,
            ),
            patch(
                "cortexgrid.model_serving.get_ray_serve_uri",
                return_value="http://ray:30000",
            ),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _serve_app(self, status: str, message: str = "") -> None:
        self.state.apps["fam__suf__run"] = {"name": "fam__suf__run"}
        self.state.status = status
        self.state.message = message

    def test_not_deployed_when_no_serve_app(self) -> None:
        s = model_serving_status("fam", "suf", "run")
        self.assertEqual(s.phase, "not_deployed")
        self.assertIsNone(s.url)

    def test_not_started_is_reported_distinctly(self) -> None:
        self._serve_app("NOT_STARTED")
        self.assertEqual(
            model_serving_status("fam", "suf", "run").phase, "not_started"
        )

    def test_deploying_reflects_controller_status_and_message(self) -> None:
        self._serve_app("DEPLOYING", "pulling weights")
        s = model_serving_status("fam", "suf", "run")
        self.assertEqual(s.phase, "deploying")
        self.assertEqual(s.message, "pulling weights")

    def test_running_carries_the_route_url(self) -> None:
        self._serve_app("RUNNING")
        s = model_serving_status("fam", "suf", "run")
        self.assertEqual(s.phase, "running")
        self.assertEqual(s.url, "http://ray:30000/r/fam/suf/run")

    def test_unhealthy_maps_to_unhealthy(self) -> None:
        self._serve_app("UNHEALTHY")
        self.assertEqual(
            model_serving_status("fam", "suf", "run").phase, "unhealthy"
        )

    def test_deleting_maps_to_deleting(self) -> None:
        self._serve_app("DELETING")
        self.assertEqual(
            model_serving_status("fam", "suf", "run").phase, "deleting"
        )

    def test_deploy_failed_maps_to_failed(self) -> None:
        self._serve_app("DEPLOY_FAILED", "oom")
        self.assertEqual(
            model_serving_status("fam", "suf", "run").phase, "failed"
        )


class TestModelServingMessages(unittest.TestCase):
    def _messages(self, applications: dict[str, Any]) -> list[tuple[str, str, str]]:
        with patch(
            "cortexgrid.model_serving.get_serve_details",
            return_value={"applications": applications},
        ):
            return [
                (m.source, m.status, m.message)
                for m in model_serving_messages("fam", "suf", "run")
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
            patch("cortexgrid.model_serving.bundle", return_value=desc),
            patch(
                "cortexgrid.model_serving.worker_provides",
                return_value=frozenset({"ray"}),
            ),
            patch("cortexgrid.model_serving.upload", return_value="s3://b/x.zip"),
        ):
            meta = bundle_class(_ServeApp, "fam", "suf", "run")

        self.assertEqual(meta.pip_requirements, ["tqdm==4.67.3"])

    def test_bundle_class_rejects_a_class_wrapped_by_ray_ingress(self) -> None:
        with (
            patch("cortexgrid.model_serving.bundle") as bundle,
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
            patch("cortexgrid.model_serving.bundle", return_value=desc),
            patch("cortexgrid.model_serving.upload", side_effect=fake_upload),
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
            patch("cortexgrid.model_serving.bundle", return_value=desc),
            patch("cortexgrid.model_serving.upload", side_effect=fake_upload),
        ):
            meta = bundle_class(_ServeApp, "fam", "suf", "run")

        self.assertEqual(
            dest_paths, [f"serve-bundles/run/fam__suf/{meta.fingerprint}.zip"]
        )
        self.assertEqual(meta.bundle_url, f"s3://b/{dest_paths[0]}")

    def _build(self, desc: BundleDesc) -> ServeBundle:
        with (
            patch("cortexgrid.model_serving.bundle", return_value=desc),
            patch(
                "cortexgrid.model_serving.worker_provides",
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
            patch("cortexgrid.model_serving.bundle", return_value=desc),
            patch("cortexgrid.model_serving.upload", side_effect=fake_upload),
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

        spec = _build_application_spec(
            "fam", "suf", "run", meta, ModelRequirements(), 1
        )

        self.assertEqual(
            spec["runtime_env"],
            {"working_dir": "s3://b/x.zip", "pip": ["tqdm==4.67.3"]},
        )

    def test_spec_without_pip_requirements_has_no_pip_key(self) -> None:
        # A pip key, even an empty one, makes Ray build a virtualenv.
        spec = _build_application_spec(
            "fam", "suf", "run", _FAKE_META, ModelRequirements(), 1
        )

        self.assertEqual(spec["runtime_env"], {"working_dir": _FAKE_META.bundle_url})

    def test_spec_requests_the_requirements_from_ray(self) -> None:
        spec = _build_application_spec(
            "fam", "suf", "run", _FAKE_META, _GPU_REQUIREMENTS, 2
        )

        self.assertEqual(spec["args"]["num_replicas"], 2)
        self.assertEqual(
            spec["args"]["ray_actor_options"],
            {
                "num_gpus": 1,
                "memory": 16 * 1024**3,
                "resources": {"vram_mib": 24 * 1024},
            },
        )

    def test_spec_without_requirements_requests_no_resources(self) -> None:
        spec = _build_application_spec(
            "fam", "suf", "run", _FAKE_META, ModelRequirements(), 1
        )

        self.assertEqual(spec["args"]["ray_actor_options"], {"num_gpus": 0})

    def _load_with_tags(
        self, tags: dict[str, str]
    ) -> tuple[BundleMetadata, ModelRequirements]:
        client = MagicMock()
        client.search_model_versions.return_value = [SimpleNamespace(tags=tags)]
        with (
            patch("cortexgrid.model_serving.MlflowClient", return_value=client),
            patch(
                "cortexgrid.model_serving.get_mlflow_tracking_uri",
                return_value="http://test:5000",
            ),
        ):
            return _load_deploy_metadata("fam", "suf", "run")

    def test_bundle_metadata_round_trips_through_tags(self) -> None:
        meta = BundleMetadata(
            bundle_url="s3://b/x.zip",
            class_import_path="stub:Stub",
            pip_requirements=["haikunator==2.1.0", "tqdm==4.67.3"],
            fingerprint="abc123",
        )

        self.assertEqual(self._load_with_tags(metadata_to_tags(meta))[0], meta)

    def test_requirements_load_from_the_same_version(self) -> None:
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
        spec = _build_application_spec(
            "fam", "suf", "run", _FAKE_META, _GPU_REQUIREMENTS, 2
        )

        self.assertEqual(self._round_trip(spec), spec)

    def test_spec_with_pip_requirements_survives_the_round_trip_unchanged(self) -> None:
        meta = BundleMetadata(
            bundle_url="s3://b/x.zip",
            class_import_path="stub:Stub",
            pip_requirements=["tqdm==4.67.3"],
        )

        spec = _build_application_spec("fam", "suf", "run", meta, ModelRequirements(), 1)

        self.assertEqual(self._round_trip(spec), spec)


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


if __name__ == "__main__":
    unittest.main()
