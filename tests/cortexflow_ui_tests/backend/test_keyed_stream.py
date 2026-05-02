from __future__ import annotations

import asyncio
import json
import unittest
from typing import Any, Callable, MutableMapping
from unittest.mock import patch

from fastapi import WebSocket
from starlette.websockets import WebSocketState

from cortexflow.jobs import JobLifecycle
from cortexflow.ray_util import JobStatus
from cortexflow_ui.backend.streams import run_jobs_stream, run_notes_stream


class FakeWebSocket(WebSocket):
    """In-memory WebSocket that drives the real Starlette state machine.

    Intercepts at the ASGI layer (receive/send callables) so accept(),
    send_json(), and receive_text() flow through the parent class
    unchanged. The first ASGI receive yields websocket.connect (consumed
    by accept); subsequent receives block until disconnect() is called,
    then yield websocket.disconnect (which makes receive_text raise
    WebSocketDisconnect).
    """

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self._disconnect = asyncio.Event()
        self._connect_consumed = False
        super().__init__(
            scope={"type": "websocket", "path": "/", "headers": []},
            receive=self._asgi_receive,
            send=self._asgi_send,
        )

    @property
    def accepted(self) -> bool:
        return self.application_state == WebSocketState.CONNECTED

    def disconnect(self) -> None:
        self._disconnect.set()

    async def _asgi_receive(self) -> MutableMapping[str, Any]:
        if not self._connect_consumed:
            self._connect_consumed = True
            return {"type": "websocket.connect"}
        await self._disconnect.wait()
        return {"type": "websocket.disconnect", "code": 1000}

    async def _asgi_send(self, message: MutableMapping[str, Any]) -> None:
        if message["type"] == "websocket.send" and "text" in message:
            self.sent.append(json.loads(message["text"]))


async def _wait_for(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("timed out waiting for condition")
        await asyncio.sleep(0.01)


class TestKeyedStreamServe(unittest.IsolatedAsyncioTestCase):
    """Drives run_jobs_stream.stream (KeyedStream) through serve()."""

    async def asyncSetUp(self) -> None:
        self._patches = [
            patch(
                "cortexflow_ui.backend.streams.run_jobs_stream.list_ray_jobs_with_submission_id",
                return_value=[],
            ),
            patch(
                "cortexflow_ui.backend.streams.run_jobs_stream.get_ray_job_status",
                return_value=JobStatus.RUNNING,
            ),
            patch(
                "cortexflow_ui.backend.streams.run_jobs_stream.list_experiment_run_jobs",
                return_value=[
                    JobLifecycle(experiment_name="alpha", run_id="run-x", job_id="j1"),
                ],
            ),
        ]
        for p in self._patches:
            p.start()
        for p in self._patches:
            self.addCleanup(p.stop)

    async def test_first_subscriber_receives_added_event_per_job(self) -> None:
        ws = FakeWebSocket()
        task = asyncio.create_task(run_jobs_stream.stream.serve(ws, "run-1"))
        try:
            await _wait_for(lambda: bool(ws.sent))
            self.assertTrue(ws.accepted)
            self.assertEqual(ws.sent[0]["type"], "added")
            self.assertEqual(
                ws.sent[0]["item"],
                {"job_id": "j1", "status": "running", "retry": False},
            )
        finally:
            ws.disconnect()
            await task

    async def test_disconnect_clears_task_and_clients_but_preserves_cache(self) -> None:
        ws = FakeWebSocket()
        task = asyncio.create_task(run_jobs_stream.stream.serve(ws, "run-2"))
        await _wait_for(lambda: bool(ws.sent))
        ws.disconnect()
        await task

        self.assertNotIn("run-2", run_jobs_stream.stream._tasks)
        self.assertNotIn("run-2", run_jobs_stream.stream._clients)
        self.assertEqual(
            run_jobs_stream.stream.cache.get("run-2"),
            {"j1": run_jobs_stream.Job(job_id="j1", status="running", retry=False)},
        )

    async def test_second_subscriber_receives_cached_added_immediately(self) -> None:
        ws_a = FakeWebSocket()
        task_a = asyncio.create_task(run_jobs_stream.stream.serve(ws_a, "run-3"))
        try:
            await _wait_for(lambda: bool(ws_a.sent))

            ws_b = FakeWebSocket()
            task_b = asyncio.create_task(run_jobs_stream.stream.serve(ws_b, "run-3"))
            try:
                await _wait_for(lambda: bool(ws_b.sent))
                self.assertEqual(ws_b.sent[0]["type"], "added")
                self.assertEqual(
                    ws_b.sent[0]["item"],
                    {"job_id": "j1", "status": "running", "retry": False},
                )
            finally:
                ws_b.disconnect()
                await task_b
        finally:
            ws_a.disconnect()
            await task_a


class _FakeIsoDt:
    def __init__(self, s: str) -> None:
        self._s = s

    def isoformat(self) -> str:
        return self._s


class _FakePgCursor:
    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows

    def execute(self, *args, **kwargs) -> None:
        pass

    def fetchall(self) -> list[tuple]:
        return self._rows

    def __enter__(self) -> "_FakePgCursor":
        return self

    def __exit__(self, *args) -> None:
        del args


class _FakePgConn:
    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows

    def cursor(self) -> _FakePgCursor:
        return _FakePgCursor(self._rows)

    def __enter__(self) -> "_FakePgConn":
        return self

    def __exit__(self, *args) -> None:
        del args


class TestKeyedDiffStreamServe(unittest.IsolatedAsyncioTestCase):
    """Drives run_notes_stream.stream (KeyedDiffStream) through serve().

    Mocks the psycopg layer under list_run_notes (the actual list_fn) so
    the real production function is exercised end-to-end.
    """

    async def asyncSetUp(self) -> None:
        self.notes: list[dict] = []

        def fake_connect() -> _FakePgConn:
            rows = [
                (
                    n["id"],
                    n["body"],
                    _FakeIsoDt(n["created_at"]),
                    _FakeIsoDt(n["updated_at"]),
                )
                for n in self.notes
            ]
            return _FakePgConn(rows)

        patcher = patch(
            "cortexflow_ui.backend.models.notes._connect",
            side_effect=fake_connect,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        resolve_patcher = patch(
            "cortexflow_ui.backend.models.notes.resolve_run_id",
            side_effect=lambda run_name: run_name,
        )
        resolve_patcher.start()
        self.addCleanup(resolve_patcher.stop)

    @staticmethod
    def _has(events: list[dict], type_: str, **fields) -> bool:
        return any(
            e.get("type") == type_ and all(e.get(k) == v for k, v in fields.items())
            for e in events
        )

    async def test_added_event_for_existing_notes(self) -> None:
        self.notes.append(
            {"id": "n1", "body": "hi", "created_at": "t0", "updated_at": "t0"}
        )
        ws = FakeWebSocket()
        task = asyncio.create_task(run_notes_stream.stream.serve(ws, "run-1"))
        try:
            await _wait_for(
                lambda: any(
                    e["type"] == "added" and e["item"]["id"] == "n1" for e in ws.sent
                )
            )
        finally:
            ws.disconnect()
            await task

    async def test_added_event_when_note_appears(self) -> None:
        ws = FakeWebSocket()
        task = asyncio.create_task(run_notes_stream.stream.serve(ws, "run-2"))
        try:
            await asyncio.sleep(0.05)
            self.assertEqual(ws.sent, [])

            self.notes.append(
                {"id": "n2", "body": "hi", "created_at": "t0", "updated_at": "t0"}
            )
            await _wait_for(
                lambda: any(
                    e["type"] == "added" and e["item"]["id"] == "n2" for e in ws.sent
                )
            )
        finally:
            ws.disconnect()
            await task

    async def test_updated_event_when_updated_at_changes(self) -> None:
        self.notes.append(
            {"id": "n3", "body": "hi", "created_at": "t0", "updated_at": "t0"}
        )
        ws = FakeWebSocket()
        task = asyncio.create_task(run_notes_stream.stream.serve(ws, "run-3"))
        try:
            await _wait_for(
                lambda: any(
                    e["type"] == "added" and e["item"]["id"] == "n3" for e in ws.sent
                )
            )
            self.notes[0] = {**self.notes[0], "updated_at": "t1"}
            await _wait_for(
                lambda: any(
                    e["type"] == "updated" and e["item"]["id"] == "n3" for e in ws.sent
                )
            )
        finally:
            ws.disconnect()
            await task

    async def test_removed_event_when_note_disappears(self) -> None:
        self.notes.append(
            {"id": "n4", "body": "hi", "created_at": "t0", "updated_at": "t0"}
        )
        ws = FakeWebSocket()
        task = asyncio.create_task(run_notes_stream.stream.serve(ws, "run-4"))
        try:
            await _wait_for(
                lambda: any(
                    e["type"] == "added" and e["item"]["id"] == "n4" for e in ws.sent
                )
            )
            self.notes.clear()
            await _wait_for(
                lambda: any(
                    e["type"] == "removed" and e.get("id") == "n4" for e in ws.sent
                )
            )
        finally:
            ws.disconnect()
            await task

    async def test_second_subscriber_gets_added_per_cached_item(self) -> None:
        self.notes.append(
            {"id": "n5", "body": "hi", "created_at": "t0", "updated_at": "t0"}
        )
        ws_a = FakeWebSocket()
        task_a = asyncio.create_task(run_notes_stream.stream.serve(ws_a, "run-5"))
        try:
            await _wait_for(
                lambda: any(
                    e["type"] == "added" and e["item"]["id"] == "n5" for e in ws_a.sent
                )
            )

            ws_b = FakeWebSocket()
            task_b = asyncio.create_task(run_notes_stream.stream.serve(ws_b, "run-5"))
            try:
                await _wait_for(
                    lambda: any(
                        e["type"] == "added" and e["item"]["id"] == "n5"
                        for e in ws_b.sent
                    )
                )
            finally:
                ws_b.disconnect()
                await task_b
        finally:
            ws_a.disconnect()
            await task_a


if __name__ == "__main__":
    unittest.main()
