from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from ray.serve.config import AutoscalingPolicy

from cortexgrid._model_scheduler import (
    _ClusterOccupancy,
    _ModelScheduler,
    _NodeOccupancy,
    _ReplicaWaitingForRoom,
    model_autoscaling_config,
)


_WHOLE_GPU = {"GPU": 1.0, "vram_mib": 16384.0}
_HALF_GPU = {"GPU": 0.5, "vram_mib": 8192.0}
_FIRST = "ServeReplica:first:App"
_SECOND = "ServeReplica:second:App"
_THIRD = "ServeReplica:third:App"
_BASE = "ServeReplica:base:App"
_DEPENDENT = "ServeReplica:dependent:App"
_OTHER_DEPENDENT = "ServeReplica:other-dependent:App"
_NEWCOMER = "ServeReplica:newcomer:App"
_ON_BASE = ["base"]


def _app_of(replica_class_name: str) -> str:
    return replica_class_name.split(":")[1]


def _node_held_by(
    held: dict[str, dict[str, float]], total: dict[str, float]
) -> _NodeOccupancy:
    free = dict(total)
    for resources in held.values():
        for name, amount in resources.items():
            free[name] -= amount
    return _NodeOccupancy(free_resources=free, resources_held_by_model=held)


def _card_held_by(held: dict[str, dict[str, float]]) -> _NodeOccupancy:
    return _node_held_by(held, _WHOLE_GPU)


def _waiting(
    required: dict[str, float], replica_class_name: str = _NEWCOMER
) -> _ReplicaWaitingForRoom:
    return _ReplicaWaitingForRoom(
        actor_id="pending",
        replica_class_name=replica_class_name,
        required_resources=required,
    )


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

_LOAD_POLICY_WITH_CORTEXGRID_MISSING = """
import importlib.abc
import sys

class CortexgridIsNotInstalled(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if name.split(".")[0] == "cortexgrid":
            raise ModuleNotFoundError(name)

sys.meta_path.insert(0, CortexgridIsNotInstalled())

import cloudpickle

policy_class = cloudpickle.loads(sys.stdin.buffer.read())
policy_class(required_apps=["base"])
"""


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

    def _report(
        self, name: str, has_requests: bool, required_apps: list[str] | None = None
    ) -> bool:
        self.now += 1.0
        return self.scheduler._record_activity_and_check_pause(
            name, _app_of(name), required_apps or [], has_requests, self.now
        )

    def _all_report_idle(self, required_apps_by_model: dict[str, list[str]]) -> set[str]:
        return {
            name
            for name, required_apps in required_apps_by_model.items()
            if self._report(name, has_requests=False, required_apps=required_apps)
        }

    def _serve_in_turn(self, required_apps_by_model: dict[str, list[str]]) -> None:
        for name, required_apps in required_apps_by_model.items():
            self._report(name, has_requests=True, required_apps=required_apps)
        self._all_report_idle(required_apps_by_model)

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

    def test_never_pauses_a_model_the_waiting_replica_requires(self) -> None:
        models = {_BASE: [], _OTHER_DEPENDENT: []}
        self._serve_in_turn(models)
        self.cluster = _ClusterOccupancy(
            nodes=[_card_held_by({_BASE: _HALF_GPU, _OTHER_DEPENDENT: _HALF_GPU})],
            replicas_waiting_for_room=[_waiting(_HALF_GPU, _DEPENDENT)],
        )
        self._report(_DEPENDENT, has_requests=False, required_apps=_ON_BASE)

        self.assertEqual(self._all_report_idle(models), {_OTHER_DEPENDENT})

    def test_a_dependent_on_another_node_is_paused_with_the_model_it_requires(
        self,
    ) -> None:
        models = {_BASE: [], _DEPENDENT: _ON_BASE}
        self._serve_in_turn(models)
        self.cluster = _ClusterOccupancy(
            nodes=[
                _card_held_by({_BASE: _WHOLE_GPU}),
                _node_held_by({_DEPENDENT: {"CPU": 1.0}}, {"CPU": 1.0}),
            ],
            replicas_waiting_for_room=[_waiting(_WHOLE_GPU)],
        )

        self.assertEqual(self._all_report_idle(models), {_BASE, _DEPENDENT})

    def test_pauses_a_model_together_with_the_dependents_running_on_it(self) -> None:
        models = {_DEPENDENT: _ON_BASE, _BASE: []}
        self._serve_in_turn(models)
        self.cluster = _ClusterOccupancy(
            nodes=[_card_held_by({_BASE: _HALF_GPU, _DEPENDENT: _HALF_GPU})],
            replicas_waiting_for_room=[_waiting(_WHOLE_GPU)],
        )

        self.assertEqual(self._all_report_idle(models), {_BASE, _DEPENDENT})

    def test_does_not_pause_a_model_whose_dependent_is_busy(self) -> None:
        self._serve_in_turn({_BASE: [], _DEPENDENT: _ON_BASE})
        self.cluster = _ClusterOccupancy(
            nodes=[
                _card_held_by({_BASE: _WHOLE_GPU}),
                _node_held_by({_DEPENDENT: {"CPU": 1.0}}, {"CPU": 1.0}),
            ],
            replicas_waiting_for_room=[_waiting(_WHOLE_GPU)],
        )
        self._report(_DEPENDENT, has_requests=True, required_apps=_ON_BASE)

        self.assertFalse(self._report(_BASE, has_requests=False))

    def test_pauses_a_dependent_rather_than_the_model_both_dependents_require(
        self,
    ) -> None:
        quarter_gpu = {"GPU": 0.25, "vram_mib": 4096.0}
        models = {_DEPENDENT: _ON_BASE, _OTHER_DEPENDENT: _ON_BASE, _BASE: []}
        self._serve_in_turn(models)
        self.cluster = _ClusterOccupancy(
            nodes=[
                _card_held_by(
                    {
                        _BASE: _HALF_GPU,
                        _DEPENDENT: quarter_gpu,
                        _OTHER_DEPENDENT: quarter_gpu,
                    }
                )
            ],
            replicas_waiting_for_room=[_waiting(quarter_gpu)],
        )

        self.assertEqual(self._all_report_idle(models), {_DEPENDENT})

    def test_pausing_a_dependent_leaves_the_model_it_requires_serving(self) -> None:
        models = {_DEPENDENT: _ON_BASE, _BASE: []}
        self._serve_in_turn(models)
        self.cluster = _ClusterOccupancy(
            nodes=[_card_held_by({_BASE: _HALF_GPU, _DEPENDENT: _HALF_GPU})],
            replicas_waiting_for_room=[_waiting(_HALF_GPU)],
        )

        self.assertEqual(self._all_report_idle(models), {_DEPENDENT})

    def test_a_paused_dependent_does_not_hold_the_model_it_requires(self) -> None:
        models = {_DEPENDENT: _ON_BASE, _BASE: []}
        self._serve_in_turn(models)
        self.cluster = _ClusterOccupancy(
            nodes=[_card_held_by({_BASE: _WHOLE_GPU})],
            replicas_waiting_for_room=[_waiting(_WHOLE_GPU)],
        )

        self.assertEqual(self._all_report_idle(models), {_BASE})


class TestPolicyLoadsInTheServeController(unittest.TestCase):
    def test_policy_loads_where_cortexgrid_is_not_installed(self) -> None:
        policy = AutoscalingPolicy(**model_autoscaling_config(1, ["base"])["policy"])
        with tempfile.TemporaryDirectory() as directory_without_cortexgrid:
            loading = subprocess.run(
                [sys.executable, "-c", _LOAD_POLICY_WITH_CORTEXGRID_MISSING],
                input=policy.get_serialized_policy_def(),
                capture_output=True,
                cwd=directory_without_cortexgrid,
            )

        self.assertEqual(loading.returncode, 0, loading.stderr.decode())


if __name__ == "__main__":
    unittest.main()
