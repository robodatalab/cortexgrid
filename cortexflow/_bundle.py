"""Code bundler: the files needed to run a piece of Python on another machine.

`bundle(seed)` returns every file the interpreter loads to run the module at
`seed` -- following its import graph and each package's __init__ chain, resolving
imports the way the interpreter does. The standard library is excluded (it ships
with the interpreter). To drop dependencies the target already has, subtract
their bundles:

    ship = bundle(my_code) - bundle(Path(torch.__file__))

`stage(files, dest)` lays a bundle out under `dest` at each file's import path,
so `dest` on sys.path (e.g. a Ray working_dir) makes every module importable.
"""

from __future__ import annotations

import ast
import functools
import importlib.util
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path


def bundle(seed: Path) -> set[Path]:
    """Every file needed to run the module at `seed`, each at its real path."""
    seed = seed.resolve()
    files: set[Path] = set()
    queue: list[Path] = [seed]
    while queue:
        file = queue.pop()
        if file in files:
            continue
        files.add(file)
        queue.extend(_init_chain(file))  # importing a module runs its __init__ chain
        if file.suffix == ".py":
            for name in _imports(file):
                dep = _module_file(name)
                if dep is not None:
                    queue.append(dep)
    return files


def stage(files: set[Path], dest: Path) -> None:
    """Copy `files` under `dest`, each at its import path (relative to the
    sys.path entry it lives under), so `dest` on sys.path imports them all."""
    for file in files:
        target = dest / file.relative_to(_sys_path_root(file))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file, target)


# Distributions already present in the Ray worker image (k8s/docker/ray/Dockerfile).
# They and their whole dependency trees -- torch's CUDA stack, sympy, numpy, ray,
# mlflow, ... -- are on the worker already, so a caller subtracts them from a
# bundle rather than shipping them again.
_WORKER_BAKED = ("ray", "mlflow", "torch", "smart_open", "dotenv", "psutil")


@functools.lru_cache(maxsize=1)
def worker_provides() -> frozenset[Path]:
    """Every file the Ray worker image already provides. Subtract from a bundle
    before shipping: `bundle(entry) - worker_provides()`."""
    provided: set[Path] = set()
    for name in _WORKER_BAKED:
        origin = _module_file(name)
        if origin is not None:
            provided |= bundle(origin)
    return frozenset(provided)


def _module_file(name: str) -> Path | None:
    """The file the interpreter would load for import `name`; None for the
    standard library, builtins, and namespace packages."""
    if name.split(".")[0] in sys.stdlib_module_names:
        return None
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, AttributeError, ValueError):
        return None
    if spec is None or spec.origin in (None, "built-in", "frozen"):
        return None
    return Path(spec.origin).resolve()


def _imports(file: Path) -> Iterator[str]:
    """Absolute module names imported by a .py file, relative imports resolved
    against the file's own package."""
    package = _package(file)
    for node in ast.walk(ast.parse(file.read_text())):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                try:
                    base = importlib.util.resolve_name(
                        "." * node.level + (node.module or ""), package
                    )
                except (ImportError, ValueError):
                    continue
            else:
                base = node.module
            if base:
                yield base
                for alias in node.names:
                    yield f"{base}.{alias.name}"


def _init_chain(file: Path) -> Iterator[Path]:
    """The package __init__.py files above `file`, up to its sys.path root."""
    directory = file.parent
    while (directory / "__init__.py").exists():
        yield (directory / "__init__.py").resolve()
        directory = directory.parent


def _package(file: Path) -> str:
    """Dotted name of the package `file` lives in ('' at the sys.path root)."""
    parts: list[str] = []
    directory = file.parent
    while (directory / "__init__.py").exists():
        parts.insert(0, directory.name)
        directory = directory.parent
    return ".".join(parts)


def _sys_path_root(file: Path) -> Path:
    """The sys.path entry `file` is imported from: its first ancestor directory
    without an __init__.py."""
    directory = file.parent
    while (directory / "__init__.py").exists():
        directory = directory.parent
    return directory
