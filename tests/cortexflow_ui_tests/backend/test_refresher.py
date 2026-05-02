"""Tests for Refresher race semantics and serve_websocket ordering.

Verifies the fix for the snapshot-vs-dispatch race: a new subscriber's
initial-state replay must remain consistent with the diff events its
listener subsequently receives, even when a poll fires during the
window between subscribe and the end of replay.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from dataclasses import dataclass
from typing import Any, Callable, MutableMapping

from fastapi import WebSocket
from starlette.websockets import WebSocketState

from cortexflow_ui.backend.utils.keyed_stream import (
    DiffEvent,
    KeyedCache,
    Refresher,
    serve_websocket,
)


@dataclass
class _Item:
    id: str
    value: str


class _PausableFakeWebSocket(WebSocket):
    """In-memory WebSocket whose data sends can be gated for tests."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self._disconnect = asyncio.Event()
        self._connect_consumed = False
        self._send_gate = asyncio.Event()
        self._send_gate.set()
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

    def pause_data_sends(self) -> None:
        self._send_gate.clear()

    def resume_data_sends(self) -> None:
        self._send_gate.set()

    async def _asgi_receive(self) -> MutableMapping[str, Any]:
        if not self._connect_consumed:
            self._connect_consumed = True
            return {"type": "websocket.connect"}
        await self._disconnect.wait()
        return {"type": "websocket.disconnect", "code": 1000}

    async def _asgi_send(self, message: MutableMapping[str, Any]) -> None:
        if message["type"] == "websocket.send" and "text" in message:
            await self._send_gate.wait()
            self.sent.append(json.loads(message["text"]))


async def _wait_for(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("timed out waiting for condition")
        await asyncio.sleep(0.005)


def _apply_events(events: list[dict]) -> dict[str, dict]:
    state: dict[str, dict] = {}
    for e in events:
        if e["type"] in ("added", "updated"):
            state[e["item"]["id"]] = e["item"]
        elif e["type"] == "removed":
            state.pop(e["id"], None)
    return state


class TestSubscribeAndSnapshotAtomicity(unittest.IsolatedAsyncioTestCase):
    async def test_blocks_until_in_flight_dispatch_releases_then_observes_post_poll(
        self,
    ) -> None:
        """While a poll is mid-broadcast, a concurrent subscribe_and_snapshot
        must wait for the dispatch lock and observe the post-poll cache."""
        cache: KeyedCache[str, str, _Item] = KeyedCache()
        cache.set("k", {"a": _Item(id="a", value="old")})

        def poll_fn(_: str) -> dict[str, _Item]:
            return {"a": _Item(id="a", value="new")}

        refresher = Refresher[str, str, _Item](
            name="t", cache=cache, poll_fn=poll_fn, poll_interval_sec=999
        )

        listener_started = asyncio.Event()
        listener_release = asyncio.Event()
        first_received: list[DiffEvent] = []

        async def slow_listener(event: DiffEvent) -> None:
            first_received.append(event)
            listener_started.set()
            await listener_release.wait()

        refresher.subscribe("k", slow_listener)

        poll_task = asyncio.create_task(refresher._poll_and_dispatch("k"))
        await listener_started.wait()

        new_received: list[DiffEvent] = []

        async def new_listener(event: DiffEvent) -> None:
            new_received.append(event)

        sub_task = asyncio.create_task(
            refresher.subscribe_and_snapshot("k", new_listener)
        )
        await asyncio.sleep(0.02)
        self.assertFalse(
            sub_task.done(),
            "subscribe_and_snapshot must block while dispatch lock is held",
        )

        listener_release.set()
        await poll_task
        sub, snapshot = await sub_task

        self.assertEqual(snapshot, {"a": _Item(id="a", value="new")})
        self.assertEqual(
            new_received,
            [],
            "new listener must not receive in-flight events from the poll "
            "that completed before its subscription",
        )

        refresher.unsubscribe(sub)


class TestServeWebsocketRace(unittest.IsolatedAsyncioTestCase):
    async def test_poll_during_initial_replay_converges_to_latest_state(
        self,
    ) -> None:
        """A poll firing while initial-state replay is in progress must not
        leave stale data on the wire. Live events buffer behind the replay
        and drain afterward; the final state on the wire matches the
        upstream after both phases complete."""
        cache: KeyedCache[str, str, _Item] = KeyedCache()
        cache.set(
            "k",
            {
                "a": _Item(id="a", value="1"),
                "b": _Item(id="b", value="2-old"),
            },
        )

        data: dict[str, _Item] = dict(cache.get("k"))

        def poll_fn(_: str) -> dict[str, _Item]:
            return dict(data)

        refresher = Refresher[str, str, _Item](
            name="t", cache=cache, poll_fn=poll_fn, poll_interval_sec=999
        )

        ws = _PausableFakeWebSocket()
        ws.pause_data_sends()
        serve = asyncio.create_task(serve_websocket(refresher, ws, "k"))

        await _wait_for(lambda: ws.accepted)
        await asyncio.sleep(0.02)
        self.assertEqual(ws.sent, [], "data sends should be gated")

        data["b"] = _Item(id="b", value="2-new")
        data["c"] = _Item(id="c", value="3")
        await refresher._poll_and_dispatch("k")

        ws.resume_data_sends()

        await _wait_for(
            lambda: any(
                e["type"] == "added" and e["item"].get("id") == "c"
                for e in ws.sent
            )
        )

        self.assertEqual(
            _apply_events(ws.sent),
            {
                "a": {"id": "a", "value": "1"},
                "b": {"id": "b", "value": "2-new"},
                "c": {"id": "c", "value": "3"},
            },
            "client state must converge to the latest upstream after the "
            "race between initial replay and concurrent poll",
        )

        ws.disconnect()
        await serve

    async def test_no_duplicate_added_when_initial_snapshot_matches_post_poll(
        self,
    ) -> None:
        """If the cache already matches what a concurrent poll would
        produce, the listener's diff events for that poll are empty;
        the client must see exactly one Added per item, not two."""
        cache: KeyedCache[str, str, _Item] = KeyedCache()
        cache.set(
            "k",
            {
                "a": _Item(id="a", value="1"),
                "b": _Item(id="b", value="2"),
            },
        )

        def poll_fn(_: str) -> dict[str, _Item]:
            return {
                "a": _Item(id="a", value="1"),
                "b": _Item(id="b", value="2"),
            }

        refresher = Refresher[str, str, _Item](
            name="t", cache=cache, poll_fn=poll_fn, poll_interval_sec=999
        )

        ws = _PausableFakeWebSocket()
        ws.pause_data_sends()
        serve = asyncio.create_task(serve_websocket(refresher, ws, "k"))

        await _wait_for(lambda: ws.accepted)
        await asyncio.sleep(0.02)

        await refresher._poll_and_dispatch("k")

        ws.resume_data_sends()

        await _wait_for(lambda: len(ws.sent) >= 2)
        await asyncio.sleep(0.05)

        added_ids = [e["item"]["id"] for e in ws.sent if e["type"] == "added"]
        self.assertEqual(
            sorted(added_ids),
            ["a", "b"],
            "each item should appear in exactly one Added event on the wire",
        )

        ws.disconnect()
        await serve


if __name__ == "__main__":
    unittest.main()
