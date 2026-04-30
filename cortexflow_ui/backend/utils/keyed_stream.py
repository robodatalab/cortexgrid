"""Per-key WebSocket stream with reference-counted polling.

Each key gets its own poll task and cache. The first WebSocket subscriber
for a given key starts the task; the last to disconnect cancels it and
clears the cache. Subsequent subscribers receive the latest cached value
immediately, then live updates from the next poll onwards.

Used by run_jobs_stream / run_dashboard_stream / job_stream.
"""

from __future__ import annotations

import abc
import asyncio
import logging
from dataclasses import asdict, dataclass
from typing import Any, Callable, Hashable

from fastapi import WebSocket, WebSocketDisconnect

log = logging.getLogger(__name__)


@dataclass
class StateEvent:
    data: Any
    type: str = "state"


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


class KeyedStreamBase(abc.ABC):
    def __init__(
        self,
        name: str,
        poll_interval_sec: float,
    ):
        self.name = name
        self.poll_interval_sec = poll_interval_sec
        self._cache: dict[Hashable, Any] = {}
        self._clients: dict[Hashable, set[WebSocket]] = {}
        self._tasks: dict[Hashable, asyncio.Task] = {}

    async def serve(self, ws: WebSocket, key: Hashable) -> None:
        await ws.accept()
        clients = self._clients.setdefault(key, set())
        clients.add(ws)

        if key not in self._tasks:
            self._tasks[key] = asyncio.create_task(self._poll_loop(key))
        elif key in self._cache:
            await self._send_initial_state(ws, key)

        await self._wait_until_socket_disconnected(ws, key)

    async def _wait_until_socket_disconnected(
        self, ws: WebSocket, key: Hashable
    ) -> None:
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            await self._unsubscribe(ws, key)

    async def _unsubscribe(self, ws: WebSocket, key: Hashable) -> None:
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

    async def _poll_loop(self, key: Hashable) -> None:
        log.info("%s poll loop started for key=%s", self.name, key)
        try:
            while True:
                await self._poll_and_dispatch(key)
        except asyncio.CancelledError:
            log.info("%s poll loop cancelled for key=%s", self.name, key)
            raise

    async def _broadcast(
        self, key: Hashable, event: StateEvent | DiffEvent
    ) -> None:
        payload = asdict(event)
        for ws in list(self._clients.get(key, ())):
            try:
                await ws.send_json(payload)
            except Exception:
                clients = self._clients.get(key)
                if clients is not None:
                    clients.discard(ws)

    @abc.abstractmethod
    async def _send_initial_state(self, ws: WebSocket, key: Hashable) -> None:
        pass

    @abc.abstractmethod
    async def _poll_and_dispatch(self, key: Hashable) -> None:
        pass


class KeyedStream(KeyedStreamBase):
    def __init__(
        self,
        name: str,
        poll_fn: Callable[[Any], Any],
        poll_interval_sec: float = 10,
    ):
        super().__init__(
            name=name,
            poll_interval_sec=poll_interval_sec,
        )

        self.poll_fn = poll_fn

    async def _send_initial_state(self, ws: WebSocket, key: Hashable) -> None:
        try:
            await ws.send_json(asdict(StateEvent(data=self._cache[key])))
        except Exception:
            pass

    async def _poll_and_dispatch(self, key: Hashable) -> None:
        try:
            data = await asyncio.to_thread(self.poll_fn, key)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("%s poll failed for key=%s", self.name, key)
            await asyncio.sleep(self.poll_interval_sec)
            return

        self._cache[key] = data
        await self._broadcast(key, StateEvent(data=data))


class KeyedDiffStream(KeyedStreamBase):
    """Per-key WebSocket stream that emits per-item diff events.

    Same reference-counted poll pattern as KeyedStream, but instead of
    pushing a full snapshot per poll, computes a diff against the previous
    list and emits one of {type: added, item}, {type: updated, item},
    {type: removed, id}. New subscribers receive an `added` event per
    cached item, then live diffs from the next poll onwards.
    """

    def __init__(
        self,
        name: str,
        list_fn: Callable[[Any], list[Any]],
        id_fn: Callable[[Any], str],
        version_fn: Callable[[Any], Hashable] = lambda i: i.updated_at,
        poll_interval_sec: float = 30,
    ):
        super().__init__(
            name=name,
            poll_interval_sec=poll_interval_sec,
        )

        def poll(key: Hashable) -> dict:
            items = list_fn(key)
            return {id_fn(item): item for item in items}

        self._poll = poll
        self._version_fn = version_fn

    async def _send_initial_state(self, ws: WebSocket, key: Hashable) -> None:
        for item in self._cache.get(key, {}).values():
            try:
                await ws.send_json(asdict(AddedEvent(item=item)))
            except Exception:
                pass

    async def _poll_and_dispatch(self, key: Hashable) -> None:
        try:
            data = await asyncio.to_thread(self._poll, key)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("%s poll failed for key=%s", self.name, key)
            await asyncio.sleep(self.poll_interval_sec)
            return

        old_data = self._cache.get(key, {})
        self._cache[key] = data
        events = _diff_dict(old_data, data, self._version_fn)

        for event in events:
            await self._broadcast(key, event)


def _diff_dict(
    old_by_id: dict,
    new_by_id: dict,
    version_fn: Callable[[Any], Hashable],
) -> list[DiffEvent]:
    events: list[DiffEvent] = []
    for id_, item in new_by_id.items():
        if id_ not in old_by_id:
            events.append(AddedEvent(item=item))
        elif version_fn(old_by_id[id_]) != version_fn(item):
            events.append(UpdatedEvent(item=item))
    for id_ in old_by_id:
        if id_ not in new_by_id:
            events.append(RemovedEvent(id=id_))
    return events
