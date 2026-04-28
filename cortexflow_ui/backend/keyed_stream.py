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
