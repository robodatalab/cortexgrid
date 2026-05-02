"""Topic-keyed cache with background polling and per-listener pub/sub.

Refresher owns a cache and a single always-on poll loop. Each topic in
the loop's sweep has at least one subscribed listener; listeners are
notified of per-item diff events (added/updated/removed) computed
against the previous cache snapshot. When a topic's last listener
unsubscribes, the topic is dropped from the sweep and its cache is
cleared. `pin(topic)` keeps a topic in the sweep regardless of
listener count, for caches that need to stay warm for non-WebSocket
readers.

`serve_websocket` is a thin transport adapter: it subscribes a
listener that buffers diff events into a queue while the initial
cache snapshot is replayed on connect, then drains the queue and
forwards live events until disconnect.

Concurrency model: poll dispatch (`_poll_and_dispatch`) and new
subscriptions (`subscribe_and_snapshot`) serialize on a single
`asyncio.Lock`. A poll's cache update + listener broadcast happens
atomically; a new subscriber's snapshot read + listener insertion
happens atomically. Together this guarantees a new subscriber sees a
cache snapshot consistent with the set of dispatch events its
listener will receive: never half a poll's effects.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass
from typing import Any, Awaitable, Callable, Generic, Hashable, TypeVar

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

Listener = Callable[[DiffEvent], Awaitable[None]]


class KeyedCache(Generic[TopicKey, ItemId, Payload]):
    """Shared keyed store. Dumb storage; no diffing, no events."""

    def __init__(self) -> None:
        self._data: dict[TopicKey, dict[ItemId, Payload]] = {}

    def get(self, topic: TopicKey) -> dict[ItemId, Payload]:
        return self._data.get(topic, {})

    def set(self, topic: TopicKey, items: dict[ItemId, Payload]) -> None:
        self._data[topic] = items

    def clear(self, topic: TopicKey) -> None:
        self._data.pop(topic, None)


@dataclass
class Subscription(Generic[TopicKey]):
    topic: TopicKey
    listener: Listener


class Refresher(Generic[TopicKey, ItemId, Payload]):
    """Background poller with per-topic listener pub/sub.

    The single poll loop sweeps every topic that has at least one
    listener (or has been pinned), polling the data source and
    broadcasting per-item diffs against the cache.
    """

    def __init__(
        self,
        name: str,
        cache: KeyedCache[TopicKey, ItemId, Payload],
        poll_fn: Callable[[TopicKey], dict[ItemId, Payload]],
        poll_interval_sec: float = 30,
    ) -> None:
        self.name = name
        self.cache = cache
        self.poll_fn = poll_fn
        self.poll_interval_sec = poll_interval_sec
        self._listeners: dict[TopicKey, list[Listener]] = {}
        self._dispatch_lock = asyncio.Lock()
        self._task: asyncio.Task | None = None

    def ensure_started(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    def pin(self, topic: TopicKey) -> None:
        """Keep `topic` in the sweep regardless of listener count.

        Implemented as a permanent no-op listener so the same
        listener-count refcount governs both pinned and subscribed
        topics; no separate pin set.
        """

        async def _noop(_event: DiffEvent) -> None:
            return None

        self._listeners.setdefault(topic, []).append(_noop)

    def subscribe(
        self, topic: TopicKey, listener: Listener
    ) -> Subscription[TopicKey]:
        self._listeners.setdefault(topic, []).append(listener)
        return Subscription(topic=topic, listener=listener)

    async def subscribe_and_snapshot(
        self, topic: TopicKey, listener: Listener
    ) -> tuple[Subscription[TopicKey], dict[ItemId, Payload]]:
        """Atomically subscribe and capture a cache snapshot.

        Held under the dispatch lock so that no diff event for `topic`
        is dispatched between the snapshot view and the listener
        becoming active. The returned snapshot can be replayed as
        initial state without dropping or duplicating events relative
        to what the listener will subsequently receive.

        If this is the first listener for `topic`, schedule an
        immediate poll so cold subscribers see data within one poll
        round-trip rather than waiting up to `poll_interval_sec` for
        the next sweep.
        """
        async with self._dispatch_lock:
            is_first = topic not in self._listeners
            sub = self.subscribe(topic, listener)
            snapshot = dict(self.cache.get(topic))
        if is_first:
            asyncio.create_task(self._poll_and_dispatch(topic))
        return sub, snapshot

    def unsubscribe(self, sub: Subscription[TopicKey]) -> None:
        listeners = self._listeners.get(sub.topic)
        if listeners is None:
            return
        try:
            listeners.remove(sub.listener)
        except ValueError:
            return
        if not listeners:
            self._listeners.pop(sub.topic, None)
            self.cache.clear(sub.topic)

    def snapshot(self, topic: TopicKey) -> dict[ItemId, Payload]:
        return self.cache.get(topic)

    async def _loop(self) -> None:
        log.info("%s refresher loop started", self.name)
        while True:
            for topic in list(self._listeners):
                await self._poll_and_dispatch(topic)
            await asyncio.sleep(self.poll_interval_sec)

    async def _poll_and_dispatch(self, topic: TopicKey) -> None:
        try:
            new_data = await asyncio.to_thread(self.poll_fn, topic)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("%s poll failed for topic=%s", self.name, topic)
            return

        async with self._dispatch_lock:
            old_data = self.cache.get(topic)
            self.cache.set(topic, new_data)
            for event in _diff_dict(old_data, new_data):
                await self._broadcast(topic, event)

    async def _broadcast(self, topic: TopicKey, event: DiffEvent) -> None:
        for listener in list(self._listeners.get(topic, ())):
            try:
                await listener(event)
            except Exception:
                log.exception(
                    "%s listener failed for topic=%s", self.name, topic
                )


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


async def serve_websocket(
    refresher: Refresher[TopicKey, ItemId, Payload],
    ws: WebSocket,
    topic: TopicKey,
) -> None:
    """Bridge a single WebSocket to one topic on a Refresher.

    Sends an initial `added` event per cache item (none if cold), then
    forwards live diff events until the client disconnects. Live
    events arriving during the initial replay are buffered into a
    queue and drained in order afterward, so the wire stream is
    consistent with the snapshot's view.
    """
    refresher.ensure_started()
    await ws.accept()

    queue: asyncio.Queue[DiffEvent] = asyncio.Queue()

    async def _enqueue(event: DiffEvent) -> None:
        await queue.put(event)

    sub, initial = await refresher.subscribe_and_snapshot(topic, _enqueue)
    try:
        for item in initial.values():
            await ws.send_json(asdict(AddedEvent(item=item)))

        send_task = asyncio.create_task(_drain_queue_to_ws(ws, queue))
        recv_task = asyncio.create_task(_drain_recv_until_disconnect(ws))
        try:
            await asyncio.wait(
                {send_task, recv_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for t in (send_task, recv_task):
                if not t.done():
                    t.cancel()
            for t in (send_task, recv_task):
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
    finally:
        refresher.unsubscribe(sub)


async def _drain_queue_to_ws(
    ws: WebSocket, queue: asyncio.Queue[DiffEvent]
) -> None:
    while True:
        event = await queue.get()
        try:
            await ws.send_json(asdict(event))
        except Exception:
            return


async def _drain_recv_until_disconnect(ws: WebSocket) -> None:
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        return
