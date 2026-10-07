"""Declare a serve-app's HTTP ingress without importing Ray.

    from cortexgrid import serve

    @serve.ingress
    class MyServeApp:
        @serve.endpoint
        async def predict(self, xs: list[float]) -> list[float]: ...

Unlike `ray.serve.ingress`, it builds the FastAPI app from the class's
`serve.endpoint` methods, and the class is left exactly as written: the
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

import inspect
from typing import Any, Callable, TypeVar, get_type_hints

from fastapi import FastAPI
from pydantic import TypeAdapter


_T = TypeVar("_T", bound=type)
_Method = TypeVar("_Method", bound=Callable[..., Any])

_INGRESS_APP_ATTR = "__cortexgrid_ingress_app__"
_ENDPOINT_ATTR = "__cortexgrid_endpoint__"


def endpoint(method: _Method) -> _Method:
    setattr(method, _ENDPOINT_ATTR, True)
    return method


def ingress(cls: _T) -> _T:
    """Mark a serve-app class as fronted by a FastAPI app serving its
    endpoints. Returns the class itself, unwrapped."""
    app = FastAPI()
    methods = inspect.getmembers(cls, inspect.isfunction)
    for name, method in methods:
        if getattr(method, _ENDPOINT_ATTR, False):
            route = _route_of(method)
            app.add_api_route(f"/{name}", route, methods=["POST"])
    setattr(cls, _INGRESS_APP_ATTR, app)
    return cls


def ingress_app(cls: type) -> Any | None:
    """The app `cls` was marked with by `ingress`, or None if it was not."""
    return getattr(cls, _INGRESS_APP_ATTR, None)


def _route_of(method: Callable[..., Any]) -> Callable[..., Any]:
    hints = get_type_hints(method)
    returned = TypeAdapter(hints.pop("return"))
    parameters = {name: TypeAdapter(hint) for name, hint in hints.items()}

    def arguments_of(body: dict[str, Any]) -> dict[str, Any]:
        return {
            name: parameter.validate_python(body[name])
            for name, parameter in parameters.items()
            if name in body
        }

    if inspect.iscoroutinefunction(method):

        async def route(self: Any, body: dict[str, Any]) -> Any:
            arguments = arguments_of(body)
            answered = await method(self, **arguments)
            answered_on_the_wire = returned.dump_python(answered, mode="json")
            return answered_on_the_wire

    else:

        def route(self: Any, body: dict[str, Any]) -> Any:
            arguments = arguments_of(body)
            answered = method(self, **arguments)
            answered_on_the_wire = returned.dump_python(answered, mode="json")
            return answered_on_the_wire

    route.__name__ = method.__name__
    route.__qualname__ = method.__qualname__
    return route
