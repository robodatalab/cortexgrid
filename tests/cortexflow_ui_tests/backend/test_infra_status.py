from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from cortexflow_ui.backend.models.infra_status import (
    _classify,
    _collect_unreachable_nodes,
    get_infra_status,
)


def _make_node(name, ready_status="True", reason=None, no_ready_condition=False):
    conditions = (
        []
        if no_ready_condition
        else [SimpleNamespace(type="Ready", status=ready_status, reason=reason)]
    )
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name),
        status=SimpleNamespace(conditions=conditions),
    )


def _make_pod(
    name="p",
    namespace="ns",
    node_name="n1",
    phase="Running",
    containers_ready=True,
    waiting_reason=None,
):
    if waiting_reason is not None:
        cs = [
            SimpleNamespace(
                ready=False,
                state=SimpleNamespace(waiting=SimpleNamespace(reason=waiting_reason)),
            )
        ]
    else:
        cs = [
            SimpleNamespace(ready=containers_ready, state=SimpleNamespace(waiting=None))
        ]
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name, namespace=namespace),
        spec=SimpleNamespace(node_name=node_name),
        status=SimpleNamespace(phase=phase, container_statuses=cs),
    )


def _v1_with(nodes=(), pods=()):
    v1 = MagicMock()
    v1.list_node.return_value = SimpleNamespace(items=list(nodes))
    v1.list_pod_for_all_namespaces.return_value = SimpleNamespace(items=list(pods))
    return v1


class TestCollectUnreachableNodes(unittest.TestCase):
    def test_all_ready_returns_empty(self):
        v1 = _v1_with(nodes=[_make_node("n1"), _make_node("n2")])
        self.assertEqual(_collect_unreachable_nodes(v1), {})

    def test_unknown_status_records_reason(self):
        v1 = _v1_with(
            nodes=[
                _make_node("n1", ready_status="Unknown", reason="NodeStatusUnknown"),
            ]
        )
        self.assertEqual(_collect_unreachable_nodes(v1), {"n1": "NodeStatusUnknown"})

    def test_false_status_records_reason(self):
        v1 = _v1_with(
            nodes=[
                _make_node("n1", ready_status="False", reason="KubeletNotReady"),
            ]
        )
        self.assertEqual(_collect_unreachable_nodes(v1), {"n1": "KubeletNotReady"})

    def test_falls_back_to_status_when_reason_empty(self):
        v1 = _v1_with(nodes=[_make_node("n1", ready_status="False", reason=None)])
        self.assertEqual(_collect_unreachable_nodes(v1), {"n1": "False"})

    def test_falls_back_to_unknown_when_no_ready_condition(self):
        v1 = _v1_with(nodes=[_make_node("n1", no_ready_condition=True)])
        self.assertEqual(_collect_unreachable_nodes(v1), {"n1": "Unknown"})

    def test_only_unhealthy_nodes_are_recorded(self):
        v1 = _v1_with(
            nodes=[
                _make_node("healthy"),
                _make_node("dead", ready_status="Unknown", reason="NodeStatusUnknown"),
            ]
        )
        self.assertEqual(_collect_unreachable_nodes(v1), {"dead": "NodeStatusUnknown"})


class TestClassify(unittest.TestCase):
    def test_pod_on_unreachable_node_overrides_running_phase(self):
        pod = _make_pod(node_name="dead", phase="Running", containers_ready=True)
        self.assertEqual(
            _classify(pod, {"dead": "NodeStatusUnknown"}),
            (False, "node-unreachable: NodeStatusUnknown"),
        )

    def test_pod_on_healthy_node_uses_existing_logic(self):
        pod = _make_pod(node_name="alive", phase="Running", containers_ready=True)
        self.assertEqual(_classify(pod, {"dead": "NodeStatusUnknown"}), (True, "ready"))

    def test_default_none_keeps_existing_behavior(self):
        pod = _make_pod(phase="Running", containers_ready=True)
        self.assertEqual(_classify(pod), (True, "ready"))

    def test_empty_dict_keeps_existing_behavior(self):
        pod = _make_pod(phase="Running", containers_ready=True)
        self.assertEqual(_classify(pod, {}), (True, "ready"))

    def test_succeeded_pod(self):
        pod = _make_pod(phase="Succeeded")
        self.assertEqual(_classify(pod), (True, "succeeded"))

    def test_pod_with_waiting_reason(self):
        pod = _make_pod(phase="Pending", waiting_reason="ImagePullBackOff")
        self.assertEqual(_classify(pod), (False, "ImagePullBackOff"))


class TestGetInfraStatus(unittest.TestCase):
    @patch("cortexflow_ui.backend.models.infra_status._load_kube_config")
    @patch("cortexflow_ui.backend.models.infra_status.client.CoreV1Api")
    def test_pod_on_dead_node_is_unhealthy(self, mock_api_cls, _mock_cfg):
        v1 = _v1_with(
            nodes=[
                _make_node("alive"),
                _make_node("dead", ready_status="Unknown", reason="NodeStatusUnknown"),
            ],
            pods=[
                _make_pod(name="ok-pod", namespace="ns1", node_name="alive"),
                _make_pod(name="ghost-pod", namespace="ns2", node_name="dead"),
            ],
        )
        v1.read_namespaced_pod_log.return_value = "fake logs"
        mock_api_cls.return_value = v1

        result = get_infra_status()

        self.assertFalse(result.overall)
        by_name = {p.name: p for p in result.pods}
        self.assertTrue(by_name["ok-pod"].healthy)
        self.assertEqual(by_name["ok-pod"].health, "ready")
        self.assertFalse(by_name["ghost-pod"].healthy)
        self.assertEqual(
            by_name["ghost-pod"].health, "node-unreachable: NodeStatusUnknown"
        )

    @patch("cortexflow_ui.backend.models.infra_status._load_kube_config")
    @patch("cortexflow_ui.backend.models.infra_status.client.CoreV1Api")
    def test_all_healthy_overall_true(self, mock_api_cls, _mock_cfg):
        v1 = _v1_with(
            nodes=[_make_node("alive")],
            pods=[_make_pod(name="ok-pod", namespace="ns", node_name="alive")],
        )
        mock_api_cls.return_value = v1

        result = get_infra_status()

        self.assertTrue(result.overall)
        self.assertEqual(len(result.pods), 1)
        self.assertEqual(result.pods[0].health, "ready")


if __name__ == "__main__":
    unittest.main()
