"""Declare a serve-app's HTTP ingress without importing Ray.

    from cortexgrid import serve

    @serve.ingress(app)
    class MyServeApp: ...

Same shape as `ray.serve.ingress`, but the class is left exactly as written: the
FastAPI app is only recorded on it, and `cortexgrid._serve_entry.build` applies
Ray's ingress when it builds the Serve application on the cluster.

Ray's decorator replaces the class with a wrapper subclass defined in
ray/serve/api.py; older Ray (e.g. 2.9) leaves the wrapper's __module__ naming
that module. Everything that locates a serve-app by its module - bundling its
source, recording its import path - would then find Ray instead of the user's
code. Deferring the wrap to the one place Serve needs it keeps the class
locatable everywhere else (the laptop, Ray jobs, tests).
"""

from __future__ import annotations

from typing import Any, Callable, TypeVar


_T = TypeVar("_T", bound=type)

_INGRESS_APP_ATTR = "__cortexgrid_ingress_app__"


def ingress(app: Any) -> Callable[[_T], _T]:
    """Mark a serve-app class as fronted by the ASGI `app` (e.g. a FastAPI
    instance). Returns the class itself, unwrapped."""

    def decorator(cls: _T) -> _T:
        setattr(cls, _INGRESS_APP_ATTR, app)
        return cls

    return decorator


def ingress_app(cls: type) -> Any | None:
    """The app `cls` was marked with by `ingress`, or None if it was not."""
    return getattr(cls, _INGRESS_APP_ATTR, None)
