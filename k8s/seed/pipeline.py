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
    @abc.abstractmethod
    def setup(self, deps: dict) -> None: ...

    @abc.abstractmethod
    def teardown(self, deps: dict) -> None: ...


class Pipeline(Operator):
    def __init__(self, operators: list[Operator]):
        self.operators = operators
        self.on_step_done: Optional[StepCallback] = None

    def setup(self, deps: dict) -> None:
        for op in self.operators:
            op.setup(deps)
            if self.on_step_done is not None:
                self.on_step_done(type(op).__name__)

    def teardown(self, deps: dict) -> None:
        for op in reversed(self.operators):
            op.teardown(deps)
            if self.on_step_done is not None:
                self.on_step_done(type(op).__name__)
