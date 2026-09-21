"""Notes storage for the cortexgrid UI.

Two independent tables in the `notes` Postgres database:
- run_notes: notes attached to a run.
- experiment_notes: meta-notes attached to an experiment, not tied to a
  specific run.

Connection URI is resolved per-call via cortexgrid.secrets.
"""

from __future__ import annotations

from dataclasses import dataclass

from cortexgrid.secrets import get_secret
from cortexgrid_ui.backend.streams.experiments_stream import (
    resolve_run_id,
    resolve_run_name,
)
import psycopg


@dataclass
class RunNote:
    id: str
    run_name: str
    body: str
    created_at: str
    updated_at: str


def _connect() -> psycopg.Connection:
    return psycopg.connect(get_secret("NOTES_DB_URI"))


def list_run_notes(run_name: str) -> list[RunNote]:
    run_id = resolve_run_id(run_name)
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, body, created_at, updated_at FROM run_notes "
            "WHERE run_id = %s ORDER BY created_at",
            (run_id,),
        )
        return [
            RunNote(
                id=str(row[0]),
                run_name=run_name,
                body=row[1],
                created_at=row[2].isoformat(),
                updated_at=row[3].isoformat(),
            )
            for row in cur.fetchall()
        ]


def add_run_note(run_name: str, body: str) -> RunNote | None:
    run_id = resolve_run_id(run_name)
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO run_notes (run_id, body) VALUES (%s, %s) "
            "RETURNING id, body, created_at, updated_at",
            (run_id, body),
        )
        row = cur.fetchone()
        if row is None:
            return None

        return RunNote(
            id=str(row[0]),
            run_name=run_name,
            body=row[1],
            created_at=row[2].isoformat(),
            updated_at=row[3].isoformat(),
        )


def update_run_note(note_id: str, body: str) -> RunNote | None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE run_notes SET body = %s, updated_at = now() "
            "WHERE id = %s "
            "RETURNING id, run_id, body, created_at, updated_at",
            (body, note_id),
        )
        row = cur.fetchone()
        if not row:
            return None

        return RunNote(
            id=str(row[0]),
            run_name=resolve_run_name(str(row[1])),
            body=row[2],
            created_at=row[3].isoformat(),
            updated_at=row[4].isoformat(),
        )


def delete_run_note(note_id: str) -> str | None:
    """Delete a run note. Returns the parent run_name on success, None
    if the note didn't exist. The caller uses run_name to invalidate
    the per-run notes cache."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM run_notes WHERE id = %s RETURNING run_id",
            (note_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return resolve_run_name(str(row[0]))


def delete_run_notes_for_run(run_id: str) -> None:
    """Purge every note attached to this run."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM run_notes WHERE run_id = %s", (run_id,))


@dataclass
class ExperimentNote:
    id: str
    experiment_name: str
    body: str
    created_at: str
    updated_at: str


def list_experiment_notes(experiment_name: str) -> list[ExperimentNote]:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, body, created_at, updated_at FROM experiment_notes "
            "WHERE experiment_name = %s ORDER BY created_at",
            (experiment_name,),
        )
        return [
            ExperimentNote(
                id=str(row[0]),
                experiment_name=experiment_name,
                body=row[1],
                created_at=row[2].isoformat(),
                updated_at=row[3].isoformat(),
            )
            for row in cur.fetchall()
        ]


def add_experiment_note(experiment_name: str, body: str) -> ExperimentNote | None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO experiment_notes (experiment_name, body) VALUES (%s, %s) "
            "RETURNING id, experiment_name, body, created_at, updated_at",
            (experiment_name, body),
        )
        row = cur.fetchone()
        if not row:
            return None

        return ExperimentNote(
            id=str(row[0]),
            experiment_name=str(row[1]),
            body=row[2],
            created_at=row[3].isoformat(),
            updated_at=row[4].isoformat(),
        )


def update_experiment_note(note_id: str, body: str) -> ExperimentNote | None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE experiment_notes SET body = %s, updated_at = now() "
            "WHERE id = %s "
            "RETURNING id, experiment_name, body, created_at, updated_at",
            (body, note_id),
        )
        row = cur.fetchone()
        if not row:
            return None

        return ExperimentNote(
            id=str(row[0]),
            experiment_name=str(row[1]),
            body=row[2],
            created_at=row[3].isoformat(),
            updated_at=row[4].isoformat(),
        )


def delete_experiment_note(note_id: str) -> str | None:
    """Delete an experiment note. Returns the parent experiment_name
    on success, None if the note didn't exist. The caller uses it to
    invalidate the per-experiment notes cache."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM experiment_notes WHERE id = %s "
            "RETURNING experiment_name",
            (note_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return str(row[0])


def delete_experiment_notes_for_experiment(experiment_name: str) -> None:
    """Purge every meta-note attached to this experiment."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM experiment_notes WHERE experiment_name = %s",
            (experiment_name,),
        )
