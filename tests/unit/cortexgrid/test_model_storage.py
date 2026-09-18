from __future__ import annotations

import json
import shutil
import tempfile
import time
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, call, patch

from mlflow.exceptions import MlflowException

import cortexgrid
from cortexgrid.experiment import Experiment, clear_instance, set_instance
from cortexgrid.model_serving import BundleMetadata, ModelRequirements, ServeBundle
from cortexgrid.model_storage import (
    IMPORTED,
    NO_WEIGHTS,
    delete_model,
    delete_models_for_run,
    import_model,
    list_models,
    load_model,
    model_config,
    model_registry_status,
    register_model,
    save_model,
    set_model_config,
    set_model_requirements,
)


class _FakeServeApp:
    """Stand-in for the Ray Serve ingress class paired with the weights at save
    time. `bundle_class` is mocked in these tests, so this only needs to be a
    type that `save_model` can hand to the (mocked) bundler."""


_GPU_REQUIREMENTS = ModelRequirements(num_gpus=1, ram_gb=16.0, vram_gb=24.0)

# What a serve-app that downloads no weights needs instead: which model to call
# and where to find the credential. cortexgrid never reads either key.
_CONFIG = {"model": "claude-opus-5", "api_key_secret": "anthropic-api-key"}


def _recent_ms() -> int:
    """Epoch-ms for a version created just now (well within the upload deadline),
    so a still-"uploading" version does not read as a stale/broken upload."""
    return int(time.time() * 1000)


def _make_weights_dir() -> Path:
    """Write the fixture weights the size-bytes assertions expect: config.json
    (8 bytes) + model.safetensors (7 bytes) = 15 bytes. cortexgrid treats the
    directory as opaque, so the exact contents only matter for the byte count."""
    d = Path(tempfile.mkdtemp())
    (d / "config.json").write_text('{"x": 1}')
    (d / "model.safetensors").write_bytes(b"weights")
    return d


_FAKE_BUNDLE = BundleMetadata(
    bundle_url="s3://b/serve-bundles/x.zip",
    class_import_path="fake.module:FakeClass",
    fingerprint="code-v1",
)


def _serve_bundle(fingerprint: str) -> ServeBundle:
    """A built (not uploaded) bundle of `_FakeServeApp` with this fingerprint."""
    return ServeBundle(
        files=set(),
        class_import_path="fake.module:FakeClass",
        pip_requirements=[],
        fingerprint=fingerprint,
    )


@dataclass
class FakeModelVersion:
    name: str
    version: str
    source: str | None
    run_id: str | None
    tags: dict[str, str]
    creation_timestamp: int = 1700000000000


@dataclass
class FakeRun:
    run_id: str
    run_name: str

    @property
    def info(self) -> Any:
        return SimpleNamespace(run_id=self.run_id, run_name=self.run_name)


class FakeMLflow:
    """Just enough MLflow Registry surface for storage.py."""

    def __init__(self) -> None:
        self.registered: set[str] = set()
        self.versions: list[FakeModelVersion] = []
        self.runs: dict[str, FakeRun] = {}
        self._next_version = 1

    def create_registered_model(self, name: str) -> Any:
        if name in self.registered:
            raise MlflowException("RESOURCE_ALREADY_EXISTS")
        self.registered.add(name)
        return SimpleNamespace(name=name)

    def get_registered_model(self, name: str) -> Any:
        if name not in self.registered:
            raise MlflowException("RESOURCE_DOES_NOT_EXIST")
        return SimpleNamespace(name=name)

    def create_model_version(
        self,
        name: str,
        source: str,
        run_id: str | None,
        tags: dict[str, str],
    ) -> FakeModelVersion:
        v = FakeModelVersion(
            name=name,
            version=str(self._next_version),
            source=source,
            run_id=run_id,
            tags=tags,
            # MLflow stamps the creation time, so a version this fake creates
            # is fresh rather than past the upload deadline.
            creation_timestamp=_recent_ms(),
        )
        self._next_version += 1
        self.versions.append(v)
        return v

    def search_model_versions(self, filter_string: str) -> list[FakeModelVersion]:
        result = list(self.versions)
        if "name='" in filter_string:
            n = filter_string.split("name='")[1].split("'")[0]
            result = [v for v in result if v.name == n]
        if "tags.run_name='" in filter_string:
            rn = filter_string.split("tags.run_name='")[1].split("'")[0]
            result = [v for v in result if v.tags.get("run_name") == rn]
        if "run_id='" in filter_string:
            rid = filter_string.split("run_id='")[1].split("'")[0]
            result = [v for v in result if v.run_id == rid]
        return result

    def set_model_version_tag(
        self, name: str, version: str, key: str, value: str
    ) -> None:
        for v in self.versions:
            if v.name == name and v.version == version:
                v.tags[key] = value
                return
        raise MlflowException("RESOURCE_DOES_NOT_EXIST")

    def get_model_version(self, name: str, version: str) -> FakeModelVersion:
        for v in self.versions:
            if v.name == name and v.version == version:
                return v
        raise MlflowException("RESOURCE_DOES_NOT_EXIST")

    def delete_model_version(self, name: str, version: str) -> None:
        self.versions = [
            v for v in self.versions
            if not (v.name == name and v.version == version)
        ]

    def get_run(self, run_id: str) -> FakeRun:
        return self.runs[run_id]


class FakeS3:
    """Fake `cortexgrid.s3_util` module and the boto3 client it returns."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def upload_dir(self, local_dir: str, dest_path: str) -> list[str]:
        for path in Path(local_dir).rglob("*"):
            if path.is_file():
                rel = str(path.relative_to(local_dir))
                self.objects[f"{dest_path}/{rel}"] = path.read_bytes()
        return []

    def delete_prefix(self, prefix: str) -> None:
        self.objects = {
            k: v for k, v in self.objects.items() if not k.startswith(prefix)
        }

    def get_s3_client(self) -> "FakeS3":
        return self

    def get_paginator(self, op: str) -> "FakeS3":
        return self

    def paginate(self, Bucket: str, Prefix: str) -> list[dict[str, Any]]:
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        return [{"Contents": [{"Key": k} for k in keys]}] if keys else [{}]

    def download_file(self, Bucket: str, Key: str, Filename: str) -> None:
        Path(Filename).parent.mkdir(parents=True, exist_ok=True)
        Path(Filename).write_bytes(self.objects[Key])


def _patches(mlflow: FakeMLflow, s3: FakeS3) -> list:
    return [
        patch("cortexgrid.model_storage.MlflowClient", return_value=mlflow),
        patch("cortexgrid.model_storage.s3_util", s3),
        patch("cortexgrid.model_storage.get_s3_bucket", return_value="b"),
        patch(
            "cortexgrid.model_storage.get_mlflow_tracking_uri",
            return_value="http://x",
        ),
        # bundling does pip freeze + import-graph walk + an S3 upload - not
        # what these tests cover; the bundle behaviour is tested separately.
        # The built bundle matches _FAKE_BUNDLE, so re-importing keeps it.
        patch(
            "cortexgrid.model_storage.bundle_class", return_value=_FAKE_BUNDLE
        ),
        patch(
            "cortexgrid.model_storage.build_bundle",
            return_value=_serve_bundle(_FAKE_BUNDLE.fingerprint),
        ),
        patch(
            "cortexgrid.model_storage.upload_bundle", return_value=_FAKE_BUNDLE
        ),
    ]


def _seed_version(
    mlflow: FakeMLflow,
    family: str,
    suffix: str,
    run_id: str,
    run_name: str,
) -> FakeModelVersion:
    v = FakeModelVersion(
        name=f"{family}__{suffix}",
        version="1",
        source=f"s3://b/models/{run_name}/{family}/{suffix}/weights/",
        run_id=run_id,
        tags={
            "family": family,
            "suffix": suffix,
            "run_name": run_name,
            "size_bytes": "0",
        },
    )
    mlflow.versions.append(v)
    return v


class TestSaveModel(unittest.TestCase):
    def setUp(self) -> None:
        self.mlflow = FakeMLflow()
        self.s3 = FakeS3()
        for p in _patches(self.mlflow, self.s3):
            p.start()
            self.addCleanup(p.stop)
        self.weights_dir = _make_weights_dir()
        self.addCleanup(shutil.rmtree, self.weights_dir, ignore_errors=True)

    def test_uploads_weights_to_s3_under_run_first_layout(self) -> None:
        save_model(
            self.weights_dir,
            _FakeServeApp,
            "instruct",
            "Qwen2",
            run_id="r1",
            run_name="boogey-46",
        )
        keys = sorted(self.s3.objects)
        self.assertIn(
            "models/boogey-46/Qwen2/instruct/weights/config.json", keys
        )
        self.assertIn(
            "models/boogey-46/Qwen2/instruct/weights/model.safetensors", keys
        )

    def test_creates_registered_model_when_missing(self) -> None:
        save_model(
            self.weights_dir, _FakeServeApp,
            "instruct", "Qwen2",
            run_id="r1", run_name="boogey-46",
        )
        self.assertIn("Qwen2__instruct", self.mlflow.registered)

    def test_does_not_recreate_existing_registered_model(self) -> None:
        self.mlflow.registered.add("Qwen2__instruct")
        save_model(
            self.weights_dir, _FakeServeApp,
            "instruct", "Qwen2",
            run_id="r1", run_name="boogey-46",
        )
        self.assertEqual(len(self.mlflow.versions), 1)

    def test_returns_savedmodel_with_correct_fields(self) -> None:
        result = save_model(
            self.weights_dir, _FakeServeApp,
            "instruct", "Qwen2",
            run_id="r1", run_name="boogey-46",
        )
        self.assertEqual(result.family, "Qwen2")
        self.assertEqual(result.suffix, "instruct")
        self.assertEqual(result.run_name, "boogey-46")
        self.assertEqual(
            result.data_blob_path,
            "s3://b/models/boogey-46/Qwen2/instruct/weights/",
        )

    def test_creates_version_with_run_id_and_tags(self) -> None:
        save_model(
            self.weights_dir, _FakeServeApp,
            "instruct", "Qwen2",
            run_id="r1", run_name="boogey-46",
        )
        v = self.mlflow.versions[0]
        self.assertEqual(v.run_id, "r1")
        self.assertEqual(v.tags["family"], "Qwen2")
        self.assertEqual(v.tags["suffix"], "instruct")
        self.assertEqual(v.tags["run_name"], "boogey-46")

    def test_stamps_size_bytes_tag_from_uploaded_files(self) -> None:
        save_model(
            self.weights_dir, _FakeServeApp,
            "instruct", "Qwen2",
            run_id="r1", run_name="boogey-46",
        )
        v = self.mlflow.versions[0]
        # config.json '{"x": 1}' (8 bytes) + model.safetensors b"weights" (7 bytes)
        self.assertEqual(v.tags["size_bytes"], "15")

    def test_returns_savedmodel_with_size_bytes(self) -> None:
        result = save_model(
            self.weights_dir, _FakeServeApp,
            "instruct", "Qwen2",
            run_id="r1", run_name="boogey-46",
        )
        self.assertEqual(result.size_bytes, 15)

    def test_returns_savedmodel_in_ready_phase(self) -> None:
        result = save_model(
            self.weights_dir, _FakeServeApp,
            "instruct", "Qwen2",
            run_id="r1", run_name="boogey-46",
        )
        self.assertEqual(result.phase, "ready")

    def test_marks_version_ready_after_upload(self) -> None:
        save_model(
            self.weights_dir, _FakeServeApp,
            "instruct", "Qwen2",
            run_id="r1", run_name="boogey-46",
        )
        self.assertEqual(self.mlflow.versions[0].tags["lifecycle"], "ready")

    def test_registers_version_before_upload_and_marks_failed_on_error(
        self,
    ) -> None:
        with patch.object(
            self.s3, "upload_dir", side_effect=RuntimeError("network died")
        ), self.assertRaises(RuntimeError):
            save_model(
                self.weights_dir, _FakeServeApp,
                "instruct", "Qwen2",
                run_id="r1", run_name="boogey-46",
            )

        # The version exists (was registered before the upload, so the dashboard
        # can surface it) and is marked upload_failed rather than left dangling.
        self.assertEqual(len(self.mlflow.versions), 1)
        self.assertEqual(
            self.mlflow.versions[0].tags["lifecycle"], "upload_failed"
        )

    def test_stores_requirements_as_tags(self) -> None:
        save_model(
            self.weights_dir, _FakeServeApp,
            "instruct", "Qwen2",
            run_id="r1", run_name="boogey-46",
            requirements=_GPU_REQUIREMENTS,
        )
        tags = self.mlflow.versions[0].tags
        self.assertEqual(
            (tags["num_gpus"], tags["ram_gb"], tags["vram_gb"]),
            ("1", "16.0", "24.0"),
        )

    def test_returns_savedmodel_with_requirements(self) -> None:
        result = save_model(
            self.weights_dir, _FakeServeApp,
            "instruct", "Qwen2",
            run_id="r1", run_name="boogey-46",
            requirements=_GPU_REQUIREMENTS,
        )
        self.assertEqual(result.requirements, _GPU_REQUIREMENTS)

    def test_stores_no_requirement_tags_without_requirements(self) -> None:
        result = save_model(
            self.weights_dir, _FakeServeApp,
            "instruct", "Qwen2",
            run_id="r1", run_name="boogey-46",
        )
        tags = self.mlflow.versions[0].tags
        self.assertFalse({"num_gpus", "ram_gb", "vram_gb"} & tags.keys())
        self.assertEqual(result.requirements, ModelRequirements())

    def test_stores_config_as_one_json_tag(self) -> None:
        result = save_model(
            self.weights_dir, _FakeServeApp,
            "instruct", "Qwen2",
            run_id="r1", run_name="boogey-46",
            config=_CONFIG,
        )

        self.assertEqual(
            json.loads(self.mlflow.versions[0].tags["config"]), _CONFIG
        )
        self.assertEqual(result.config, _CONFIG)

    def test_stores_no_config_tag_without_config(self) -> None:
        result = save_model(
            self.weights_dir, _FakeServeApp,
            "instruct", "Qwen2",
            run_id="r1", run_name="boogey-46",
        )

        self.assertNotIn("config", self.mlflow.versions[0].tags)
        self.assertEqual(result.config, {})

    def test_rejects_a_config_that_is_not_strings(self) -> None:
        with self.assertRaises(ValueError):
            save_model(
                self.weights_dir, _FakeServeApp,
                "instruct", "Qwen2",
                run_id="r1", run_name="boogey-46",
                config={"max_tokens": 1024},  # type: ignore[dict-item]
            )


class TestImportModel(unittest.TestCase):
    def setUp(self) -> None:
        self.mlflow = FakeMLflow()
        self.s3 = FakeS3()
        for p in _patches(self.mlflow, self.s3):
            p.start()
            self.addCleanup(p.stop)
        self.weights_dir = _make_weights_dir()
        self.addCleanup(shutil.rmtree, self.weights_dir, ignore_errors=True)

    def test_registers_under_imported_run_name_without_run(self) -> None:
        result = import_model(
            self.weights_dir, _FakeServeApp, "Qwen2", "base"
        )
        self.assertEqual(result.run_name, IMPORTED)
        self.assertEqual(result.phase, "ready")
        self.assertIsNone(self.mlflow.versions[0].run_id)
        self.assertIn(
            f"models/{IMPORTED}/Qwen2/base/weights/config.json", self.s3.objects
        )

    def test_second_import_is_a_noop(self) -> None:
        import_model(self.weights_dir, _FakeServeApp, "Qwen2", "base")
        self.s3.objects.clear()

        result = import_model(self.weights_dir, _FakeServeApp, "Qwen2", "base")

        self.assertEqual(result.phase, "ready")
        self.assertEqual(len(self.mlflow.versions), 1)
        self.assertEqual(self.s3.objects, {})

    def test_calls_source_only_when_uploading(self) -> None:
        calls: list[int] = []

        def fetch() -> Path:
            calls.append(1)
            return self.weights_dir

        import_model(fetch, _FakeServeApp, "Qwen2", "base")
        import_model(fetch, _FakeServeApp, "Qwen2", "base")

        self.assertEqual(len(calls), 1)

    def test_reimport_with_unchanged_code_keeps_the_bundle(self) -> None:
        import_model(self.weights_dir, _FakeServeApp, "Qwen2", "base")

        with patch("cortexgrid.model_storage.upload_bundle") as upload_bundle:
            import_model(self.weights_dir, _FakeServeApp, "Qwen2", "base")

        upload_bundle.assert_not_called()

    def test_reimport_with_changed_code_rebundles_and_keeps_the_weights(
        self,
    ) -> None:
        import_model(self.weights_dir, _FakeServeApp, "Qwen2", "base")
        self.s3.objects.clear()
        changed = BundleMetadata(
            bundle_url="s3://b/serve-bundles/y.zip",
            class_import_path="fake.module:FakeClass",
            fingerprint="code-v2",
        )

        with (
            patch(
                "cortexgrid.model_storage.build_bundle",
                return_value=_serve_bundle("code-v2"),
            ),
            patch(
                "cortexgrid.model_storage.upload_bundle", return_value=changed
            ) as upload_bundle,
        ):
            import_model(self.weights_dir, _FakeServeApp, "Qwen2", "base")

        upload_bundle.assert_called_once_with(
            _serve_bundle("code-v2"), "Qwen2", "base", IMPORTED
        )
        self.assertEqual(len(self.mlflow.versions), 1)
        tags = self.mlflow.versions[0].tags
        self.assertEqual(tags["serve_bundle_url"], "s3://b/serve-bundles/y.zip")
        self.assertEqual(tags["serve_bundle_fingerprint"], "code-v2")
        self.assertEqual(self.s3.objects, {})

    def test_reimport_rebundles_a_model_stored_without_a_fingerprint(
        self,
    ) -> None:
        v = _seed_version(self.mlflow, "Qwen2", "base", "", IMPORTED)
        v.tags["serve_bundle_url"] = "s3://b/serve-bundles/old.zip"
        v.tags["class_import_path"] = "fake.module:FakeClass"

        import_model(self.weights_dir, _FakeServeApp, "Qwen2", "base")

        self.assertEqual(v.tags["serve_bundle_url"], _FAKE_BUNDLE.bundle_url)
        self.assertEqual(v.tags["serve_bundle_fingerprint"], "code-v1")

    def test_raises_while_another_import_is_uploading(self) -> None:
        v = _seed_version(self.mlflow, "Qwen2", "base", "", IMPORTED)
        v.tags["lifecycle"] = "uploading"
        v.creation_timestamp = _recent_ms()

        with self.assertRaises(RuntimeError):
            import_model(self.weights_dir, _FakeServeApp, "Qwen2", "base")

    def test_raises_while_another_import_is_downloading(self) -> None:
        def fetch() -> Path:
            with self.assertRaises(RuntimeError):
                import_model(self.weights_dir, _FakeServeApp, "Qwen2", "base")
            return self.weights_dir

        import_model(fetch, _FakeServeApp, "Qwen2", "base")

        self.assertEqual(len(self.mlflow.versions), 1)
        self.assertEqual(self.mlflow.versions[0].tags["lifecycle"], "ready")

    def test_marks_version_failed_when_source_raises(self) -> None:
        def fetch() -> Path:
            raise RuntimeError("download died")

        with self.assertRaises(RuntimeError):
            import_model(fetch, _FakeServeApp, "Qwen2", "base")

        self.assertEqual(len(self.mlflow.versions), 1)
        self.assertEqual(
            self.mlflow.versions[0].tags["lifecycle"], "upload_failed"
        )

    def test_reimports_after_failed_upload(self) -> None:
        v = _seed_version(self.mlflow, "Qwen2", "base", "", IMPORTED)
        v.tags["lifecycle"] = "upload_failed"

        result = import_model(self.weights_dir, _FakeServeApp, "Qwen2", "base")

        self.assertEqual(result.phase, "ready")
        self.assertEqual(len(self.mlflow.versions), 1)

    def test_reimports_after_broken_upload(self) -> None:
        # _seed_version's default creation_timestamp is past the deadline.
        v = _seed_version(self.mlflow, "Qwen2", "base", "", IMPORTED)
        v.tags["lifecycle"] = "uploading"

        result = import_model(self.weights_dir, _FakeServeApp, "Qwen2", "base")

        self.assertEqual(result.phase, "ready")
        self.assertEqual(len(self.mlflow.versions), 1)

    def test_first_import_stores_requirements(self) -> None:
        result = import_model(
            self.weights_dir, _FakeServeApp, "Qwen2", "base", _GPU_REQUIREMENTS
        )

        self.assertEqual(result.requirements, _GPU_REQUIREMENTS)
        self.assertEqual(self.mlflow.versions[0].tags["vram_gb"], "24.0")

    def test_reimport_keeps_stored_requirements(self) -> None:
        import_model(
            self.weights_dir, _FakeServeApp, "Qwen2", "base", _GPU_REQUIREMENTS
        )
        edited = ModelRequirements(num_gpus=2, ram_gb=32.0, vram_gb=48.0)
        set_model_requirements("Qwen2", "base", IMPORTED, edited)

        result = import_model(
            self.weights_dir, _FakeServeApp, "Qwen2", "base", _GPU_REQUIREMENTS
        )

        self.assertEqual(result.requirements, edited)
        self.assertEqual(self.mlflow.versions[0].tags["num_gpus"], "2")

    def test_reimport_stores_requirements_on_a_model_without_them(self) -> None:
        import_model(self.weights_dir, _FakeServeApp, "Qwen2", "base")

        result = import_model(
            self.weights_dir, _FakeServeApp, "Qwen2", "base", _GPU_REQUIREMENTS
        )

        self.assertEqual(result.requirements, _GPU_REQUIREMENTS)
        self.assertEqual(self.mlflow.versions[0].tags["ram_gb"], "16.0")

    def test_reimport_without_requirements_keeps_stored_ones(self) -> None:
        import_model(
            self.weights_dir, _FakeServeApp, "Qwen2", "base", _GPU_REQUIREMENTS
        )

        result = import_model(self.weights_dir, _FakeServeApp, "Qwen2", "base")

        self.assertEqual(result.requirements, _GPU_REQUIREMENTS)

    def test_first_import_stores_config(self) -> None:
        result = import_model(
            self.weights_dir, _FakeServeApp, "Qwen2", "base", config=_CONFIG
        )

        self.assertEqual(result.config, _CONFIG)
        self.assertEqual(
            json.loads(self.mlflow.versions[0].tags["config"]), _CONFIG
        )

    def test_reimport_keeps_stored_config(self) -> None:
        import_model(
            self.weights_dir, _FakeServeApp, "Qwen2", "base", config=_CONFIG
        )
        edited = {"model": "claude-sonnet-5"}
        set_model_config("Qwen2", "base", IMPORTED, edited)

        result = import_model(
            self.weights_dir, _FakeServeApp, "Qwen2", "base", config=_CONFIG
        )

        self.assertEqual(result.config, edited)

    def test_reimport_stores_config_on_a_model_without_it(self) -> None:
        import_model(self.weights_dir, _FakeServeApp, "Qwen2", "base")

        result = import_model(
            self.weights_dir, _FakeServeApp, "Qwen2", "base", config=_CONFIG
        )

        self.assertEqual(result.config, _CONFIG)

    def test_reimport_without_config_keeps_the_stored_one(self) -> None:
        import_model(
            self.weights_dir, _FakeServeApp, "Qwen2", "base", config=_CONFIG
        )

        result = import_model(self.weights_dir, _FakeServeApp, "Qwen2", "base")

        self.assertEqual(result.config, _CONFIG)

    def test_survives_deleting_a_run(self) -> None:
        self.mlflow.runs["r1"] = FakeRun("r1", "boogey-46")
        import_model(self.weights_dir, _FakeServeApp, "Qwen2", "base")

        delete_models_for_run("r1")

        self.assertEqual(len(self.mlflow.versions), 1)
        self.assertIn(
            f"models/{IMPORTED}/Qwen2/base/weights/config.json", self.s3.objects
        )


class TestRegisterModel(unittest.TestCase):
    """Registering a model that stages no weights - the serve-app forwards to a
    hosted API, so there is nothing to upload but its bundle."""

    def setUp(self) -> None:
        self.mlflow = FakeMLflow()
        self.s3 = FakeS3()
        for p in _patches(self.mlflow, self.s3):
            p.start()
            self.addCleanup(p.stop)

    def test_registers_under_imported_run_name_without_weights(self) -> None:
        result = register_model(_FakeServeApp, "anthropic", "opus")

        self.assertEqual(result.run_name, IMPORTED)
        self.assertEqual(result.phase, "ready")
        self.assertIsNone(self.mlflow.versions[0].run_id)
        self.assertEqual(result.data_blob_path, NO_WEIGHTS)
        self.assertEqual(result.size_bytes, 0)
        self.assertFalse(result.has_weights)
        self.assertEqual(self.s3.objects, {})

    def test_stores_the_serve_bundle(self) -> None:
        register_model(_FakeServeApp, "anthropic", "opus")

        tags = self.mlflow.versions[0].tags
        self.assertEqual(tags["serve_bundle_url"], _FAKE_BUNDLE.bundle_url)
        self.assertEqual(tags["class_import_path"], "fake.module:FakeClass")

    def test_second_registration_is_a_noop(self) -> None:
        register_model(_FakeServeApp, "anthropic", "opus")

        result = register_model(_FakeServeApp, "anthropic", "opus")

        self.assertEqual(result.phase, "ready")
        self.assertEqual(len(self.mlflow.versions), 1)

    def test_re_registration_with_changed_code_rebundles(self) -> None:
        register_model(_FakeServeApp, "anthropic", "opus")
        changed = BundleMetadata(
            bundle_url="s3://b/serve-bundles/y.zip",
            class_import_path="fake.module:FakeClass",
            fingerprint="code-v2",
        )

        with (
            patch(
                "cortexgrid.model_storage.build_bundle",
                return_value=_serve_bundle("code-v2"),
            ),
            patch(
                "cortexgrid.model_storage.upload_bundle", return_value=changed
            ) as upload_bundle,
        ):
            register_model(_FakeServeApp, "anthropic", "opus")

        upload_bundle.assert_called_once_with(
            _serve_bundle("code-v2"), "anthropic", "opus", IMPORTED
        )
        self.assertEqual(len(self.mlflow.versions), 1)

    def test_first_registration_stores_requirements_and_config(self) -> None:
        result = register_model(
            _FakeServeApp, "anthropic", "opus", _GPU_REQUIREMENTS, _CONFIG
        )

        self.assertEqual(result.requirements, _GPU_REQUIREMENTS)
        self.assertEqual(result.config, _CONFIG)
        self.assertEqual(
            json.loads(self.mlflow.versions[0].tags["config"]), _CONFIG
        )

    def test_re_registration_keeps_an_edited_config(self) -> None:
        register_model(_FakeServeApp, "anthropic", "opus", config=_CONFIG)
        edited = {"model": "claude-sonnet-5"}
        set_model_config("anthropic", "opus", IMPORTED, edited)

        result = register_model(
            _FakeServeApp, "anthropic", "opus", config=_CONFIG
        )

        self.assertEqual(result.config, edited)

    def test_raises_while_another_registration_is_in_flight(self) -> None:
        v = _seed_version(self.mlflow, "anthropic", "opus", "", IMPORTED)
        v.tags["lifecycle"] = "uploading"
        v.creation_timestamp = _recent_ms()

        with self.assertRaises(RuntimeError):
            register_model(_FakeServeApp, "anthropic", "opus")

    def test_registers_again_after_a_failed_attempt(self) -> None:
        v = _seed_version(self.mlflow, "anthropic", "opus", "", IMPORTED)
        v.tags["lifecycle"] = "upload_failed"

        result = register_model(_FakeServeApp, "anthropic", "opus")

        self.assertEqual(result.phase, "ready")
        self.assertEqual(len(self.mlflow.versions), 1)

    def test_marks_version_failed_when_bundling_raises(self) -> None:
        with patch(
            "cortexgrid.model_storage.bundle_class",
            side_effect=RuntimeError("bundling died"),
        ):
            with self.assertRaises(RuntimeError):
                register_model(_FakeServeApp, "anthropic", "opus")

        self.assertEqual(
            self.mlflow.versions[0].tags["lifecycle"], "upload_failed"
        )

    def test_listed_like_any_other_model(self) -> None:
        register_model(_FakeServeApp, "anthropic", "opus", config=_CONFIG)

        listed = list_models()

        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0].config, _CONFIG)
        self.assertFalse(listed[0].has_weights)

    def test_delete_model_drops_the_registration(self) -> None:
        register_model(_FakeServeApp, "anthropic", "opus")

        delete_model("anthropic", "opus", IMPORTED)

        self.assertEqual(self.mlflow.versions, [])
        self.assertIsNone(model_registry_status("anthropic", "opus", IMPORTED))


class TestImportModelRecordsRun(unittest.TestCase):
    """The `cortexgrid.import_model` and `cortexgrid.register_model` facades tag
    the calling run with the model it used."""

    def setUp(self) -> None:
        self.mlflow = FakeMLflow()
        self.s3 = FakeS3()
        self.run_client = MagicMock()
        for p in [
            *_patches(self.mlflow, self.s3),
            patch("cortexgrid.get_mlflow_client", return_value=self.run_client),
        ]:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(clear_instance)
        self.weights_dir = _make_weights_dir()
        self.addCleanup(shutil.rmtree, self.weights_dir, ignore_errors=True)

    def _use_run(self, run_id: str) -> None:
        set_instance(Experiment(experiment_name="experiment", run_id=run_id))

    def test_tags_every_run_that_imports_the_model(self) -> None:
        self._use_run("r1")
        model = cortexgrid.import_model(
            self.weights_dir, _FakeServeApp, "Qwen2", "base"
        )
        self._use_run("r2")
        cortexgrid.import_model(self.weights_dir, _FakeServeApp, "Qwen2", "base")

        self.assertEqual(
            self.run_client.set_tag.call_args_list,
            [
                call("r1", "imported_model/Qwen2/base", model.created_at),
                call("r2", "imported_model/Qwen2/base", model.created_at),
            ],
        )

    def test_tags_the_run_that_registers_a_weightless_model(self) -> None:
        self._use_run("r1")

        model = cortexgrid.register_model(_FakeServeApp, "anthropic", "opus")

        self.run_client.set_tag.assert_called_once_with(
            "r1", "imported_model/anthropic/opus", model.created_at
        )

    def test_does_not_tag_the_run_when_the_import_fails(self) -> None:
        self._use_run("r1")

        def fetch() -> Path:
            raise RuntimeError("download died")

        with self.assertRaises(RuntimeError):
            cortexgrid.import_model(fetch, _FakeServeApp, "Qwen2", "base")

        self.run_client.set_tag.assert_not_called()

    def test_requires_an_experiment_before_fetching(self) -> None:
        calls: list[int] = []

        def fetch() -> Path:
            calls.append(1)
            return self.weights_dir

        with self.assertRaises(ValueError):
            cortexgrid.import_model(fetch, _FakeServeApp, "Qwen2", "base")

        self.assertEqual(calls, [])


class TestLoadModel(unittest.TestCase):
    def setUp(self) -> None:
        self.mlflow = FakeMLflow()
        self.s3 = FakeS3()
        for p in _patches(self.mlflow, self.s3):
            p.start()
            self.addCleanup(p.stop)

    def test_returns_weights_dir_with_downloaded_files(self) -> None:
        _seed_version(self.mlflow, "Qwen2", "instruct", "r1", "boogey-46")
        self.s3.objects[
            "models/boogey-46/Qwen2/instruct/weights/config.json"
        ] = b'{"x":1}'

        result = load_model("Qwen2", "instruct", "boogey-46")
        self.addCleanup(shutil.rmtree, result, ignore_errors=True)

        self.assertIsInstance(result, Path)
        self.assertEqual((result / "config.json").read_bytes(), b'{"x":1}')

    def test_raises_when_no_matching_version(self) -> None:
        with self.assertRaises(ValueError):
            load_model("Qwen2", "instruct", "missing")

    def test_raises_for_a_model_registered_without_weights(self) -> None:
        register_model(_FakeServeApp, "anthropic", "opus")

        with self.assertRaises(ValueError):
            load_model("anthropic", "opus", IMPORTED)

    def test_raises_when_version_has_no_source(self) -> None:
        v = _seed_version(self.mlflow, "Qwen2", "instruct", "r1", "boogey-46")
        v.source = None
        with self.assertRaises(ValueError):
            load_model("Qwen2", "instruct", "boogey-46")


class TestListModels(unittest.TestCase):
    def setUp(self) -> None:
        self.mlflow = FakeMLflow()
        self.s3 = FakeS3()
        for p in _patches(self.mlflow, self.s3):
            p.start()
            self.addCleanup(p.stop)

    def test_returns_savedmodel_for_each_version(self) -> None:
        _seed_version(self.mlflow, "Qwen2", "instruct", "r1", "boogey-46")
        _seed_version(self.mlflow, "DeepSeek3", "chat", "r2", "rocky-99")

        result = list_models()
        families = sorted(m.family for m in result)
        self.assertEqual(families, ["DeepSeek3", "Qwen2"])

    def test_returns_empty_when_registry_is_empty(self) -> None:
        self.assertEqual(list_models(), [])

    def test_surfaces_uploading_version_with_phase(self) -> None:
        v = _seed_version(self.mlflow, "Qwen2", "instruct", "r1", "boogey-46")
        v.tags["lifecycle"] = "uploading"
        v.creation_timestamp = _recent_ms()

        result = list_models()

        self.assertEqual(result[0].phase, "uploading")

    def test_defaults_phase_to_ready_for_untagged_version(self) -> None:
        _seed_version(self.mlflow, "Qwen2", "instruct", "r1", "boogey-46")

        result = list_models()

        self.assertEqual(result[0].phase, "ready")

    def test_defaults_requirements_for_untagged_version(self) -> None:
        _seed_version(self.mlflow, "Qwen2", "instruct", "r1", "boogey-46")

        result = list_models()

        self.assertEqual(result[0].requirements, ModelRequirements())

    def test_defaults_config_for_untagged_version(self) -> None:
        _seed_version(self.mlflow, "Qwen2", "instruct", "r1", "boogey-46")

        result = list_models()

        self.assertEqual(result[0].config, {})


class TestSetModelRequirements(unittest.TestCase):
    def setUp(self) -> None:
        self.mlflow = FakeMLflow()
        self.s3 = FakeS3()
        for p in _patches(self.mlflow, self.s3):
            p.start()
            self.addCleanup(p.stop)

    def test_replaces_stored_requirements(self) -> None:
        v = _seed_version(self.mlflow, "Qwen2", "instruct", "r1", "boogey-46")
        v.tags.update({"num_gpus": "1", "ram_gb": "8.0", "vram_gb": "12.0"})

        set_model_requirements(
            "Qwen2", "instruct", "boogey-46", _GPU_REQUIREMENTS
        )

        status = model_registry_status("Qwen2", "instruct", "boogey-46")
        assert status is not None
        self.assertEqual(status.requirements, _GPU_REQUIREMENTS)

    def test_raises_when_model_was_never_registered(self) -> None:
        with self.assertRaises(ValueError):
            set_model_requirements(
                "Qwen2", "instruct", "missing", _GPU_REQUIREMENTS
            )


class TestModelConfig(unittest.TestCase):
    def setUp(self) -> None:
        self.mlflow = FakeMLflow()
        self.s3 = FakeS3()
        for p in _patches(self.mlflow, self.s3):
            p.start()
            self.addCleanup(p.stop)

    def test_reads_back_what_was_stored(self) -> None:
        _seed_version(self.mlflow, "Qwen2", "instruct", "r1", "boogey-46")

        set_model_config("Qwen2", "instruct", "boogey-46", _CONFIG)

        self.assertEqual(model_config("Qwen2", "instruct", "boogey-46"), _CONFIG)

    def test_reads_empty_config_for_a_model_without_one(self) -> None:
        _seed_version(self.mlflow, "Qwen2", "instruct", "r1", "boogey-46")

        self.assertEqual(model_config("Qwen2", "instruct", "boogey-46"), {})

    def test_replaces_the_whole_mapping(self) -> None:
        v = _seed_version(self.mlflow, "Qwen2", "instruct", "r1", "boogey-46")
        v.tags["config"] = json.dumps(_CONFIG)

        set_model_config(
            "Qwen2", "instruct", "boogey-46", {"model": "claude-sonnet-5"}
        )

        # api_key_secret was left out of the new mapping, so it is gone.
        self.assertEqual(
            model_config("Qwen2", "instruct", "boogey-46"),
            {"model": "claude-sonnet-5"},
        )

    def test_rejects_a_blank_key(self) -> None:
        _seed_version(self.mlflow, "Qwen2", "instruct", "r1", "boogey-46")

        with self.assertRaises(ValueError):
            set_model_config("Qwen2", "instruct", "boogey-46", {" ": "x"})

    def test_set_raises_when_model_was_never_registered(self) -> None:
        with self.assertRaises(ValueError):
            set_model_config("Qwen2", "instruct", "missing", _CONFIG)

    def test_read_raises_when_model_was_never_registered(self) -> None:
        with self.assertRaises(ValueError):
            model_config("Qwen2", "instruct", "missing")


class TestModelRegistryStatus(unittest.TestCase):
    def setUp(self) -> None:
        self.mlflow = FakeMLflow()
        self.s3 = FakeS3()
        for p in _patches(self.mlflow, self.s3):
            p.start()
            self.addCleanup(p.stop)

    def test_returns_none_when_never_registered(self) -> None:
        self.assertIsNone(
            model_registry_status("Qwen2", "instruct", "missing")
        )

    def test_reports_ready_for_registered_model(self) -> None:
        _seed_version(self.mlflow, "Qwen2", "instruct", "r1", "boogey-46")

        status = model_registry_status("Qwen2", "instruct", "boogey-46")

        assert status is not None
        self.assertEqual(status.phase, "ready")

    def test_reports_uploading_phase(self) -> None:
        v = _seed_version(self.mlflow, "Qwen2", "instruct", "r1", "boogey-46")
        v.tags["lifecycle"] = "uploading"
        v.creation_timestamp = _recent_ms()

        status = model_registry_status("Qwen2", "instruct", "boogey-46")

        assert status is not None
        self.assertEqual(status.phase, "uploading")

    def test_reports_broken_when_upload_exceeds_deadline(self) -> None:
        # _seed_version's default creation_timestamp is years in the past, well
        # beyond the 3h deadline, so a still-"uploading" version reads as stale.
        v = _seed_version(self.mlflow, "Qwen2", "instruct", "r1", "boogey-46")
        v.tags["lifecycle"] = "uploading"

        status = model_registry_status("Qwen2", "instruct", "boogey-46")

        assert status is not None
        self.assertEqual(status.phase, "broken")


class TestDeleteModel(unittest.TestCase):
    def setUp(self) -> None:
        self.mlflow = FakeMLflow()
        self.s3 = FakeS3()
        for p in _patches(self.mlflow, self.s3):
            p.start()
            self.addCleanup(p.stop)

    def test_removes_version_and_blob(self) -> None:
        _seed_version(self.mlflow, "Qwen2", "instruct", "r1", "boogey-46")
        self.s3.objects[
            "models/boogey-46/Qwen2/instruct/weights/x"
        ] = b"a"

        delete_model("Qwen2", "instruct", "boogey-46")

        self.assertEqual(self.mlflow.versions, [])
        self.assertEqual(list(self.s3.objects), [])

    def test_does_not_touch_unrelated_versions(self) -> None:
        _seed_version(self.mlflow, "Qwen2", "instruct", "r1", "boogey-46")
        _seed_version(self.mlflow, "Qwen2", "chat", "r2", "rocky-99")

        delete_model("Qwen2", "instruct", "boogey-46")

        remaining = sorted(v.tags["suffix"] for v in self.mlflow.versions)
        self.assertEqual(remaining, ["chat"])


class TestDeleteModelsForRun(unittest.TestCase):
    def setUp(self) -> None:
        self.mlflow = FakeMLflow()
        self.s3 = FakeS3()
        for p in _patches(self.mlflow, self.s3):
            p.start()
            self.addCleanup(p.stop)

    def test_deletes_every_version_tied_to_run(self) -> None:
        self.mlflow.runs["r1"] = FakeRun("r1", "boogey-46")
        _seed_version(self.mlflow, "Qwen2", "instruct", "r1", "boogey-46")
        _seed_version(self.mlflow, "Qwen2", "chat", "r1", "boogey-46")
        _seed_version(self.mlflow, "DeepSeek3", "chat", "r2", "rocky-99")
        self.s3.objects["models/boogey-46/Qwen2/instruct/weights/x"] = b"a"
        self.s3.objects["models/boogey-46/Qwen2/chat/weights/y"] = b"b"
        self.s3.objects["models/rocky-99/DeepSeek3/chat/weights/z"] = b"c"

        delete_models_for_run("r1")

        run_ids = sorted(v.run_id for v in self.mlflow.versions)
        self.assertEqual(run_ids, ["r2"])
        self.assertEqual(
            sorted(self.s3.objects),
            ["models/rocky-99/DeepSeek3/chat/weights/z"],
        )

    def test_falls_back_to_run_id_when_run_name_missing(self) -> None:
        self.mlflow.runs["r1"] = FakeRun("r1", "")
        _seed_version(self.mlflow, "Qwen2", "instruct", "r1", "r1")
        self.s3.objects["models/r1/Qwen2/instruct/weights/x"] = b"a"

        delete_models_for_run("r1")
        self.assertEqual(list(self.s3.objects), [])


if __name__ == "__main__":
    unittest.main()
