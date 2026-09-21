"""Operator / Pipeline primitives for node setup and teardown.

Dependencies flow as an explicit `deps` dict passed to setup() and teardown().
Operators pull what they need out of it; a missing key raises KeyError, so
unmet dependencies fail loudly rather than silently skipping.

Constructors hold per-instance configuration only (role, mode, strictness).

Pipelines expose an optional `on_step_done` callback fired after each operator
completes; callers use it to drive a progress bar and persist checkpoints.
"""

import abc
from typing import Callable, Optional


StepCallback = Callable[[str], None]


class Operator(abc.ABC):
    @property
    def name(self) -> str:
        return type(self).__name__

    def applies(self, deps: dict) -> bool:
        """Whether the operator acts under `deps`; a pipeline skips it otherwise."""
        return True

    @abc.abstractmethod
    def setup(self, deps: dict) -> None: ...

    @abc.abstractmethod
    def teardown(self, deps: dict) -> None: ...


class ConditionalOperator(Operator):
    """Wraps an Operator and gates its setup/teardown on a predicate over deps.

    Surfaces the inner operator's name so the steps `done` in
    infra-config.yaml stay readable (e.g. `PostgresCredentials`, not
    `ConditionalOperator`).
    """

    def __init__(self, inner: Operator, predicate: Callable[[dict], bool]):
        self.inner = inner
        self.predicate = predicate

    @property
    def name(self) -> str:
        return self.inner.name

    def applies(self, deps: dict) -> bool:
        return self.predicate(deps)

    def setup(self, deps: dict) -> None:
        if self.predicate(deps):
            self.inner.setup(deps)

    def teardown(self, deps: dict) -> None:
        if self.predicate(deps):
            self.inner.teardown(deps)


class Pipeline(Operator):
    def __init__(self, operators: list[Operator]):
        self.operators = operators
        self.on_step_done: Optional[StepCallback] = None

    def steps(self, deps: dict) -> list[Operator]:
        """The operators that act under `deps`, in setup order."""
        return [op for op in self.operators if op.applies(deps)]

    def setup(self, deps: dict) -> None:
        for op in self.steps(deps):
            op.setup(deps)
            if self.on_step_done is not None:
                self.on_step_done(op.name)

    def teardown(self, deps: dict) -> None:
        for op in reversed(self.steps(deps)):
            op.teardown(deps)
            if self.on_step_done is not None:
                self.on_step_done(op.name)


# trigger: 315-trigger-tests-2026-05-16
