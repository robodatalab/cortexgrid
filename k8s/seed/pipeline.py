"""Operator / Pipeline primitives for node setup and teardown.

Dependencies flow as an explicit `deps` dict passed to setup() and teardown().
Operators pull what they need out of it; a missing key raises KeyError, so
unmet dependencies fail loudly rather than silently skipping.

Constructors hold per-instance configuration only (role, mode, strictness).
"""

import abc


class Operator(abc.ABC):
    @abc.abstractmethod
    def setup(self, deps: dict) -> None: ...

    @abc.abstractmethod
    def teardown(self, deps: dict) -> None: ...


class Pipeline(Operator):
    def __init__(self, operators: list[Operator]):
        self.operators = operators

    def setup(self, deps: dict) -> None:
        for op in self.operators:
            op.setup(deps)

    def teardown(self, deps: dict) -> None:
        for op in reversed(self.operators):
            op.teardown(deps)
