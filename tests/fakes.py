"""In-memory fakes for the infrastructure cortexgrid talks to.

Tests using these fakes assert on observable post-state of S3, MLflow,
Ray and Postgres rather than on which helper functions were called.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from mlflow.exceptions import MlflowException


@dataclass
class FakeS3:
    objects: dict[str, bytes] = field(default_factory=dict)
    exceptions: SimpleNamespace = field(
        default_factory=lambda: SimpleNamespace(ClientError=Exception)
    )

    def head_bucket(self, Bucket: str) -> dict[str, Any]:
        return {}

    def head_object(self, Bucket: str, Key: str) -> dict[str, Any]:
        if Key not in self.objects:
            raise Exception(f"NoSuchKey: {Key}")
        return {"ContentLength": len(self.objects[Key])}

    def list_objects_v2(
        self,
        Bucket: str,
        Prefix: str = "",
        ContinuationToken: str | None = None,
    ) -> dict[str, Any]:
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        return {"Contents": [{"Key": k} for k in keys]} if keys else {}

    def get_paginator(self, op: str) -> "_FakeS3Paginator":
        return _FakeS3Paginator(self, op)

    def delete_object(self, Bucket: str, Key: str) -> dict[str, Any]:
        self.objects.pop(Key, None)
        return {}

    def delete_objects(
        self, Bucket: str, Delete: dict[str, Any]
    ) -> dict[str, Any]:
        for entry in Delete.get("Objects", []):
            self.objects.pop(entry["Key"], None)
        return {}


class _FakeS3Paginator:
    def __init__(self, s3: FakeS3, op: str) -> None:
        self.s3 = s3
        self.op = op

    def paginate(self, Bucket: str, Prefix: str = "") -> list[dict[str, Any]]:
        page = self.s3.list_objects_v2(Bucket=Bucket, Prefix=Prefix)
        return [page] if page else [{}]


@dataclass
class FakeMlflowExperiment:
    experiment_id: str
    name: str
    lifecycle_stage: str = "active"
    tags: dict[str, str] = field(default_factory=dict)
    creation_time: int | None = None


@dataclass
class FakeMlflowRun:
    run_id: str
    run_name: str
    experiment_id: str
    lifecycle_stage: str = "active"
    tags: dict[str, str] = field(default_factory=dict)
    start_time: int | None = None
    end_time: int | None = None

    @property
    def info(self) -> SimpleNamespace:
        return SimpleNamespace(
            run_id=self.run_id,
            run_name=self.run_name,
            experiment_id=self.experiment_id,
            lifecycle_stage=self.lifecycle_stage,
            start_time=self.start_time,
            end_time=self.end_time,
        )

    @property
    def data(self) -> SimpleNamespace:
        return SimpleNamespace(tags=dict(self.tags))


@dataclass
class FakeArtifact:
    path: str
    is_dir: bool


@dataclass
class FakeMlflowModelVersion:
    name: str
    version: str
    source: str | None
    run_id: str
    tags: dict[str, str] = field(default_factory=dict)
    creation_timestamp: int = 1700000000000


@dataclass
class FakeMlflowClient:
    """Tracks soft-delete state via lifecycle_stage like real mlflow does."""

    experiments: list[FakeMlflowExperiment] = field(default_factory=list)
    runs: list[FakeMlflowRun] = field(default_factory=list)
    artifacts: dict[str, list[FakeArtifact]] = field(default_factory=dict)
    model_versions: list[FakeMlflowModelVersion] = field(default_factory=list)
    registered_models: set[str] = field(default_factory=set)

    def __init__(self, *, tracking_uri: str = "", **_: Any) -> None:
        self.experiments = []
        self.runs = []
        self.artifacts = {}
        self.model_versions = []
        self.registered_models = set()
        self._next_version = 1
        # Ids for records created through the client, well clear of seeded ones.
        self._next_created = 100

    def seed(
        self,
        experiments: list[FakeMlflowExperiment] | None = None,
        runs: list[FakeMlflowRun] | None = None,
        artifacts: dict[str, list[FakeArtifact]] | None = None,
        model_versions: list[FakeMlflowModelVersion] | None = None,
    ) -> "FakeMlflowClient":
        self.experiments = list(experiments or [])
        self.runs = list(runs or [])
        self.artifacts = dict(artifacts or {})
        self.model_versions = list(model_versions or [])
        self.registered_models = {v.name for v in self.model_versions}
        return self

    def search_experiments(self, **_: Any) -> list[FakeMlflowExperiment]:
        return [e for e in self.experiments if e.lifecycle_stage == "active"]

    def search_runs(
        self,
        experiment_ids: list[str],
        filter_string: str = "",
        **_: Any,
    ) -> list[FakeMlflowRun]:
        result = [
            r
            for r in self.runs
            if r.experiment_id in experiment_ids and r.lifecycle_stage == "active"
        ]
        if "attributes.run_name = '" in filter_string:
            wanted = filter_string.split("attributes.run_name = '")[1].split("'")[0]
            result = [r for r in result if r.run_name == wanted]
        return result

    def create_experiment(self, name: str) -> str:
        self._next_created += 1
        experiment = FakeMlflowExperiment(
            experiment_id=f"e{self._next_created}", name=name
        )
        self.experiments.append(experiment)
        return experiment.experiment_id

    def create_run(self, experiment_id: str, run_name: str) -> FakeMlflowRun:
        self._next_created += 1
        run = FakeMlflowRun(
            run_id=f"run-{self._next_created}",
            run_name=run_name,
            experiment_id=experiment_id,
        )
        self.runs.append(run)
        return run

    def get_experiment(self, experiment_id: str) -> FakeMlflowExperiment:
        for e in self.experiments:
            if e.experiment_id == experiment_id:
                return e
        raise KeyError(experiment_id)

    def list_artifacts(self, run_id: str, path: str = "") -> list[FakeArtifact]:
        all_for_run = self.artifacts.get(run_id, [])
        if not path:
            return list(all_for_run)
        return [a for a in all_for_run if a.path.startswith(f"{path}/") or a.path == path]

    def get_run(self, run_id: str) -> FakeMlflowRun:
        for r in self.runs:
            if r.run_id == run_id:
                return r
        raise KeyError(run_id)

    def get_experiment_by_name(self, name: str) -> FakeMlflowExperiment | None:
        # Like mlflow: deleted experiments are returned too.
        for e in self.experiments:
            if e.name == name:
                return e
        return None

    def rename_experiment(self, experiment_id: str, new_name: str) -> None:
        e = self.get_experiment(experiment_id)
        if e.lifecycle_stage != "active":
            raise ValueError("Cannot rename a non-active experiment.")
        e.name = new_name

    def restore_experiment(self, experiment_id: str) -> None:
        self.get_experiment(experiment_id).lifecycle_stage = "active"

    def set_tag(self, run_id: str, key: str, value: str) -> None:
        for r in self.runs:
            if r.run_id == run_id:
                r.tags[key] = value
                return
        raise KeyError(run_id)

    def set_experiment_tag(self, experiment_id: str, key: str, value: str) -> None:
        self.get_experiment(experiment_id).tags[key] = value

    def delete_run(self, run_id: str) -> None:
        for r in self.runs:
            if r.run_id == run_id:
                r.lifecycle_stage = "deleted"
                return
        raise KeyError(run_id)

    def delete_experiment(self, experiment_id: str) -> None:
        for e in self.experiments:
            if e.experiment_id == experiment_id:
                e.lifecycle_stage = "deleted"
                return
        raise KeyError(experiment_id)

    def search_model_versions(
        self, filter_string: str = ""
    ) -> list[FakeMlflowModelVersion]:
        result = list(self.model_versions)
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

    def create_registered_model(self, name: str) -> SimpleNamespace:
        if name in self.registered_models:
            raise MlflowException("RESOURCE_ALREADY_EXISTS")
        self.registered_models.add(name)
        return SimpleNamespace(name=name)

    def get_registered_model(self, name: str) -> SimpleNamespace:
        if name not in self.registered_models:
            raise MlflowException("RESOURCE_DOES_NOT_EXIST")
        return SimpleNamespace(name=name)

    def create_model_version(
        self,
        name: str,
        source: str,
        run_id: str,
        tags: dict[str, str] | None = None,
    ) -> FakeMlflowModelVersion:
        v = FakeMlflowModelVersion(
            name=name,
            version=str(self._next_version),
            source=source,
            run_id=run_id,
            tags=dict(tags or {}),
        )
        self._next_version += 1
        self.model_versions.append(v)
        return v

    def set_model_version_tag(
        self, name: str, version: str, key: str, value: str
    ) -> None:
        for v in self.model_versions:
            if v.name == name and v.version == version:
                v.tags[key] = value
                return
        raise MlflowException("RESOURCE_DOES_NOT_EXIST")

    def delete_model_version(self, name: str, version: str) -> None:
        self.model_versions = [
            v for v in self.model_versions
            if not (v.name == name and v.version == version)
        ]


@dataclass
class FakeRayJob:
    submission_id: str
    status: str = "RUNNING"


class FakeRay:
    def __init__(self, jobs: dict[str, str] | None = None) -> None:
        self.jobs: dict[str, FakeRayJob] = {
            sid: FakeRayJob(sid, status) for sid, status in (jobs or {}).items()
        }

    def list_jobs(self) -> list[FakeRayJob]:
        return list(self.jobs.values())

    def stop_job(self, submission_id: str) -> None:
        if submission_id in self.jobs:
            self.jobs[submission_id].status = "STOPPED"

    def get_job_status(self, submission_id: str) -> Any:
        return SimpleNamespace(value=self.jobs[submission_id].status)


@dataclass
class _NotesRow:
    table: str
    columns: dict[str, Any]


class FakeNotesDB:
    """Minimal psycopg-shaped fake for the notes DB.

    Holds two lists of rows keyed by table name. Supports the SQL
    statements the notes module actually issues; unrecognised statements
    raise so test failures are loud.
    """

    def __init__(
        self,
        run_notes: list[dict[str, Any]] | None = None,
        experiment_notes: list[dict[str, Any]] | None = None,
    ) -> None:
        self.run_notes: list[dict[str, Any]] = list(run_notes or [])
        self.experiment_notes: list[dict[str, Any]] = list(experiment_notes or [])

    def __call__(self, *_: Any, **__: Any) -> "FakeNotesDB":
        return self

    def __enter__(self) -> "FakeNotesDB":
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def cursor(self) -> "_FakeCursor":
        return _FakeCursor(self)


class _FakeCursor:
    def __init__(self, db: FakeNotesDB) -> None:
        self.db = db
        self._result: list[tuple[Any, ...]] = []

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        norm = " ".join(sql.split())
        if norm.startswith("DELETE FROM run_notes WHERE run_id ="):
            (run_id,) = params
            self.db.run_notes = [
                r for r in self.db.run_notes if r["run_id"] != run_id
            ]
            self._result = []
            return
        if norm.startswith("DELETE FROM experiment_notes WHERE experiment_name ="):
            (name,) = params
            self.db.experiment_notes = [
                r for r in self.db.experiment_notes if r["experiment_name"] != name
            ]
            self._result = []
            return
        raise NotImplementedError(f"FakeNotesDB does not handle: {norm!r}")

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._result[0] if self._result else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._result)
