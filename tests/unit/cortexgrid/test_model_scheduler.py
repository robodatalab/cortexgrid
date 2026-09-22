from __future__ import annotations

import unittest
from unittest.mock import patch

from cortexgrid._model_scheduler import (
    _ClusterOccupancy,
    _ModelScheduler,
    _NodeOccupancy,
    _ReplicaWaitingForRoom,
)


_WHOLE_GPU = {"GPU": 1.0, "vram_mib": 16384.0}
_HALF_GPU = {"GPU": 0.5, "vram_mib": 8192.0}
_FIRST = "ServeReplica:first:App"
_SECOND = "ServeReplica:second:App"
_THIRD = "ServeReplica:third:App"


def _card_held_by(held: dict[str, dict[str, float]]) -> _NodeOccupancy:
    free = dict(_WHOLE_GPU)
    for resources in held.values():
        for name, amount in resources.items():
            free[name] -= amount
    return _NodeOccupancy(free_resources=free, resources_held_by_model=held)


def _waiting(required: dict[str, float]) -> _ReplicaWaitingForRoom:
    return _ReplicaWaitingForRoom(actor_id="pending", required_resources=required)


def _as_seen_by_scheduler(
    occupancy: _ClusterOccupancy, scheduled_replica_class_names: set[str]
) -> _ClusterOccupancy:
    return _ClusterOccupancy(
        nodes=[
            _NodeOccupancy(
                free_resources=node.free_resources,
                resources_held_by_model={
                    name: held
                    for name, held in node.resources_held_by_model.items()
                    if name in scheduled_replica_class_names
                },
            )
            for node in occupancy.nodes
        ],
        replicas_waiting_for_room=occupancy.replicas_waiting_for_room,
    )


_NO_REPLICA_WAITING = _ClusterOccupancy(nodes=[], replicas_waiting_for_room=[])


class TestModelScheduler(unittest.TestCase):
    def setUp(self) -> None:
        self.scheduler = _ModelScheduler()
        self.now = 0.0
        self.cluster = _NO_REPLICA_WAITING
        patcher_waiting = patch(
            "cortexgrid._model_scheduler._any_serve_replica_waiting_for_room",
            side_effect=lambda: bool(self.cluster.replicas_waiting_for_room),
        )
        patcher_occupancy = patch(
            "cortexgrid._model_scheduler._read_cluster_occupancy",
            side_effect=lambda names: _as_seen_by_scheduler(self.cluster, names),
        )
        self.read_occupancy = patcher_occupancy.start()
        patcher_waiting.start()
        self.addCleanup(patcher_occupancy.stop)
        self.addCleanup(patcher_waiting.stop)

    def _report(self, name: str, has_requests: bool) -> bool:
        self.now += 1.0
        return self.scheduler._record_activity_and_check_pause(
            name, has_requests, self.now
        )

    def test_pauses_the_idle_model_holding_the_card_a_waiting_replica_needs(self) -> None:
        self.cluster = _ClusterOccupancy(
            nodes=[_card_held_by({_SECOND: _WHOLE_GPU})],
            replicas_waiting_for_room=[_waiting(_WHOLE_GPU)],
        )

        self.assertTrue(self._report(_SECOND, has_requests=False))

    def test_never_pauses_a_model_with_requests(self) -> None:
        self.cluster = _ClusterOccupancy(
            nodes=[_card_held_by({_SECOND: _WHOLE_GPU})],
            replicas_waiting_for_room=[_waiting(_WHOLE_GPU)],
        )

        self.assertFalse(self._report(_SECOND, has_requests=True))

    def test_pauses_nothing_while_a_node_has_room_for_the_waiting_replica(self) -> None:
        self.cluster = _ClusterOccupancy(
            nodes=[_card_held_by({_SECOND: _WHOLE_GPU}), _card_held_by({})],
            replicas_waiting_for_room=[_waiting(_WHOLE_GPU)],
        )

        self.assertFalse(self._report(_SECOND, has_requests=False))

    def test_pauses_the_least_recently_used_of_two_idle_models(self) -> None:
        self._report(_SECOND, has_requests=True)
        self._report(_THIRD, has_requests=True)
        self._report(_SECOND, has_requests=False)
        self._report(_THIRD, has_requests=False)
        self.cluster = _ClusterOccupancy(
            nodes=[_card_held_by({_SECOND: _HALF_GPU, _THIRD: _HALF_GPU})],
            replicas_waiting_for_room=[_waiting(_HALF_GPU)],
        )

        self.assertTrue(self._report(_SECOND, has_requests=False))
        self.assertFalse(self._report(_THIRD, has_requests=False))

    def test_pauses_nothing_when_even_pausing_every_idle_model_would_not_make_room(
        self,
    ) -> None:
        self._report(_FIRST, has_requests=True)
        self.cluster = _ClusterOccupancy(
            nodes=[_card_held_by({_SECOND: _HALF_GPU, _FIRST: _HALF_GPU})],
            replicas_waiting_for_room=[_waiting(_WHOLE_GPU)],
        )

        self.assertFalse(self._report(_SECOND, has_requests=False))

    def test_reads_no_occupancy_while_no_replica_waits(self) -> None:
        self.assertFalse(self._report(_SECOND, has_requests=False))
        self.read_occupancy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
