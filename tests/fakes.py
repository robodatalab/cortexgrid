"""In-memory fakes for the infrastructure cortexgrid talks to.

Tests using these fakes assert on observable post-state of S3, MLflow,
Ray, the jobs control plane's records and Postgres rather than on which
helper functions were called.
"""

from __future__ import annotations

import json
import time
import unittest
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import requests  # type: ignore

from cortexgrid import state


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


@dataclass
class FakeMlflowRun:
    run_id: str
    run_name: str
    experiment_id: str
    lifecycle_stage: str = "active"

    @property
    def info(self) -> SimpleNamespace:
        return SimpleNamespace(
            run_id=self.run_id,
            run_name=self.run_name,
            experiment_id=self.experiment_id,
            lifecycle_stage=self.lifecycle_stage,
        )


@dataclass
class FakeMlflowClient:
    """The MLflow side of an experiment or run: soft-deleted via
    lifecycle_stage, like real MLflow."""

    experiments: list[FakeMlflowExperiment] = field(default_factory=list)
    runs: list[FakeMlflowRun] = field(default_factory=list)

    def __init__(self, *, tracking_uri: str = "", **_: Any) -> None:
        self.experiments = []
        self.runs = []

    def get_experiment(self, experiment_id: str) -> FakeMlflowExperiment:
        for e in self.experiments:
            if e.experiment_id == experiment_id:
                return e
        raise KeyError(experiment_id)

    def get_run(self, run_id: str) -> FakeMlflowRun:
        for r in self.runs:
            if r.run_id == run_id:
                return r
        raise KeyError(run_id)

    def delete_run(self, run_id: str) -> None:
        self.get_run(run_id).lifecycle_stage = "deleted"

    def delete_experiment(self, experiment_id: str) -> None:
        self.get_experiment(experiment_id).lifecycle_stage = "deleted"


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


_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)
_TERMINAL = ("finished", "stopped")


def _json(value: Any) -> Any:
    """What `value` looks like after a trip through the API's JSON."""
    return json.loads(json.dumps(value))


def _not_found(segments: tuple[str, ...]) -> requests.HTTPError:
    return requests.HTTPError(f"404 Not Found: /{'/'.join(segments)}")


class FakeState:
    """In-memory stand-in for the jobs control plane's state API
    (jobs_control_plane/api.py over jobs_control_plane/db.py), installed over
    the functions of `cortexgrid.state`.

    Mirrors the routes and their semantics: a missing record reads as None,
    a write under a run that has no record fails like the API's 404, the
    stop latch never clears, deleting a run cascades to everything recorded
    under it, and `jobs/open` leaves out the jobs that are done for good.
    Tests seed and assert on the dicts below directly; `seed_*` helpers fill
    in what the API would have stored.
    """

    def __init__(self) -> None:
        self.experiments: dict[str, dict[str, Any]] = {}
        self.runs: dict[str, dict[str, Any]] = {}
        self.jobs: dict[tuple[str, str], dict[str, Any]] = {}
        self.manifests: dict[tuple[str, str], dict[str, Any]] = {}
        self.results: dict[tuple[str, str], bytes] = {}
        self.checkpoints: dict[tuple[str, str], dict[str, Any]] = {}
        self.imported_models: dict[tuple[str, str, str], str] = {}
        self.models: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.deployments: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        self._clock = 0

    def install(self, test: unittest.TestCase) -> "FakeState":
        patcher = patch.multiple(
            state,
            get=self.get,
            get_bytes=self.get_bytes,
            put=self.put,
            put_bytes=self.put_bytes,
            patch=self.patch,
            post=self.post,
            delete=self.delete,
        )
        patcher.start()
        test.addCleanup(patcher.stop)
        return self

    def _now(self) -> str:
        self._clock += 1
        return (_EPOCH + timedelta(seconds=self._clock)).isoformat()

    # Seeding

    def seed_run(
        self, run_id: str, run_name: str | None = None, experiment_name: str = "exp"
    ) -> None:
        if experiment_name not in self.experiments:
            self.seed_experiment(experiment_name)
        self.runs[run_id] = {
            "run_id": run_id,
            "run_name": run_name or run_id,
            "experiment_name": experiment_name,
            "created_at": self._now(),
        }

    def seed_experiment(self, name: str, mlflow_experiment_id: str = "1") -> None:
        self.experiments[name] = {
            "name": name,
            "mlflow_experiment_id": mlflow_experiment_id,
            "created_at": self._now(),
        }

    def seed_job(self, lifecycle: Any) -> None:
        """Record a cortexgrid.jobs.JobLifecycle as its save would."""
        self.put("runs", lifecycle.run_id, "jobs", lifecycle.job_id, body=asdict(lifecycle))

    def seed_model(
        self,
        family: str,
        suffix: str,
        run_name: str,
        source: str,
        tags: dict[str, str],
        run_id: str | None = None,
        creation_timestamp: int | None = None,
    ) -> None:
        self.models[(family, suffix, run_name)] = {
            "tags": {"family": family, "suffix": suffix, "run_name": run_name, **tags},
            "source": source,
            "run_id": run_id,
            "creation_timestamp": (
                int(time.time() * 1000) if creation_timestamp is None else creation_timestamp
            ),
        }

    # cortexgrid.state

    def get(self, *segments: str, params: dict[str, str] | None = None) -> Any:
        params = params or {}
        match segments:
            case ("experiments",):
                return _json(list(self.experiments.values()))
            case ("experiments", name):
                return _json(self.experiments.get(name))
            case ("runs",):
                return _json([
                    run for run in self.runs.values()
                    if params.get("experiment_name") in (None, run["experiment_name"])
                    and params.get("run_name") in (None, run["run_name"])
                ])
            case ("runs", run_id):
                return _json(self.runs.get(run_id))
            case ("runs", run_id, "jobs"):
                return _json([j for (r, _), j in self.jobs.items() if r == run_id])
            case ("jobs", "open"):
                return _json([j for j in self.jobs.values() if not _job_done(j)])
            case ("runs", run_id, "jobs", job_id):
                return _json(self.jobs.get((run_id, job_id)))
            case ("runs", run_id, "jobs", job_id, "manifest"):
                return _json(self.manifests.get((run_id, job_id)))
            case ("runs", run_id, "checkpoints", prefix):
                return _json(self.checkpoints.get((run_id, prefix)))
            case ("models",):
                return _json([
                    m for m in self.models.values()
                    if params.get("run_id") in (None, m["run_id"])
                ])
            case ("models", family, suffix, run_name):
                return _json(self.models.get((family, suffix, run_name)))
            case ("deployments",):
                return _json(list(self.deployments.values()))
            case ("deployments", family, suffix, run_name):
                return _json(self.deployments.get(
                    (family, suffix, run_name, params.get("config_fingerprint", ""))
                ))
        raise NotImplementedError(f"FakeState has no GET /{'/'.join(segments)}")

    def get_bytes(self, *segments: str) -> bytes | None:
        match segments:
            case ("runs", run_id, "jobs", job_id, "result"):
                return self.results.get((run_id, job_id))
        raise NotImplementedError(f"FakeState has no GET /{'/'.join(segments)}")

    def put(
        self, *segments: str, body: Any, params: dict[str, str] | None = None
    ) -> Any:
        body = _json(body)
        params = params or {}
        match segments:
            case ("experiments", name):
                if name not in self.experiments:
                    self.seed_experiment(name, body["mlflow_experiment_id"])
                return _json(self.experiments[name])
            case ("runs", run_id):
                self._require_experiment(segments, body["experiment_name"])
                self.runs[run_id] = {
                    "run_id": run_id,
                    **body,
                    "created_at": self.runs.get(run_id, {}).get("created_at", self._now()),
                }
                return None
            case ("runs", run_id, "imported-models", family, suffix):
                self._require_run(segments, run_id)
                self.imported_models[(run_id, family, suffix)] = body["created_at"]
                return None
            case ("runs", run_id, "jobs", job_id):
                self._require_run(segments, run_id)
                existing = self.jobs.get((run_id, job_id))
                if existing is None:
                    self.jobs[(run_id, job_id)] = {**body, "run_id": run_id, "job_id": job_id}
                else:
                    existing["history"] = body["history"]
                    existing["stop_requested"] = (
                        existing["stop_requested"] or body["stop_requested"]
                    )
                return None
            case ("runs", run_id, "jobs", job_id, "manifest"):
                self._require_run(segments, run_id)
                self.manifests[(run_id, job_id)] = body
                return None
            case ("runs", run_id, "checkpoints", prefix):
                self._require_run(segments, run_id)
                self.checkpoints[(run_id, prefix)] = body
                return None
            case ("models", family, suffix, run_name):
                if body["run_id"] is not None:
                    self._require_run(segments, body["run_id"])
                self.models[(family, suffix, run_name)] = {
                    **body,
                    "creation_timestamp": int(time.time() * 1000),
                }
                return None
            case ("deployments", family, suffix, run_name):
                config_fingerprint = params.get("config_fingerprint", "")
                self.deployments[(family, suffix, run_name, config_fingerprint)] = {
                    "family": family,
                    "suffix": suffix,
                    "run_name": run_name,
                    "config_fingerprint": config_fingerprint,
                    "config": {},
                    "replaced_bundle_fingerprint": "",
                    **body,
                }
                return None
        raise NotImplementedError(f"FakeState has no PUT /{'/'.join(segments)}")

    def put_bytes(self, *segments: str, body: bytes) -> None:
        match segments:
            case ("runs", run_id, "jobs", job_id, "result"):
                self._require_run(segments, run_id)
                self.results[(run_id, job_id)] = bytes(body)
                return
        raise NotImplementedError(f"FakeState has no PUT /{'/'.join(segments)}")

    def patch(
        self, *segments: str, body: Any, params: dict[str, str] | None = None
    ) -> bool:
        body = _json(body)
        params = params or {}
        match segments:
            case ("models", family, suffix, run_name, "tags"):
                model = self.models.get((family, suffix, run_name))
                if model is None:
                    return False
                model["tags"].update(body)
                return True
            case ("deployments", family, suffix, run_name):
                deployment = self.deployments.get(
                    (family, suffix, run_name, params.get("config_fingerprint", ""))
                )
                if deployment is None:
                    return False
                deployment.update(body)
                return True
        raise NotImplementedError(f"FakeState has no PATCH /{'/'.join(segments)}")

    def post(self, *segments: str) -> None:
        match segments:
            case ("runs", run_id, "stop"):
                for (r, _), job in self.jobs.items():
                    if r == run_id:
                        job["stop_requested"] = True
                return
        raise NotImplementedError(f"FakeState has no POST /{'/'.join(segments)}")

    def delete(self, *segments: str, params: dict[str, str] | None = None) -> None:
        match segments:
            case ("experiments", name):
                for run_id in [r for r, run in self.runs.items() if run["experiment_name"] == name]:
                    self._delete_run(run_id)
                self.experiments.pop(name, None)
                return
            case ("runs", run_id):
                self._delete_run(run_id)
                return
            case ("models",):
                run_id = (params or {})["run_id"]
                self.models = {k: m for k, m in self.models.items() if m["run_id"] != run_id}
                return
            case ("models", family, suffix, run_name):
                self.models.pop((family, suffix, run_name), None)
                return
            case ("deployments", family, suffix, run_name):
                self.deployments.pop(
                    (family, suffix, run_name, (params or {}).get("config_fingerprint", "")),
                    None,
                )
                return
        raise NotImplementedError(f"FakeState has no DELETE /{'/'.join(segments)}")

    def _delete_run(self, run_id: str) -> None:
        """The run and, as the schema's ON DELETE CASCADE does, all under it."""
        self.runs.pop(run_id, None)
        for table in (self.jobs, self.manifests, self.results, self.checkpoints):
            for key in [k for k in table if k[0] == run_id]:
                del table[key]
        for key in [k for k in self.imported_models if k[0] == run_id]:
            del self.imported_models[key]
        self.models = {k: m for k, m in self.models.items() if m["run_id"] != run_id}

    def _require_run(self, segments: tuple[str, ...], run_id: str) -> None:
        if run_id not in self.runs:
            raise _not_found(segments)

    def _require_experiment(self, segments: tuple[str, ...], name: str) -> None:
        if name not in self.experiments:
            raise _not_found(segments)


def _job_done(job: dict[str, Any]) -> bool:
    """The schema's generated `jobs.done` column."""
    if not job["history"]:
        return False
    last = job["history"][-1]["state"]
    return last in _TERMINAL or (last == "failed" and not job["retry"])
