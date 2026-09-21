from __future__ import annotations

import unittest

from k8s.seed.pipeline import ConditionalOperator, Operator, Pipeline


class _Recorder(Operator):
    """Minimal Operator that appends events to a shared log on setup/teardown."""

    def __init__(self, tag: str, events: list[str]):
        self.tag = tag
        self.events = events

    def setup(self, deps: dict) -> None:
        self.events.append(f"{self.tag}.setup")

    def teardown(self, deps: dict) -> None:
        self.events.append(f"{self.tag}.teardown")


class _Boom(Operator):
    """Operator whose setup always raises. Used to assert callback ordering on failure."""

    def setup(self, deps: dict) -> None:
        raise RuntimeError("boom")

    def teardown(self, deps: dict) -> None:
        raise RuntimeError("boom")


class TestPipelineOrdering(unittest.TestCase):
    """Pipeline runs operators forward for setup, reversed for teardown."""

    def test_setup_runs_operators_in_order(self) -> None:
        events: list[str] = []
        Pipeline([_Recorder("A", events), _Recorder("B", events)]).setup({})
        self.assertEqual(events, ["A.setup", "B.setup"])

    def test_teardown_runs_operators_in_reverse(self) -> None:
        events: list[str] = []
        Pipeline([_Recorder("A", events), _Recorder("B", events)]).teardown({})
        self.assertEqual(events, ["B.teardown", "A.teardown"])


class TestPipelineOnStepDone(unittest.TestCase):
    """on_step_done fires after each operator with its class name."""

    def test_callback_fires_per_operator_on_setup(self) -> None:
        events: list[str] = []
        seen: list[str] = []
        p = Pipeline([_Recorder("A", events), _Recorder("B", events)])
        p.on_step_done = seen.append
        p.setup({})
        self.assertEqual(seen, ["_Recorder", "_Recorder"])

    def test_callback_fires_in_reverse_on_teardown(self) -> None:
        events: list[str] = []
        seen: list[tuple[str, str]] = []
        p = Pipeline(
            [_Recorder("A", events), _Recorder("B", events)]
        )
        p.on_step_done = lambda name: seen.append((name, events[-1]))
        p.teardown({})
        # After each teardown, the last event is that operator's teardown line —
        # we use it to verify callback ordering matches reverse iteration.
        self.assertEqual([ev for _, ev in seen], ["B.teardown", "A.teardown"])

    def test_callback_not_fired_after_failure(self) -> None:
        events: list[str] = []
        seen: list[str] = []
        p = Pipeline([_Recorder("A", events), _Boom(), _Recorder("C", events)])
        p.on_step_done = seen.append
        with self.assertRaises(RuntimeError):
            p.setup({})
        # A's setup completed → callback fired once. Boom raised before its
        # callback could fire. C.setup never ran.
        self.assertEqual(seen, ["_Recorder"])
        self.assertEqual(events, ["A.setup"])

    def test_callback_is_optional(self) -> None:
        events: list[str] = []
        p = Pipeline([_Recorder("A", events)])
        # on_step_done defaults to None; setup/teardown must not raise.
        p.setup({})
        p.teardown({})
        self.assertEqual(events, ["A.setup", "A.teardown"])


class TestPipelineSkipsInapplicableSteps(unittest.TestCase):
    """A ConditionalOperator whose predicate is false is not a step: it neither
    runs nor reports done, so it is never recorded as done."""

    def _pipeline(self, events: list[str]) -> Pipeline:
        return Pipeline([
            _Recorder("A", events),
            ConditionalOperator(_Recorder("B", events), lambda deps: deps["b"]),
        ])

    def test_setup_and_teardown_skip_it(self) -> None:
        events: list[str] = []
        seen: list[str] = []
        p = self._pipeline(events)
        p.on_step_done = seen.append
        p.setup({"b": False})
        p.teardown({"b": False})
        self.assertEqual(events, ["A.setup", "A.teardown"])
        self.assertEqual(seen, ["_Recorder", "_Recorder"])

    def test_steps_lists_only_applicable_operators(self) -> None:
        p = self._pipeline([])
        self.assertEqual(len(p.steps({"b": False})), 1)
        self.assertEqual(len(p.steps({"b": True})), 2)


if __name__ == "__main__":
    unittest.main()
