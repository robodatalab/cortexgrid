"""Code bundler: the files needed to run a piece of Python on another machine.

`bundle(seed)` describes what is needed to run the module at `seed`: the local
files it reaches -- following its import graph and each package's __init__
chain, resolving imports the way the interpreter does -- and the third-party
dependencies those files import. A file is local unless it lives in an installed
package location (site-packages / dist-packages); imports that resolve into one
are not followed. The standard library is excluded (it ships with the
interpreter). Bundles of several seeds combine with `BundleDesc.merge`.

`stage(files, dest)` lays a bundle out under `dest` at each file's import path,
so `dest` on sys.path (e.g. a Ray working_dir) makes every module importable.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass
import functools
import importlib.machinery
import importlib.util
from pathlib import Path
import shutil
import sys


ThirdPartyDependencyName = str
ThirdPartyDependencyVersion = str

@dataclass
class BundleDesc:
    local_files: set[Path]
    tp_deps: dict[ThirdPartyDependencyName, ThirdPartyDependencyVersion]

    def merge(self, other: BundleDesc) -> BundleDesc:
        return BundleDesc(
            local_files=self.local_files.union(other.local_files),
            tp_deps={**self.tp_deps, **other.tp_deps},
        )


def bundle(seed: Path) -> BundleDesc:
    """What is needed to run the module at `seed`: its local files, each at its
    real path. Third-party dependency detection is not implemented yet, so
    `tp_deps` is always empty."""
    seed = seed.resolve()
    files: set[Path] = set()
    queue: list[Path] = [seed]
    while queue:
        file = queue.pop()
        if file in files or not _is_local(file):
            continue
        files.add(file)
        queue.extend(_init_chain(file))  # importing a module runs its __init__ chain
        if file.suffix == ".py":
            for name in _imports(file):
                dep = _module_file(name)
                if dep is not None:
                    queue.append(dep)
    return BundleDesc(local_files=files, tp_deps={})


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
    before shipping: `bundle(entry).local_files - worker_provides()`."""
    provided: set[Path] = set()
    for name in _WORKER_BAKED:
        origin = _module_file(name)
        if origin is not None:
            provided |= bundle(origin).local_files
    return frozenset(provided)


def _is_local(file: Path) -> bool:
    """True unless `file` lives in an installed-package location."""
    return not any(part in ("site-packages", "dist-packages") for part in file.parts)


def _module_file(name: str) -> Path | None:
    """The file the interpreter would load for import `name`; None for the
    standard library, builtins, namespace packages, and names that are not
    modules (e.g. a function in `from pkg import function`)."""
    if name.split(".")[0] in sys.stdlib_module_names:
        return None
    spec = _find_spec(name)
    if spec is None or spec.origin in (None, "built-in", "frozen"):
        return None
    return Path(spec.origin).resolve()


def _find_spec(name: str) -> importlib.machinery.ModuleSpec | None:
    """The spec the import system would find for `name`, without importing any
    of its parent packages (importlib.util.find_spec imports them).

    Mirrors the import system's own lookup: each meta path finder is asked for
    `name`, with the parent's submodule_search_locations as the import path for
    a submodule. https://docs.python.org/3.11/reference/import.html#the-meta-path
    """
    parent, _, child = name.rpartition(".")
    path: list[str] | None = None
    if parent:
        parent_spec = _find_spec(parent)
        if parent_spec is None or parent_spec.submodule_search_locations is None:
            return None
        path = list(parent_spec.submodule_search_locations)
        portions = _namespace_portions(child, path)
        if portions:
            # The finders cannot build a nested namespace package's spec without
            # its parent in sys.modules, so build it here.
            spec = importlib.machinery.ModuleSpec(name, None, is_package=True)
            spec.submodule_search_locations = portions
            return spec
    for finder in sys.meta_path:
        spec = finder.find_spec(name, path)
        if spec is not None:
            return spec
    return None


def _namespace_portions(child: str, path: list[str]) -> list[str]:
    """The directories that make `child` a namespace package under `path`: each
    `<entry>/<child>` directory, provided no entry holds `child` as a module or
    regular package. Empty if `child` is not a namespace package.
    https://peps.python.org/pep-0420/#specification
    """
    suffixes = importlib.machinery.all_suffixes()
    portions: list[str] = []
    for entry in map(Path, path):
        directory = entry / child
        if any(
            (entry / f"{child}{suffix}").is_file()
            or (directory / f"__init__{suffix}").is_file()
            for suffix in suffixes
        ):
            return []
        if directory.is_dir():
            portions.append(str(directory))
    return portions


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
