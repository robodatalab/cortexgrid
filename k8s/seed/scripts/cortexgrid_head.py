#!/usr/bin/env python3
"""Head secrets server: stores secrets in a .env file and serves them over HTTP.

Uploaded to /usr/local/bin/cortexgrid-head.py by the HeadServer operator and
run by the cortexgrid-head systemd unit. It runs on the head host rather than
in k8s, so it is up before the cluster: cortexgrid.secrets clients, the seed
pipeline and the External Secrets Operator all read secrets through it.

  GET    /secrets       -> ["ID", ...]
  GET    /secrets/<id>  -> {"value": "..."}, 404 if missing
  PUT    /secrets/<id>  <- {"value": "..."}, 204
  DELETE /secrets/<id>  -> 204, also when the id doesn't exist

No authentication: the head is only reachable over the tailnet.

The file uses .env syntax so it can be read and edited by hand. Writes rewrite
the whole file, so hand-written comments don't survive them.

Standard library only, so the head needs nothing beyond python3.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast
from urllib.parse import unquote, urlsplit


_ID = re.compile(r"[A-Za-z0-9_.-]+")
_ENTRY = re.compile(
    r"^[ \t]*(?:export[ \t]+)?(?P<key>[A-Za-z0-9_.-]+)[ \t]*=[ \t]*"
    r"(?:\"(?P<double>(?:[^\"\\]|\\[\s\S])*)\"|'(?P<single>[^']*)'|(?P<bare>[^\n#]*?))"
    r"[ \t]*(?:#[^\n]*)?$",
    re.MULTILINE,
)
_UNESCAPES = {"n": "\n", "r": "\r", '"': '"', "\\": "\\"}


def parse_env(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for m in _ENTRY.finditer(text):
        if m.group("double") is not None:
            values[m.group("key")] = re.sub(
                r"\\(.)",
                lambda e: _UNESCAPES.get(e.group(1), e.group(0)),
                m.group("double"),
            )
        elif m.group("single") is not None:
            values[m.group("key")] = m.group("single")
        else:
            values[m.group("key")] = m.group("bare")
    return values


def format_env(values: dict[str, str]) -> str:
    lines = []
    for key, value in values.items():
        escaped = (
            value.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\r", "\\r")
        )
        lines.append(f'{key}="{escaped}"\n')
    return "".join(lines)


class EnvFileStore:
    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()

    def list(self) -> list[str]:
        with self._lock:
            return sorted(self._read())

    def get(self, id: str) -> str | None:
        with self._lock:
            return self._read().get(id)

    def set(self, id: str, value: str) -> None:
        with self._lock:
            values = self._read()
            values[id] = value
            self._write(values)

    def delete(self, id: str) -> None:
        with self._lock:
            values = self._read()
            if values.pop(id, None) is not None:
                self._write(values)

    def _read(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        return parse_env(self._path.read_text())

    def _write(self, values: dict[str, str]) -> None:
        # Write a sibling temp file (mkstemp creates it 0600) and rename it over
        # the store, so a crash mid-write never leaves a truncated file.
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".env.")
        with os.fdopen(fd, "w") as f:
            f.write(format_env(values))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self._path)


class SecretsServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], store: EnvFileStore):
        super().__init__(address, SecretsHandler)
        self.store = store


class SecretsHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if urlsplit(self.path).path == "/secrets":
            self._send_json(HTTPStatus.OK, self._store().list())
            return
        id = self._secret_id()
        if id is None:
            return
        value = self._store().get(id)
        if value is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": f"secret {id!r} not found"})
            return
        self._send_json(HTTPStatus.OK, {"value": value})

    def do_PUT(self) -> None:
        id = self._secret_id()
        if id is None:
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            value = json.loads(self.rfile.read(length))["value"]
        except (ValueError, KeyError, TypeError):
            value = None
        if not isinstance(value, str):
            self._send_json(
                HTTPStatus.BAD_REQUEST, {"error": 'body must be {"value": "<string>"}'}
            )
            return
        self._store().set(id, value)
        self._send_empty(HTTPStatus.NO_CONTENT)

    def do_DELETE(self) -> None:
        id = self._secret_id()
        if id is None:
            return
        self._store().delete(id)
        self._send_empty(HTTPStatus.NO_CONTENT)

    def _store(self) -> EnvFileStore:
        return cast(SecretsServer, self.server).store

    def _secret_id(self) -> str | None:
        path = urlsplit(self.path).path
        prefix = "/secrets/"
        if not path.startswith(prefix):
            self._send_json(HTTPStatus.NOT_FOUND, {"error": f"no route for {path}"})
            return None
        id = unquote(path[len(prefix) :])
        if not _ID.fullmatch(id):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": f"invalid secret id {id!r}"})
            return None
        return id

    def _send_json(self, status: HTTPStatus, body: object) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_empty(self, status: HTTPStatus) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()


def main() -> None:
    p = argparse.ArgumentParser(description="cortexgrid head secrets server")
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--env-file", type=Path, required=True)
    args = p.parse_args()
    server = SecretsServer(("0.0.0.0", args.port), EnvFileStore(args.env_file))
    server.serve_forever()


if __name__ == "__main__":
    main()
