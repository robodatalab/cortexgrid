"""Per-key WebSocket stream with reference-counted polling.

Each key gets its own poll task and cache. The first WebSocket subscriber
for a given key starts the task; the last to disconnect cancels it and
clears the cache. Subsequent subscribers receive the latest cached value
immediately, then live updates from the next poll onwards.

Used by run_jobs_stream / run_dashboard_stream / job_stream.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Hashable

from fastapi import WebSocket, WebSocketDisconnect

log = logging.getLogger(__name__)


class KeyedStream:
    def __init__(
        self,
        name: str,
        poll_fn: Callable[[Any], Any],
        poll_interval_sec: float = 10,
    ):
        self.name = name
        self.poll_fn = poll_fn
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
            try:
                await ws.send_json({"type": "state", "data": self._cache[key]})
            except Exception:
                pass
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
                try:
                    data = await asyncio.to_thread(self.poll_fn, key)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("%s poll failed for key=%s", self.name, key)
                    await asyncio.sleep(self.poll_interval_sec)
                    continue
                self._cache[key] = data
                for ws in list(self._clients.get(key, ())):
                    try:
                        await ws.send_json({"type": "state", "data": data})
                    except Exception:
                        clients = self._clients.get(key)
                        if clients is not None:
                            clients.discard(ws)
                await asyncio.sleep(self.poll_interval_sec)
        except asyncio.CancelledError:
            log.info("%s poll loop cancelled for key=%s", self.name, key)
            raise


class KeyedDiffStream:
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
        list_fn: Callable[[Any], list[dict]],
        id_fn: Callable[[dict], str],
        poll_interval_sec: float = 30,
    ):
        self.name = name
        self.list_fn = list_fn
        self.id_fn = id_fn
        self.poll_interval_sec = poll_interval_sec
        self._cache: dict[Hashable, dict[str, dict]] = {}
        self._clients: dict[Hashable, set[WebSocket]] = {}
        self._tasks: dict[Hashable, asyncio.Task] = {}

    async def serve(self, ws: WebSocket, key: Hashable) -> None:
        await ws.accept()
        clients = self._clients.setdefault(key, set())
        clients.add(ws)
        if key not in self._tasks:
            self._tasks[key] = asyncio.create_task(self._poll_loop(key))
        else:
            for item in self._cache.get(key, {}).values():
                try:
                    await ws.send_json({"type": "added", "item": item})
                except Exception:
                    pass
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
                try:
                    items = await asyncio.to_thread(self.list_fn, key)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("%s poll failed for key=%s", self.name, key)
                    await asyncio.sleep(self.poll_interval_sec)
                    continue
                await self._broadcast(key, self._diff(key, items))
                await asyncio.sleep(self.poll_interval_sec)
        except asyncio.CancelledError:
            log.info("%s poll loop cancelled for key=%s", self.name, key)
            raise

    def _diff(self, key: Hashable, items: list[dict]) -> list[dict]:
        new_by_id = {self.id_fn(item): item for item in items}
        old_by_id = self._cache.get(key, {})
        events: list[dict] = []
        for id_, item in new_by_id.items():
            if id_ not in old_by_id:
                events.append({"type": "added", "item": item})
            elif old_by_id[id_]["updated_at"] != item["updated_at"]:
                events.append({"type": "updated", "item": item})
        for id_ in old_by_id:
            if id_ not in new_by_id:
                events.append({"type": "removed", "id": id_})
        self._cache[key] = new_by_id
        return events

    async def _broadcast(self, key: Hashable, events: list[dict]) -> None:
        for ws in list(self._clients.get(key, ())):
            for event in events:
                try:
                    await ws.send_json(event)
                except Exception:
                    clients = self._clients.get(key)
                    if clients is not None:
                        clients.discard(ws)
                    break
