"""Code bundler: the files needed to run a piece of Python on another machine.

`bundle(seed)` describes what is needed to run the module at `seed`: the local
files it reaches -- following its import graph and each package's __init__
chain, resolving imports the way the interpreter does -- and the third-party
distributions those files import. A file is local unless it lives in an installed
package location (site-packages / dist-packages). An import that resolves into
one is recorded as the distribution that installed the file, at its installed
version, and is not followed: installing that distribution brings its own
dependencies. The standard library is excluded (it ships with the interpreter).
Bundles of several seeds combine with `BundleDesc.merge`.

`stage(files, dest)` lays a bundle out under `dest` at each file's import path,
so `dest` on sys.path (e.g. a Ray working_dir) makes every module importable.

`BundleDesc.pip_requirements(worker_provides())` pins the third-party
distributions the Ray worker image does not already have, for a Ray `pip`
runtime_env to install on the worker.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
import functools
import importlib.machinery
import importlib.metadata
import importlib.util
import os
from pathlib import Path
import shutil
import sys

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


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

    def pip_requirements(
        self, provided: frozenset[ThirdPartyDependencyName]
    ) -> list[str]:
        """The third-party distributions, pinned (`name==version`) and sorted,
        minus the `provided` ones."""
        return sorted(
            f"{name}=={version}"
            for name, version in self.tp_deps.items()
            if name not in provided
        )


class UnownedDependencyError(LookupError):
    """An import resolved into an installed package location, but no installed
    distribution owns the file, so it can be neither shipped nor installed."""


def bundle(seed: Path) -> BundleDesc:
    """What is needed to run the module at `seed`: its local files, each at its
    real path, and the third-party distributions they import, keyed by
    canonical name, each at its installed version.

    Raises UnownedDependencyError for an import that resolves into an installed
    package location no distribution owns."""
    seed = seed.resolve()
    files: set[Path] = set()
    tp_deps: dict[ThirdPartyDependencyName, ThirdPartyDependencyVersion] = {}
    visited: set[Path] = set()
    queue: list[Path] = [seed]
    while queue:
        file = queue.pop()
        if file in visited:
            continue
        visited.add(file)
        if not _is_local(file):
            dist = _owning_distribution(file)
            tp_deps[canonicalize_name(dist.metadata["Name"])] = dist.version
            continue
        files.add(file)
        queue.extend(_init_chain(file))  # importing a module runs its __init__ chain
        if file.suffix == ".py":
            for name in _imports(file):
                dep = _module_file(name)
                if dep is not None:
                    queue.append(dep)
    return BundleDesc(local_files=files, tp_deps=tp_deps)


def stage(files: set[Path], dest: Path) -> None:
    """Copy `files` under `dest`, each at its import path (relative to the
    sys.path entry it lives under), so `dest` on sys.path imports them all."""
    for file in files:
        target = dest / file.relative_to(_sys_path_root(file))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file, target)


# What the Ray worker image pip-installs, as the Dockerfile spells it. They and
# their dependency trees are on the worker already, so they are never installed
# there again -- a second copy in the job's virtualenv would shadow the image's.
#
# KEEP IN SYNC with k8s/docker/ray/Dockerfile, by hand: every package it
# pip-installs must be listed here. A package missing here gets installed a
# second time on the worker; one listed here but no longer in the image is
# never installed at all.
_WORKER_BAKED = (
    "ray[default,serve]",
    "smart_open[s3]",
    "mlflow",
    "python-dotenv",
    "psutil",
    "nvidia-cublas-cu12",
    "nvidia-cudnn-cu12",
    "nvidia-cuda-nvrtc-cu12",
    "nvidia-cuda-runtime-cu12",
    "nvidia-cuda-cupti-cu12",
    "nvidia-cufft-cu12",
    "nvidia-curand-cu12",
    "nvidia-cusolver-cu12",
    "nvidia-cusparse-cu12",
    "nvidia-cusparselt-cu12",
    "nvidia-nccl-cu12",
    "nvidia-nvshmem-cu12",
    "nvidia-nvtx-cu12",
    "nvidia-nvjitlink-cu12",
    "nvidia-cufile-cu12",
    "cuda-bindings",
    "triton",
    "torch",
    "filelock",
    "typing-extensions",
    "sympy",
    "networkx",
    "jinja2",
    "fsspec",
)


@functools.lru_cache(maxsize=1)
def worker_provides() -> frozenset[ThirdPartyDependencyName]:
    """Every distribution the Ray worker image already provides:
    `distribution_closure(_WORKER_BAKED)`."""
    return distribution_closure(_WORKER_BAKED)


def distribution_closure(
    requirements: Iterable[str],
) -> frozenset[ThirdPartyDependencyName]:
    """Canonical names of the distributions `requirements` name plus everything
    they depend on, transitively, as this environment's installed metadata
    declares it -- extras followed where requested, environment markers
    evaluated here. A requirement not installed here contributes only its own
    name: its dependencies are unknown."""
    names: set[ThirdPartyDependencyName] = set()
    seen: set[tuple[str, frozenset[str]]] = set()
    queue = [Requirement(spec) for spec in requirements]
    while queue:
        requirement = queue.pop()
        name = canonicalize_name(requirement.name)
        key = (name, frozenset(requirement.extras))
        if key in seen:
            continue
        seen.add(key)
        names.add(name)
        try:
            dist = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            continue
        extras = requirement.extras or {""}
        for spec in dist.requires or ():
            dependency = Requirement(spec)
            if dependency.marker is None or any(
                dependency.marker.evaluate({"extra": extra}) for extra in extras
            ):
                queue.append(dependency)
    return frozenset(names)


def _owning_distribution(file: Path) -> importlib.metadata.Distribution:
    """The installed distribution whose file list (RECORD) contains `file`.
    Rebuilds the index once on a miss, in case something was installed since it
    was built."""
    path = tuple(sys.path)
    dist = _distribution_index(path).get(file)
    if dist is None:
        _distribution_index.cache_clear()
        dist = _distribution_index(path).get(file)
    if dist is None:
        raise UnownedDependencyError(
            f"{file} is imported from an installed package location, but no "
            "installed distribution lists it among its files, so it can be "
            "neither shipped nor pip-installed on the worker. Install it with "
            "pip or uv so it carries distribution metadata."
        )
    return dist


@functools.lru_cache(maxsize=1)
def _distribution_index(
    path: tuple[str, ...],
) -> dict[Path, importlib.metadata.Distribution]:
    """Every file installed by a distribution found on `path` (sys.path, which
    the cache is keyed on), mapped to that distribution. Each distribution's
    root is resolved once; its files are joined onto it lexically, which keeps
    this fast for environments with tens of thousands of files."""
    index: dict[Path, importlib.metadata.Distribution] = {}
    for dist in importlib.metadata.distributions(path=list(path)):
        root = Path(dist.locate_file("")).resolve()
        for file in dist.files or ():
            index[Path(os.path.normpath(root / file))] = dist
    return index


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
