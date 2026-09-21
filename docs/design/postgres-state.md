# Moving cortexgrid's own state from MLflow to Postgres (#476)

## What changes, what does not

No public API of `cortexgrid` or of the jobs control plane changes: every function, class, method and signature stays, including `JobLifecycle.save_to_mlflow` / `load_from_mlflow`, whose names are kept although they no longer touch MLflow.

MLflow only displays metrics: it keeps its experiments, runs, metrics, params and user artifacts (`log_metric`, `log_params`, `log_artifact`, the run dashboard, the MLflow link in the cortexgrid dashboard).

Everything cortexgrid is responsible for lives in a dedicated `cortexgrid` Postgres database, owned by the jobs control plane and read and written through a thin HTTP API on it:

| Record | Before | After (`cortexgrid` DB) |
|---|---|---|
| Experiment | the MLflow experiment itself | `experiments` row, mapped to `mlflow_experiment_id` |
| Run | the MLflow run itself | `runs` row, keyed by the MLflow run id |
| Job lifecycle | artifact `job/<id>/lifecycle.json` | `jobs` row |
| Job payload manifest | artifact `job/<id>/manifest.json` | `job_manifests` row |
| Job result | artifact `job/<id>/result.pkl` | `job_results` row |
| Checkpoint manifest | artifact `checkpoint/<job>/manifest.json` | `checkpoints` row |
| Model registry entry | MLflow `ModelVersion` + tags | `models` row (tags kept as a JSON object) |
| Run → imported model link | MLflow run tag `imported_model/<family>/<suffix>` | `run_imported_models` row |
| Deployment | not recorded (Ray Serve's app list) | `deployments` row: the spec PUT, its URL, and the phase / message / replica placements last observed |

Blobs stay where they are (S3/MinIO): code tarballs, checkpoint attributes, weights, serve bundles.

## Architecture

```
laptop / Ray driver / serve replica / UI backend
        │  cortexgrid (public API unchanged)
        │  cortexgrid/state.py — one keep-alive HTTP session
        ▼
jobs-control-plane pod ── one process ──────────────────────┐
  uvicorn: jobs_control_plane/api.py  (state API, :8000)    │
  thread:  poll loop (server.py) ── uses cortexgrid ──► API │
  jobs_control_plane/db.py — psycopg connection pool        │
        ▼                                                   │
Postgres (on-prem pod / RDS): database `cortexgrid` ◄───────┘
```

- postgres colocated with control plane host (for speed of access)
- Only the control plane holds `CORTEXGRID_DB_URI`. Everyone else reaches the records through `JOBS_CONTROL_PLANE_URI` (NodePort 30700 on the head's tailscale IP, same posture as MLflow's 30500).
- The poll loop keeps calling the `cortexgrid` library; its calls land on the API in the same process. It reads only the open jobs (`jobs.done` is a generated column), not every job of every run, and at the end of each cycle refreshes the deployment records from one read of the Ray Serve controller (`observe_deployments`).
- A no-op `import_model` / `register_model` / `model_registry_status` / `deploy_model` is a few keep-alive HTTP round trips to the control plane, each one indexed lookup: no MLflow, no Ray, and no secrets-server round trip per call (the control-plane URI is cached per process).

## Where it lives

| Piece | File |
|---|---|
| Schema | [k8s/charts/cortexgrid/files/postgres/cortexgrid_schema.sql](../../k8s/charts/cortexgrid/files/postgres/cortexgrid_schema.sql) |
| SQL | [jobs_control_plane/db.py](../../jobs_control_plane/db.py) |
| State API | [jobs_control_plane/api.py](../../jobs_control_plane/api.py) |
| Poll loop + server | [jobs_control_plane/server.py](../../jobs_control_plane/server.py) |
| Client | [cortexgrid/state.py](../../cortexgrid/state.py) |
| Records in the library | `cortexgrid/{experiment,jobs,checkpoint,model_storage,model_serving,__init__}.py` |
| UI backend | `cortexgrid_ui/backend/streams/{experiments_stream,job_details_stream}.py` |
| Database bootstrap | on-prem: `templates/postgres/notes_init.yaml`; AWS: `terraform/platform/rds/cortexgrid.tf` |
| Secrets | `CORTEXGRID_DB_URI` (`PostgresCredentials`, `TerraformOutputs`), `JOBS_CONTROL_PLANE_URI` (`ControlPlaneDetails`) |
| Chart | `templates/jobs_control_plane/service.yaml` (NodePort 30700), port + readiness probe on the deployment |
| Image | `control-plane` dependency group (fastapi, uvicorn, psycopg + pool) |
| Test fake | `FakeState` in [tests/fakes.py](../../tests/fakes.py): the API's semantics in memory, installed over `cortexgrid.state` |

## State API

Internal to the repo (`cortexgrid/state.py` is its only client). JSON bodies unless noted; a missing record is a 404, and so is a write under a run cortexgrid has no record of.

| Method | Path | Replaces |
|---|---|---|
| PUT / GET / DELETE | `/experiments/{name}` | MLflow experiment lookups (PUT inserts if absent and returns the stored record) |
| GET | `/experiments` | `search_experiments` |
| PUT / GET / DELETE | `/runs/{run_id}` | `get_run` (DELETE cascades to everything under the run) |
| GET | `/runs?experiment_name=&run_name=` | `search_runs` |
| POST | `/runs/{run_id}/stop` | load + save of every lifecycle in the run |
| PUT | `/runs/{run_id}/imported-models/{family}/{suffix}` | `set_tag(run_id, "imported_model/...")` |
| PUT / GET | `/runs/{run_id}/jobs/{job_id}` | `lifecycle.json` |
| GET | `/runs/{run_id}/jobs` | `list_artifacts("job")` + one download per job |
| GET | `/jobs/open` | every lifecycle of every run |
| PUT / GET | `/runs/{run_id}/jobs/{job_id}/manifest` | `manifest.json` |
| PUT / GET (octet-stream) | `/runs/{run_id}/jobs/{job_id}/result` | `result.pkl` |
| PUT / GET | `/runs/{run_id}/checkpoints/{prefix:path}` | checkpoint `manifest.json` |
| PUT / GET / DELETE | `/models/{family}/{suffix}/{run_name}` | `create_model_version` / `search_model_versions` / `delete_model_version` |
| PATCH | `/models/{family}/{suffix}/{run_name}/tags` | `set_model_version_tag` (several at once) |
| GET / DELETE | `/models?run_id=` | `search_model_versions` |
| PUT / GET / PATCH / DELETE | `/deployments/{family}/{suffix}/{run_name}` | — (PATCH records an observation) |
| GET | `/deployments` | `GET /api/serve/applications/` |
| GET | `/health` | — |

## Deployments

- `deploy_model` checks the model's deployment record first. When the record shows the app live (running, deploying, not started or unhealthy) and the spec rebuilt against the GPU tiers stored with the record equals the stored spec, it returns from the record without calling Ray. Otherwise it takes the old path (fresh GPU tiers, clear a failed app, PUT unless Ray already has the spec) and then writes the record.
- `undeploy_model` PUTs the remaining apps and deletes the record.
- `list_deployed_models`, `model_serving_status` and `model_replica_placements` read the records. They are at most one poll cycle old.
- `wait_for_model_serving` and `model_serving_messages` still ask Ray live.
- `observe_deployments` runs on every poll cycle. It reads the Serve controller once and patches each record whose phase, message or placements changed. A vanished app reads as `not_deployed`, which the listing leaves out.

## Experiments and their MLflow counterparts

- `Experiment.init(name)` looks up cortexgrid's experiment record. Only when there is none does it create an MLflow experiment, under the plain name if MLflow allows it and otherwise under `<name>__<8 hex>`, because MLflow never frees a deleted experiment's name. It then records the mapping, with insert-if-absent, so a concurrent init of the same name reuses the winner's MLflow experiment.
- `delete_experiment` soft-deletes the MLflow experiment without renaming it and drops cortexgrid's record. The restore / rename / delete dance is gone.

## Starting fresh (no migration)

Existing MLflow experiments, runs and model versions are orphaned by the switch, so they are wiped before the new control plane starts:

1. Undeploy every Serve app (`put_serve_applications([])`).
2. `DROP DATABASE mlflow WITH (FORCE); CREATE DATABASE mlflow;` and restart the mlflow pod (it recreates its schema).
3. `TRUNCATE run_notes, experiment_notes;` in `notes` (they point at runs that no longer exist).
4. Delete the S3 prefixes `job/`, `checkpoint/`, `models/`, `serve-bundles/`, `mlflow-artifacts/`.
5. Re-run head setup so the seed publishes `CORTEXGRID_DB_URI` and `JOBS_CONTROL_PLANE_URI` (AWS: `terraform apply` first, which creates the `cortexgrid` database). Then sync the chart; on-prem, the init Job creates `cortexgrid`.

## Behaviour that differs

- Saving the same `(family, suffix)` twice in one run replaces the entry instead of adding a second MLflow version. The weights already shared one S3 path.
- The control plane stops re-reading jobs it is done with. After a Ray head restart, finished jobs therefore no longer get a spurious `pending` event appended.
- A `stop_experiment_run_jobs` racing with a poll cycle can no longer be undone by the poller writing back its older copy of the lifecycle.
- Serving status, listings and placements are read from the deployment records, so they can be up to one poll cycle (5 s) stale.
- A redeploy that changes nothing no longer re-reads the GPU tiers. A tier added to or removed from the cluster reaches the spec on the next deploy that changes something else.
- Serve apps not created by `deploy_model` are no longer listed.
