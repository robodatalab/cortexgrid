-- Schema for the `cortexgrid` database, consumed by jobs_control_plane.db: the
-- records cortexgrid keeps for itself. MLflow only displays metrics; the
-- experiments and runs here map onto its own.
-- Applied via psql against an already-created `cortexgrid` database, like
-- notes_schema.sql.

CREATE TABLE IF NOT EXISTS experiments (
  name                 TEXT PRIMARY KEY,
  mlflow_experiment_id TEXT NOT NULL,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS runs (
  run_id          TEXT PRIMARY KEY,  -- the MLflow run id
  run_name        TEXT NOT NULL UNIQUE,
  experiment_name TEXT NOT NULL REFERENCES experiments (name) ON DELETE CASCADE,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS runs_experiment_idx ON runs (experiment_name);

-- One row per JobLifecycle. Only `history` and the `stop_requested` latch
-- change after the insert.
CREATE TABLE IF NOT EXISTS jobs (
  run_id           TEXT NOT NULL REFERENCES runs (run_id) ON DELETE CASCADE,
  job_id           TEXT NOT NULL,
  experiment_name  TEXT NOT NULL,
  stop_requested   BOOLEAN NOT NULL DEFAULT false,
  retry            BOOLEAN NOT NULL DEFAULT false,
  num_gpus         INTEGER NOT NULL DEFAULT 0,
  num_cpus         INTEGER NOT NULL DEFAULT 1,
  pip_requirements JSONB NOT NULL DEFAULT '[]',
  history          JSONB NOT NULL DEFAULT '[]',
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- True once the control plane has nothing left to do for the job: its last
  -- observed state is terminal and no retry follows. A failed retry=True job
  -- stays open even once stopped: a resubmission already handed to a worker
  -- may still reach Ray, and the poller has to see it to stop it.
  done             BOOLEAN GENERATED ALWAYS AS (COALESCE(
                     history -> -1 ->> 'state' IN ('finished', 'stopped')
                     OR (history -> -1 ->> 'state' = 'failed' AND NOT retry),
                     false)) STORED,
  PRIMARY KEY (run_id, job_id)
);

CREATE INDEX IF NOT EXISTS jobs_open_idx ON jobs (created_at) WHERE NOT done;

CREATE TABLE IF NOT EXISTS job_manifests (
  run_id           TEXT NOT NULL REFERENCES runs (run_id) ON DELETE CASCADE,
  job_id           TEXT NOT NULL,
  code_tarball_uri TEXT NOT NULL,
  PRIMARY KEY (run_id, job_id)
);

CREATE TABLE IF NOT EXISTS job_results (
  run_id TEXT NOT NULL REFERENCES runs (run_id) ON DELETE CASCADE,
  job_id TEXT NOT NULL,
  result BYTEA NOT NULL,  -- cloudpickled JobResult
  PRIMARY KEY (run_id, job_id)
);

CREATE TABLE IF NOT EXISTS checkpoints (
  run_id   TEXT NOT NULL REFERENCES runs (run_id) ON DELETE CASCADE,
  prefix   TEXT NOT NULL,  -- checkpoint/<job_id>
  manifest JSONB NOT NULL,
  PRIMARY KEY (run_id, prefix)
);

-- One row per model registry entry. `tags` carries the entry's metadata
-- (family, suffix, run_name, lifecycle, size_bytes, bundle metadata,
-- requirements, config) under the keys cortexgrid.model_storage and
-- cortexgrid.model_serving define.
CREATE TABLE IF NOT EXISTS models (
  family     TEXT NOT NULL,
  suffix     TEXT NOT NULL,
  run_name   TEXT NOT NULL,
  run_id     TEXT REFERENCES runs (run_id) ON DELETE CASCADE,  -- NULL when imported
  source     TEXT NOT NULL,
  tags       JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (family, suffix, run_name)
);

CREATE INDEX IF NOT EXISTS models_run_idx ON models (run_id);

-- Which imported model each run used (`import_model` / `register_model`).
CREATE TABLE IF NOT EXISTS run_imported_models (
  run_id     TEXT NOT NULL REFERENCES runs (run_id) ON DELETE CASCADE,
  family     TEXT NOT NULL,
  suffix     TEXT NOT NULL,
  created_at TEXT NOT NULL,  -- the model's SavedModel.created_at
  PRIMARY KEY (run_id, family, suffix)
);

-- One row per model `deploy_model` has put on Ray Serve. `spec` is the Serve
-- application it PUT and `tiers` the GPU size classes that spec was built
-- against; `phase`, `message` and `replicas` (where each replica runs) are
-- what the jobs control plane last observed. No foreign key to `models`:
-- deleting a registry entry leaves a running app running.
CREATE TABLE IF NOT EXISTS deployments (
  family      TEXT NOT NULL,
  suffix      TEXT NOT NULL,
  run_name    TEXT NOT NULL,
  spec        JSONB NOT NULL,
  tiers       JSONB NOT NULL,
  url         TEXT NOT NULL,
  phase       TEXT NOT NULL,
  message     TEXT NOT NULL DEFAULT '',
  replicas    JSONB NOT NULL DEFAULT '[]',
  deployed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  replaced_bundle_fingerprint TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (family, suffix, run_name)
);

ALTER TABLE deployments
  ADD COLUMN IF NOT EXISTS replaced_bundle_fingerprint TEXT NOT NULL DEFAULT '';
