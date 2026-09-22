"""cortexgrid's own records in the `cortexgrid` Postgres database.

The control plane is the only reader and writer; everyone else goes through
its state API (jobs_control_plane/api.py). Schema:
k8s/charts/cortexgrid/files/postgres/cortexgrid_schema.sql.
"""

from __future__ import annotations

import functools
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from cortexgrid.secrets import get_secret


Row = dict[str, Any]

_JOB_COLUMNS = (
    "experiment_name, run_id, job_id, stop_requested, retry, "
    "num_gpus, num_cpus, pip_requirements, history"
)
# Registry entries carry their creation time as ms since the epoch, which is
# how cortexgrid.model_storage reads it.
_MODEL_COLUMNS = (
    "tags, source, run_id, "
    "(extract(epoch FROM created_at) * 1000)::bigint AS creation_timestamp"
)
_DEPLOYMENT_COLUMNS = (
    "family, suffix, run_name, spec, tiers, url, phase, message, replicas, "
    "replaced_bundle_fingerprint"
)


@functools.cache
def _pool() -> ConnectionPool:
    return ConnectionPool(
        get_secret("CORTEXGRID_DB_URI"),
        kwargs={"row_factory": dict_row},
        open=True,
    )


def _all(sql: str, params: Any = ()) -> list[Row]:
    with _pool().connection() as conn:
        return conn.execute(sql, params).fetchall()


def _one(sql: str, params: Any = ()) -> Row | None:
    with _pool().connection() as conn:
        return conn.execute(sql, params).fetchone()


def _write(sql: str, params: Any = ()) -> int:
    """Execute a write; returns the number of rows it touched."""
    with _pool().connection() as conn:
        return conn.execute(sql, params).rowcount


# Experiments and runs


def put_experiment(name: str, mlflow_experiment_id: str) -> Row:
    """Record an experiment unless one of that name exists; returns the one
    recorded, so a concurrent creator learns which MLflow experiment won."""
    _write(
        "INSERT INTO experiments (name, mlflow_experiment_id) VALUES (%s, %s) "
        "ON CONFLICT (name) DO NOTHING",
        (name, mlflow_experiment_id),
    )
    experiment = get_experiment(name)
    assert experiment is not None
    return experiment


def get_experiment(name: str) -> Row | None:
    return _one(
        "SELECT name, mlflow_experiment_id, created_at FROM experiments "
        "WHERE name = %s",
        (name,),
    )


def list_experiments() -> list[Row]:
    return _all(
        "SELECT name, mlflow_experiment_id, created_at FROM experiments "
        "ORDER BY created_at"
    )


def delete_experiment(name: str) -> None:
    _write("DELETE FROM experiments WHERE name = %s", (name,))


def put_run(run_id: str, run_name: str, experiment_name: str) -> None:
    _write(
        "INSERT INTO runs (run_id, run_name, experiment_name) VALUES (%s, %s, %s) "
        "ON CONFLICT (run_id) DO UPDATE SET run_name = EXCLUDED.run_name, "
        "experiment_name = EXCLUDED.experiment_name",
        (run_id, run_name, experiment_name),
    )


def get_run(run_id: str) -> Row | None:
    return _one(
        "SELECT run_id, run_name, experiment_name, created_at FROM runs "
        "WHERE run_id = %s",
        (run_id,),
    )


def list_runs(experiment_name: str | None, run_name: str | None) -> list[Row]:
    return _all(
        "SELECT run_id, run_name, experiment_name, created_at FROM runs "
        "WHERE (%(experiment_name)s::text IS NULL OR experiment_name = %(experiment_name)s) "
        "AND (%(run_name)s::text IS NULL OR run_name = %(run_name)s) "
        "ORDER BY created_at",
        {"experiment_name": experiment_name, "run_name": run_name},
    )


def delete_run(run_id: str) -> None:
    """Delete a run and, by cascade, everything recorded under it."""
    _write("DELETE FROM runs WHERE run_id = %s", (run_id,))


def put_imported_model(run_id: str, family: str, suffix: str, created_at: str) -> None:
    _write(
        "INSERT INTO run_imported_models (run_id, family, suffix, created_at) "
        "VALUES (%s, %s, %s, %s) ON CONFLICT (run_id, family, suffix) "
        "DO UPDATE SET created_at = EXCLUDED.created_at",
        (run_id, family, suffix, created_at),
    )


# Jobs


def put_job(lifecycle: dict[str, Any]) -> None:
    """Insert a lifecycle, or record a new history on an existing one. Its
    other fields are fixed at creation, and the stop latch only ever sets."""
    _write(
        f"INSERT INTO jobs ({_JOB_COLUMNS}) VALUES ("
        "%(experiment_name)s, %(run_id)s, %(job_id)s, %(stop_requested)s, "
        "%(retry)s, %(num_gpus)s, %(num_cpus)s, %(pip_requirements)s, %(history)s) "
        "ON CONFLICT (run_id, job_id) DO UPDATE SET "
        "stop_requested = jobs.stop_requested OR EXCLUDED.stop_requested, "
        "history = EXCLUDED.history",
        {
            **lifecycle,
            "pip_requirements": Jsonb(lifecycle["pip_requirements"]),
            "history": Jsonb(lifecycle["history"]),
        },
    )


def get_job(run_id: str, job_id: str) -> Row | None:
    return _one(
        f"SELECT {_JOB_COLUMNS} FROM jobs WHERE run_id = %s AND job_id = %s",
        (run_id, job_id),
    )


def list_jobs(run_id: str) -> list[Row]:
    return _all(
        f"SELECT {_JOB_COLUMNS} FROM jobs WHERE run_id = %s ORDER BY created_at",
        (run_id,),
    )


def list_open_jobs() -> list[Row]:
    return _all(f"SELECT {_JOB_COLUMNS} FROM jobs WHERE NOT done ORDER BY created_at")


def stop_run(run_id: str) -> None:
    _write("UPDATE jobs SET stop_requested = true WHERE run_id = %s", (run_id,))


def put_manifest(run_id: str, job_id: str, code_tarball_uri: str) -> None:
    _write(
        "INSERT INTO job_manifests (run_id, job_id, code_tarball_uri) "
        "VALUES (%s, %s, %s) ON CONFLICT (run_id, job_id) "
        "DO UPDATE SET code_tarball_uri = EXCLUDED.code_tarball_uri",
        (run_id, job_id, code_tarball_uri),
    )


def get_manifest(run_id: str, job_id: str) -> Row | None:
    return _one(
        "SELECT code_tarball_uri FROM job_manifests WHERE run_id = %s AND job_id = %s",
        (run_id, job_id),
    )


def put_result(run_id: str, job_id: str, result: bytes) -> None:
    _write(
        "INSERT INTO job_results (run_id, job_id, result) VALUES (%s, %s, %s) "
        "ON CONFLICT (run_id, job_id) DO UPDATE SET result = EXCLUDED.result",
        (run_id, job_id, result),
    )


def get_result(run_id: str, job_id: str) -> bytes | None:
    row = _one(
        "SELECT result FROM job_results WHERE run_id = %s AND job_id = %s",
        (run_id, job_id),
    )
    return None if row is None else bytes(row["result"])


def put_checkpoint(run_id: str, prefix: str, manifest: dict[str, Any]) -> None:
    _write(
        "INSERT INTO checkpoints (run_id, prefix, manifest) VALUES (%s, %s, %s) "
        "ON CONFLICT (run_id, prefix) DO UPDATE SET manifest = EXCLUDED.manifest",
        (run_id, prefix, Jsonb(manifest)),
    )


def get_checkpoint(run_id: str, prefix: str) -> dict[str, Any] | None:
    row = _one(
        "SELECT manifest FROM checkpoints WHERE run_id = %s AND prefix = %s",
        (run_id, prefix),
    )
    return None if row is None else row["manifest"]


# Model registry


def put_model(
    family: str,
    suffix: str,
    run_name: str,
    run_id: str | None,
    source: str,
    tags: dict[str, str],
) -> None:
    """Register an entry, replacing any under the same key."""
    _write(
        "INSERT INTO models (family, suffix, run_name, run_id, source, tags) "
        "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (family, suffix, run_name) "
        "DO UPDATE SET run_id = EXCLUDED.run_id, source = EXCLUDED.source, "
        "tags = EXCLUDED.tags, created_at = now()",
        (family, suffix, run_name, run_id, source, Jsonb(tags)),
    )


def get_model(family: str, suffix: str, run_name: str) -> Row | None:
    return _one(
        f"SELECT {_MODEL_COLUMNS} FROM models "
        "WHERE family = %s AND suffix = %s AND run_name = %s",
        (family, suffix, run_name),
    )


def list_models(run_id: str | None) -> list[Row]:
    return _all(
        f"SELECT {_MODEL_COLUMNS} FROM models "
        "WHERE %(run_id)s::text IS NULL OR run_id = %(run_id)s ORDER BY created_at",
        {"run_id": run_id},
    )


def patch_model_tags(
    family: str, suffix: str, run_name: str, tags: dict[str, str]
) -> bool:
    """Merge `tags` into the entry's; False if there is no such entry."""
    return (
        _write(
            "UPDATE models SET tags = tags || %s "
            "WHERE family = %s AND suffix = %s AND run_name = %s",
            (Jsonb(tags), family, suffix, run_name),
        )
        > 0
    )


def delete_model(family: str, suffix: str, run_name: str) -> None:
    _write(
        "DELETE FROM models WHERE family = %s AND suffix = %s AND run_name = %s",
        (family, suffix, run_name),
    )


def delete_models_for_run(run_id: str) -> None:
    _write("DELETE FROM models WHERE run_id = %s", (run_id,))


# Deployments


def put_deployment(
    family: str,
    suffix: str,
    run_name: str,
    spec: dict[str, Any],
    tiers: list[int],
    url: str,
    phase: str,
    message: str,
    replicas: list[dict[str, Any]],
    replaced_bundle_fingerprint: str,
) -> None:
    _write(
        f"INSERT INTO deployments ({_DEPLOYMENT_COLUMNS}) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (family, suffix, run_name) DO UPDATE SET "
        "spec = EXCLUDED.spec, tiers = EXCLUDED.tiers, url = EXCLUDED.url, "
        "phase = EXCLUDED.phase, message = EXCLUDED.message, "
        "replicas = EXCLUDED.replicas, "
        "replaced_bundle_fingerprint = EXCLUDED.replaced_bundle_fingerprint, "
        "deployed_at = now()",
        (
            family,
            suffix,
            run_name,
            Jsonb(spec),
            Jsonb(tiers),
            url,
            phase,
            message,
            Jsonb(replicas),
            replaced_bundle_fingerprint,
        ),
    )


def get_deployment(family: str, suffix: str, run_name: str) -> Row | None:
    return _one(
        f"SELECT {_DEPLOYMENT_COLUMNS} FROM deployments "
        "WHERE family = %s AND suffix = %s AND run_name = %s",
        (family, suffix, run_name),
    )


def list_deployments() -> list[Row]:
    return _all(f"SELECT {_DEPLOYMENT_COLUMNS} FROM deployments ORDER BY deployed_at")


def observe_deployment(
    family: str,
    suffix: str,
    run_name: str,
    phase: str,
    message: str,
    replicas: list[dict[str, Any]],
    replaced_bundle_fingerprint: str,
) -> bool:
    """Record what the Serve controller reports for a deployment; False if
    there is no such deployment."""
    return (
        _write(
            "UPDATE deployments SET phase = %s, message = %s, replicas = %s, "
            "replaced_bundle_fingerprint = %s "
            "WHERE family = %s AND suffix = %s AND run_name = %s",
            (
                phase,
                message,
                Jsonb(replicas),
                replaced_bundle_fingerprint,
                family,
                suffix,
                run_name,
            ),
        )
        > 0
    )


def delete_deployment(family: str, suffix: str, run_name: str) -> None:
    _write(
        "DELETE FROM deployments WHERE family = %s AND suffix = %s AND run_name = %s",
        (family, suffix, run_name),
    )
