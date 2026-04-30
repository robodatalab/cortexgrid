"""Per-key WebSocket stream with reference-counted polling.

Each key gets its own poll task and cache. Per poll, computes a diff
against the previous snapshot and emits one of
{type: added, item}, {type: updated, item}, {type: removed, id}.
The first WebSocket subscriber for a key starts the task; the last to
disconnect cancels it and clears the cache. New subscribers receive an
`added` event per cached item, then live diffs from the next poll on.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass
from typing import Any, Callable, Generic, Hashable, TypeVar

from fastapi import WebSocket, WebSocketDisconnect

log = logging.getLogger(__name__)


TopicKey = TypeVar("TopicKey", bound=Hashable)
ItemId = TypeVar("ItemId", bound=Hashable)
Payload = TypeVar("Payload")


@dataclass
class AddedEvent:
    item: Any
    type: str = "added"


@dataclass
class UpdatedEvent:
    item: Any
    type: str = "updated"


@dataclass
class RemovedEvent:
    id: str
    type: str = "removed"


DiffEvent = AddedEvent | UpdatedEvent | RemovedEvent


class KeyedStream(Generic[TopicKey, ItemId, Payload]):
    def __init__(
        self,
        name: str,
        poll_fn: Callable[[TopicKey], dict[ItemId, Payload]],
        poll_interval_sec: float = 30,
    ):
        self.name = name
        self.poll_fn = poll_fn
        self.poll_interval_sec = poll_interval_sec
        self._cache: dict[TopicKey, dict[ItemId, Payload]] = {}
        self._clients: dict[TopicKey, set[WebSocket]] = {}
        self._tasks: dict[TopicKey, asyncio.Task] = {}

    async def serve(self, ws: WebSocket, key: TopicKey) -> None:
        await ws.accept()
        clients = self._clients.setdefault(key, set())
        clients.add(ws)

        if key not in self._tasks:
            self._tasks[key] = asyncio.create_task(self._poll_loop(key))
        elif key in self._cache:
            await self._send_initial_state(ws, key)

        await self._wait_until_socket_disconnected(ws, key)

    async def _wait_until_socket_disconnected(
        self, ws: WebSocket, key: TopicKey
    ) -> None:
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            await self._unsubscribe(ws, key)

    async def _unsubscribe(self, ws: WebSocket, key: TopicKey) -> None:
        clients = self._clients.get(key)
        if clients is None:
            return
        clients.discard(ws)
        if clients:
            return
        task = self._tasks.pop(key, None)
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._cache.pop(key, None)
        self._clients.pop(key, None)

    async def _poll_loop(self, key: TopicKey) -> None:
        log.info("%s poll loop started for key=%s", self.name, key)
        try:
            while True:
                await self._poll_and_dispatch(key)
        except asyncio.CancelledError:
            log.info("%s poll loop cancelled for key=%s", self.name, key)
            raise

    async def _send_initial_state(self, ws: WebSocket, key: TopicKey) -> None:
        for item in self._cache.get(key, {}).values():
            try:
                await ws.send_json(asdict(AddedEvent(item=item)))
            except Exception:
                pass

    async def _poll_and_dispatch(self, key: TopicKey) -> None:
        try:
            data = await asyncio.to_thread(self.poll_fn, key)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("%s poll failed for key=%s", self.name, key)
            await asyncio.sleep(self.poll_interval_sec)
            return

        old_data = self._cache.get(key, {})
        self._cache[key] = data
        events = _diff_dict(old_data, data)

        for event in events:
            await self._broadcast(key, event)

    async def _broadcast(self, key: TopicKey, event: DiffEvent) -> None:
        payload = asdict(event)
        for ws in list(self._clients.get(key, ())):
            try:
                await ws.send_json(payload)
            except Exception:
                clients = self._clients.get(key)
                if clients is not None:
                    clients.discard(ws)


def _diff_dict(old_by_id: dict, new_by_id: dict) -> list[DiffEvent]:
    events: list[DiffEvent] = []
    for id_, item in new_by_id.items():
        if id_ not in old_by_id:
            events.append(AddedEvent(item=item))
        elif old_by_id[id_] != item:
            events.append(UpdatedEvent(item=item))
    for id_ in old_by_id:
        if id_ not in new_by_id:
            events.append(RemovedEvent(id=id_))
    return events
