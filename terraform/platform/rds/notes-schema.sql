-- Schema for the `notes` database, consumed by cortexflow_ui.backend.models.notes.
-- Applied by terraform via psql against an already-created `notes` database.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS run_notes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id TEXT NOT NULL,
  body TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS run_notes_run_idx
  ON run_notes (run_id, created_at);

CREATE TABLE IF NOT EXISTS experiment_notes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  experiment_name TEXT NOT NULL,
  body TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS experiment_notes_exp_idx
  ON experiment_notes (experiment_name, created_at);
