"""Notes storage for the cortexflow UI.

Two independent tables in the `notes` Postgres database:
- run_notes: notes attached to an MLflow run.
- experiment_notes: meta-notes attached to an MLflow experiment, not tied
  to a specific run.

Connection URI is resolved per-call via cortexflow.secrets.
"""

from __future__ import annotations

from typing import Any

import psycopg

from cortexflow.secrets import get_secret


def _connect() -> psycopg.Connection:
    return psycopg.connect(get_secret("NOTES_DB_URI"))


def _row_to_note(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "id": str(row[0]),
        "body": row[1],
        "created_at": row[2].isoformat(),
        "updated_at": row[3].isoformat(),
    }


def list_run_notes(run_id: str) -> list[dict[str, Any]]:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, body, created_at, updated_at FROM run_notes "
            "WHERE run_id = %s ORDER BY created_at",
            (run_id,),
        )
        return [_row_to_note(row) for row in cur.fetchall()]


def add_run_note(run_id: str, body: str) -> dict[str, Any]:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO run_notes (run_id, body) VALUES (%s, %s) "
            "RETURNING id, body, created_at, updated_at",
            (run_id, body),
        )
        return _row_to_note(cur.fetchone())


def update_run_note(note_id: str, body: str) -> dict[str, Any] | None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE run_notes SET body = %s, updated_at = now() "
            "WHERE id = %s "
            "RETURNING id, body, created_at, updated_at",
            (body, note_id),
        )
        row = cur.fetchone()
        return _row_to_note(row) if row else None


def delete_run_note(note_id: str) -> bool:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM run_notes WHERE id = %s", (note_id,))
        return cur.rowcount > 0


def list_experiment_notes(experiment_name: str) -> list[dict[str, Any]]:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, body, created_at, updated_at FROM experiment_notes "
            "WHERE experiment_name = %s ORDER BY created_at",
            (experiment_name,),
        )
        return [_row_to_note(row) for row in cur.fetchall()]


def add_experiment_note(experiment_name: str, body: str) -> dict[str, Any]:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO experiment_notes (experiment_name, body) VALUES (%s, %s) "
            "RETURNING id, body, created_at, updated_at",
            (experiment_name, body),
        )
        return _row_to_note(cur.fetchone())


def update_experiment_note(note_id: str, body: str) -> dict[str, Any] | None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE experiment_notes SET body = %s, updated_at = now() "
            "WHERE id = %s "
            "RETURNING id, body, created_at, updated_at",
            (body, note_id),
        )
        row = cur.fetchone()
        return _row_to_note(row) if row else None


def delete_experiment_note(note_id: str) -> bool:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM experiment_notes WHERE id = %s", (note_id,))
        return cur.rowcount > 0
