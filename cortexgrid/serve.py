"""Declare a serve-app's HTTP ingress without importing Ray.

    from cortexgrid import serve

    @serve.ingress
    class MyServeApp:
        @serve.endpoint
        async def predict(self, xs: list[float]) -> list[float]: ...

Unlike `ray.serve.ingress`, it builds the FastAPI app from the class's
`serve.endpoint` methods, and the class is left unwrapped: the FastAPI app
and the client generated for it are only recorded on it, and
`cortexgrid._serve_entry.build` applies Ray's ingress when it builds the Serve
application on the cluster.

Ray's decorator replaces the class with a wrapper subclass defined in
ray/serve/api.py; older Ray (e.g. 2.9) leaves the wrapper's __module__ naming
that module. Everything that locates a serve-app by its module - bundling its
source, recording its import path - would then find Ray instead of the user's
code. Deferring the wrap to the one place Serve needs it keeps the class
locatable everywhere else (the laptop, Ray jobs, tests).
"""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Callable, TypeVar, get_args, get_type_hints

import httpx
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import TypeAdapter

from cortexgrid.model_serving.lifecycle import Deployment, DeploymentClient


_T = TypeVar("_T", bound=type)
_Method = TypeVar("_Method", bound=Callable[..., Any])

_INGRESS_APP_ATTR = "__cortexgrid_ingress_app__"
_ENDPOINT_ATTR = "__cortexgrid_endpoint__"


def endpoint(method: _Method) -> _Method:
    setattr(method, _ENDPOINT_ATTR, True)
    return method


def ingress(cls: _T) -> _T:
    """Mark a serve-app class as fronted by a FastAPI app serving its
    endpoints, and give it the client that calls them. Returns the class
    itself, unwrapped."""
    app = FastAPI()
    calls: dict[str, Any] = {"__module__": cls.__module__}
    methods = inspect.getmembers(cls, inspect.isfunction)
    for name, method in methods:
        if getattr(method, _ENDPOINT_ATTR, False):
            marshalling = _EndpointMarshalling.of(method)
            route = _route_of(method, marshalling)
            app.add_api_route(f"/{name}", route, methods=["POST"])
            calls[name] = _call_of(name, method, marshalling)
    client = type(f"{cls.__name__}Client", (_EndpointsClient,), calls)
    setattr(cls, _INGRESS_APP_ATTR, app)
    setattr(cls, "client", client)
    return cls


def ingress_app(cls: type) -> Any | None:
    """The app `cls` was marked with by `ingress`, or None if it was not."""
    return getattr(cls, _INGRESS_APP_ATTR, None)


class _EndpointsClient(DeploymentClient):
    def __init__(self, deployment: Deployment[Any]) -> None:
        super().__init__(key=deployment.key, url=deployment.url)


@dataclass(frozen=True)
class _EndpointMarshalling:
    signature_without_self: inspect.Signature
    parameters: dict[str, TypeAdapter[Any]]
    answer: TypeAdapter[Any]

    @classmethod
    def of(cls, method: Callable[..., Any]) -> _EndpointMarshalling:
        signature = inspect.signature(method)
        parameter_values = signature.parameters.values()
        parameters_in_order = list(parameter_values)
        signature_without_self = signature.replace(parameters=parameters_in_order[1:])
        hints = get_type_hints(method)
        returned_hint = hints.pop("return")
        if inspect.isasyncgenfunction(method):
            streamed_hints = get_args(returned_hint)
            answer_hint = streamed_hints[0]
        else:
            answer_hint = returned_hint
        answer = TypeAdapter(answer_hint)
        parameters = {name: TypeAdapter(hint) for name, hint in hints.items()}
        return cls(signature_without_self, parameters, answer)

    def arguments_to_json(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        bound = self.signature_without_self.bind(*args, **kwargs)
        return {
            name: self.parameters[name].dump_python(value, mode="json")
            for name, value in bound.arguments.items()
        }

    def arguments_from_json(self, body: dict[str, Any]) -> dict[str, Any]:
        return {
            name: parameter.validate_python(body[name])
            for name, parameter in self.parameters.items()
            if name in body
        }

    def answer_to_json(self, answer: Any) -> Any:
        return self.answer.dump_python(answer, mode="json")

    def answer_from_json(self, answered: Any) -> Any:
        return self.answer.validate_python(answered)

    def answer_to_json_line(self, answer: Any) -> bytes:
        answered = self.answer.dump_json(answer)
        return answered + b"\n"

    def answer_from_json_line(self, line: str) -> Any:
        return self.answer.validate_json(line)


def _route_of(
    method: Callable[..., Any], marshalling: _EndpointMarshalling
) -> Callable[..., Any]:
    if inspect.isasyncgenfunction(method):

        async def route(self: Any, body: dict[str, Any]) -> Any:
            arguments = marshalling.arguments_from_json(body)
            answers = method(self, **arguments)
            lines = _json_lines_of(answers, marshalling)
            streamed = StreamingResponse(lines, media_type="application/x-ndjson")
            return streamed

    elif inspect.iscoroutinefunction(method):

        async def route(self: Any, body: dict[str, Any]) -> Any:
            arguments = marshalling.arguments_from_json(body)
            answer = await method(self, **arguments)
            answered = marshalling.answer_to_json(answer)
            return answered

    else:

        def route(self: Any, body: dict[str, Any]) -> Any:
            arguments = marshalling.arguments_from_json(body)
            answer = method(self, **arguments)
            answered = marshalling.answer_to_json(answer)
            return answered

    route.__name__ = method.__name__
    route.__qualname__ = method.__qualname__
    return route


async def _json_lines_of(
    answers: AsyncIterator[Any], marshalling: _EndpointMarshalling
) -> AsyncIterator[bytes]:
    async for answer in answers:
        line = marshalling.answer_to_json_line(answer)
        yield line


def _call_of(
    name: str, method: Callable[..., Any], marshalling: _EndpointMarshalling
) -> Callable[..., Any]:
    if inspect.isasyncgenfunction(method):

        async def call(self: _EndpointsClient, *args: Any, **kwargs: Any) -> Any:
            body = marshalling.arguments_to_json(*args, **kwargs)
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    "POST", f"{self.url}/{name}", json=body
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        answer = marshalling.answer_from_json_line(line)
                        yield answer

    elif inspect.iscoroutinefunction(method):

        async def call(self: _EndpointsClient, *args: Any, **kwargs: Any) -> Any:
            body = marshalling.arguments_to_json(*args, **kwargs)
            async with httpx.AsyncClient(timeout=None) as client:
                response = await client.post(f"{self.url}/{name}", json=body)
            response.raise_for_status()
            answered = response.json()
            answer = marshalling.answer_from_json(answered)
            return answer

    else:

        def call(self: _EndpointsClient, *args: Any, **kwargs: Any) -> Any:
            body = marshalling.arguments_to_json(*args, **kwargs)
            with httpx.Client(timeout=None) as client:
                response = client.post(f"{self.url}/{name}", json=body)
            response.raise_for_status()
            answered = response.json()
            answer = marshalling.answer_from_json(answered)
            return answer

    call.__name__ = name
    return call
