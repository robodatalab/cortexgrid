"""Operator / Pipeline primitives for node setup and teardown.

An Operator is a discrete piece of cluster state with a matching setup/teardown
pair. A Pipeline is an ordered list of operators — setup() runs them in order,
teardown() runs them in reverse, so the last thing installed is the first
thing removed.
"""

import abc
import argparse
from dataclasses import dataclass, field
from typing import Optional

from fabric import Connection  # type: ignore


@dataclass
class Context:
    """Shared state passed through a pipeline run."""

    args: argparse.Namespace
    cfg: dict = field(default_factory=dict)
    entry: Optional[dict] = None
    connection: Optional[Connection] = None
    head_token: Optional[str] = None
    head_ip: Optional[str] = None


class Operator(abc.ABC):
    """Base class for pipeline operators. Override setup() and teardown()."""

    @abc.abstractmethod
    def setup(self, ctx: Context) -> None:
        pass

    @abc.abstractmethod
    def teardown(self, ctx: Context) -> None:
        pass


class Pipeline:
    def __init__(self, operators: list[Operator]):
        self.operators = operators

    def setup(self, ctx: Context) -> None:
        for op in self.operators:
            op.setup(ctx)

    def teardown(self, ctx: Context) -> None:
        for op in reversed(self.operators):
            op.teardown(ctx)
