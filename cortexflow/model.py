"""Public Model abstract base + the DeployedModel proxy returned by deploy_model.

A user-defined model inherits from `cortexflow.Model`, implements `infer`,
`save`, and `load`, and works two ways:

  - Locally:        m = MyModel(...); m.infer(x)
  - On the cluster: cortexflow.save_model(m, family, suffix);
                    deployed = cortexflow.deploy_model(family, suffix, run_name)
                    deployed.infer(x)  # routed over HTTP to the Ray Serve replica

The replica wraps the user's class with a generic FastAPI shim that exposes
a single POST /infer endpoint forwarding (*args, **kwargs) to the model.
"""

from __future__ import annotations

import abc
from pathlib import Path
from typing import Any

import requests


class Model(abc.ABC):
    """Inherit to make a class deployable via `cortexflow.deploy_model`.

    Class attributes `num_gpus` and `num_replicas` configure the Ray Serve
    actor when deployed; override on the subclass for GPU/replica needs.
    """

    num_gpus: int = 0
    num_replicas: int = 1

    @abc.abstractmethod
    def infer(self, *args: Any, **kwargs: Any) -> Any:
        """Run inference. Whatever shape you like; the proxy forwards
        positional and keyword args unchanged."""

    @abc.abstractmethod
    def save(self, d: Path) -> None:
        """Persist this model's state into directory `d`."""

    @classmethod
    @abc.abstractmethod
    def load(cls, d: Path) -> "Model":
        """Reconstruct an instance from a previously-saved directory `d`."""


class DeployedModel:
    """Client-side proxy returned by `cortexflow.deploy_model`. Calling
    `.infer(*args, **kwargs)` POSTs JSON to the deployed app's /infer route
    and returns the decoded result."""

    def __init__(self, url: str) -> None:
        self.url = url

    def infer(self, *args: Any, **kwargs: Any) -> Any:
        response = requests.post(
            f"{self.url}/infer",
            json={"args": list(args), "kwargs": kwargs},
            timeout=60,
        )
        response.raise_for_status()
        return response.json()["result"]
