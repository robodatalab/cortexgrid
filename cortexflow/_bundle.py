"""Dependency tracing for cortexflow code-bundling.

Purpose: given the source file that defines an entry function, collect
everything the Python interpreter would need to import and run that function on
a remote worker that starts from nothing -- no first-party packages installed,
no access to private package indexes.

The bundler starts at the entry file and follows the import graph the way the
interpreter resolves it (via sys.path), not by assuming a package sits directly
under the submitting project. Each reached module is classified:

  - ship as source: modules the worker cannot obtain on its own -- the
    submitter's own code and any first-party package installed from VCS, an
    editable checkout, or a local path. These are copied into the job bundle.
    A package is always shipped whole: shipping a partial package whose
    __init__ imports missing siblings would shadow a complete copy on the
    worker and fail at import time.
  - external (pip): modules available from a public index. Only their pinned
    versions travel with the job (see filter_pip_freeze); the worker installs
    them.
  - skipped: the standard library, and distributions baked into the worker
    image.

A faithful trace has to account for all of the following, or the job dies on
the worker with ModuleNotFoundError:

  - modules installed outside the project tree (site-packages, editable
    installs, src/ layouts, PYTHONPATH), resolved by real import lookup rather
    than by joining the dotted name onto the project directory.
  - transitive imports reached *through* an installed package, not just those
    directly under the project -- tracing must not dead-end at a module it
    cannot place under the project root.
  - relative imports (from . import x, from .core import y), which name real
    sibling files.
  - non-.py runtime files a package loads: compiled extensions (.so/.pyd) and
    package data.
  - import graphs that span more than one sys.path root (project source plus an
    installed package), which the staged bundle has to preserve.

Known blind spot: imports built dynamically (importlib.import_module on a
computed name, plugin registries) are invisible to static analysis.
"""

from __future__ import annotations

import ast
import contextlib
import importlib.metadata
import importlib.util
import inspect
import json
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator


def _module_package(file: Path) -> str:
    """Dotted package name `file` belongs to, for resolving its relative
    imports. For an __init__.py this is the package itself; for a regular module
    it is the module's dotted name minus the final component ('' at top level)."""
    dotted, _ = infer_module_path(file)
    if file.name == "__init__.py":
        return dotted
    return dotted.rpartition(".")[0]


def _import_targets(file: Path) -> list[str]:
    """Absolute dotted module names imported by `file`.

    Relative imports are resolved against the file's own package, so
    `from .core import x` in package `p` yields `p.core`. For `from pkg import
    name`, both `pkg` and `pkg.name` are returned: `name` may be a submodule to
    follow, or a plain attribute that simply will not resolve to a module.
    """
    tree = ast.parse(file.read_text())
    pkg = _module_package(file)
    targets: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                targets.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                base = node.module or ""
            else:
                # A relative import drops `level - 1` trailing components from
                # the current package, then appends the named module (if any).
                parts = pkg.split(".") if pkg else []
                base = ".".join(parts[: len(parts) - (node.level - 1)])
                if node.module:
                    base = f"{base}.{node.module}" if base else node.module
            if base:
                targets.append(base)
            for alias in node.names:
                targets.append(f"{base}.{alias.name}" if base else alias.name)
    return targets


_DEP_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+")

_CORTEXFLOW_DIR = Path(__file__).parent.resolve()


def _is_installed_package(path: Path) -> bool:
    """True if `path` lives in an installed-package location (a wheel install
    in a venv/system), as opposed to editable source checked out in a workspace.
    Installed distributions live under site-packages/dist-packages; editable or
    in-tree source does not."""
    return any(part in ("site-packages", "dist-packages") for part in path.parts)


def _canonicalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _resolve(name: str) -> Path | None:
    """The real source file `name` resolves to via the interpreter's own import
    lookup (sys.path), or None if `name` is not an importable Python module: a
    plain attribute, a namespace package, a builtin, or a compiled extension
    (an .so ships only as part of its whole installed package, never alone)."""
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, AttributeError, ValueError):
        return None
    if spec is None or not spec.origin or not spec.origin.endswith(".py"):
        return None
    return Path(spec.origin).resolve()


def _dist_info_for(site_packages: Path, top_level: str) -> Path | None:
    """The *.dist-info directory in `site_packages` whose distribution name
    canonicalizes to `top_level`. Matches the common case where the import name
    equals the distribution name (model_gateway, cortexflow); a genuine mismatch
    (PyYAML/yaml) just isn't found and the package is treated as external."""
    canon = _canonicalize(top_level)
    for child in site_packages.glob("*.dist-info"):
        dist = child.name[: -len(".dist-info")].rsplit("-", 1)[0]
        if _canonicalize(dist) == canon:
            return child
    return None


def _is_first_party_install(top_file: Path, top_level: str) -> bool:
    """True if the installed distribution providing `top_level` came from VCS or
    an editable/local checkout -- pip records that in direct_url.json. Such a
    package is not on a public index, so the worker cannot pip-install it and it
    must ship as source. A plain wheel has no direct_url.json and stays external."""
    site_packages = next(
        (p for p in top_file.parents if p.name in ("site-packages", "dist-packages")),
        None,
    )
    if site_packages is None:
        return False
    dist_info = _dist_info_for(site_packages, top_level)
    if dist_info is None:
        return False
    direct_url = dist_info / "direct_url.json"
    if not direct_url.is_file():
        return False
    try:
        data = json.loads(direct_url.read_text())
    except (OSError, ValueError):
        return False
    return "vcs_info" in data or bool(data.get("dir_info", {}).get("editable"))


def _classify_top(top_level: str) -> tuple[str, Path | None]:
    """Classify a top-level import name into how the worker must obtain it:

    'own'         -- loose source (the submitter's own tree); ship the traced
                     files. Returns the top-level package/module file.
    'first_party' -- a VCS/editable installed distribution; ship its whole
                     package directory. Returns its top-level package file.
    'external'    -- a public wheel, or unresolvable; leave it to pip.
    """
    top_file = _resolve(top_level)
    if top_file is None:
        return "external", None
    if not _is_installed_package(top_file):
        return "own", top_file
    if _is_first_party_install(top_file, top_level):
        return "first_party", top_file
    return "external", top_file


def infer_module_path(file: Path) -> tuple[str, Path]:
    """From a workspace .py file, walk up __init__.py chain to derive
    its dotted module name and the sys.path entry it lives under."""
    parts: list[str] = []
    if file.name != "__init__.py":
        parts.append(file.stem)
    cur = file.parent
    while (cur / "__init__.py").exists():
        parts.insert(0, cur.name)
        cur = cur.parent
    return ".".join(parts), cur


def _package_files(pkg_dir: Path) -> Iterator[Path]:
    """Every shippable file under an installed package directory, skipping
    bytecode caches (which are rebuilt on the worker)."""
    for f in pkg_dir.rglob("*"):
        if not f.is_file():
            continue
        if "__pycache__" in f.parts or f.suffix in (".pyc", ".pyo"):
            continue
        yield f.resolve()


def _collect(entry_file: Path, force_whole: list[Path]) -> tuple[set[Path], set[str]]:
    """Trace everything the interpreter needs to import and run the function
    defined in `entry_file`, resolving imports the way the interpreter does.

    Returns (files_to_ship, external_top_levels). Two kinds of work coexist:
    loose source is traced file by file (only what is reached ships), while a
    first-party installed package ships as its whole directory -- the unit pip
    would otherwise have delivered, so package data and dynamically imported
    submodules travel too. `force_whole` are package directories shipped
    unconditionally (the cortexflow runtime the worker always needs).
    external names are the public wheels the worker pip-installs.
    """
    ship: set[Path] = set()
    external: set[str] = set()
    handled_pkgs: set[Path] = set()
    file_queue: list[Path] = []
    pkg_queue: list[Path] = list(force_whole)
    classified: dict[str, tuple[str, Path | None]] = {}

    def classify(top: str) -> tuple[str, Path | None]:
        if top not in classified:
            classified[top] = _classify_top(top)
        return classified[top]

    def route(name: str) -> None:
        top = name.split(".")[0]
        if top in sys.stdlib_module_names:
            return
        kind, top_file = classify(top)
        if kind == "external":
            external.add(top)
        elif kind == "first_party" and top_file is not None:
            pkg_queue.append(top_file.parent)
        else:  # own source: ship the specific module this import names
            target = _resolve(name)
            if target is not None and not _is_installed_package(target):
                file_queue.append(target)

    entry_top = infer_module_path(entry_file)[0].split(".")[0]
    kind, top_file = _classify_top(entry_top)
    if kind == "first_party" and top_file is not None:
        pkg_queue.append(top_file.parent)
    else:
        file_queue.append(entry_file)

    while file_queue or pkg_queue:
        while pkg_queue:
            pkg_dir = pkg_queue.pop().resolve()
            if pkg_dir in handled_pkgs:
                continue
            handled_pkgs.add(pkg_dir)
            for f in _package_files(pkg_dir):
                ship.add(f)
            for py in pkg_dir.rglob("*.py"):
                if "__pycache__" in py.parts:
                    continue
                for name in _import_targets(py.resolve()):
                    route(name)
        if file_queue:
            file = file_queue.pop().resolve()
            if file in ship:
                continue
            ship.add(file)
            # Ship and trace the parent __init__.py chain: importing this module
            # executes them, so the modules they import are genuine runtime deps.
            _, root = infer_module_path(file)
            cur = file.parent
            while cur != root and (cur / "__init__.py").exists():
                file_queue.append((cur / "__init__.py").resolve())
                cur = cur.parent
            for name in _import_targets(file):
                route(name)
    return ship, external


def _ship_roots(files: set[Path]) -> set[Path]:
    """The sys.path roots the shipped files live under. A bundle may span more
    than one (the submitter's own source plus an installed first-party package);
    each file is staged relative to its own root so all of them stay importable."""
    return {infer_module_path(f)[1] for f in files}


def find_pyproject_for(file: Path) -> Path:
    """Walk up from `file` to find the nearest pyproject.toml."""
    cur = file.resolve().parent
    while cur != cur.parent:
        candidate = cur / "pyproject.toml"
        if candidate.is_file():
            return candidate
        cur = cur.parent
    raise FileNotFoundError(f"No pyproject.toml found above {file}")


@dataclass
class StagedBundle:
    """A staged on-disk copy of the files needed to ship an entry point."""

    roots: list[Path]
    staging_dir: Path
    external_deps: list[str]


@contextlib.contextmanager
def stage_bundle(entry: Callable[..., Any] | type) -> Iterator[StagedBundle]:
    """Bundle `entry`'s reachable files into a tempdir; clean up on exit.

    Yields a StagedBundle whose `staging_dir` holds only the files reachable
    from `entry`'s import graph, each placed at its import path so the whole
    directory can go on the worker's sys.path. `entry` is the file-defining
    symbol Ray will load (a function for `cortexflow.remote`, a class for
    `cortexflow.deploy_model`).
    """
    roots, files, external_deps = bundle_for_entry(entry)
    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp)
        for f in files:
            _, root = infer_module_path(f)
            dst = staging / f.relative_to(root)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dst)
        yield StagedBundle(
            roots=sorted(roots),
            staging_dir=staging,
            external_deps=sorted(external_deps),
        )


def bundle_for_entry(
    entry: Callable[..., Any] | type,
) -> tuple[set[Path], set[Path], set[str]]:
    """Compute (ship_roots, files_to_ship, external_imports) for shipping `entry`.

    The pyproject.toml above the entry's source file only marks the project
    root. Its [project].dependencies is intentionally ignored: imports are
    resolved from the running interpreter, first-party code ships as source, and
    pinned versions of external wheels come from pip freeze. This favours the
    live local version of any first-party library over a published one.
    """
    entry_file = Path(inspect.getfile(entry)).resolve()
    workspace_root = find_pyproject_for(entry_file).parent
    force_whole: list[Path] = []
    # The worker's job driver imports cortexflow even when the entry does not,
    # so ship the live cortexflow whenever it lives within this submission's
    # project tree (an in-project .venv, or a monorepo checkout).
    if _CORTEXFLOW_DIR.is_relative_to(workspace_root):
        force_whole.append(_CORTEXFLOW_DIR)
    files, external = _collect(entry_file, force_whole)
    return _ship_roots(files), files, external


# Distributions baked into the ray image's system site-packages. Runtime envs
# created by Ray use --system-site-packages, so these are visible to the
# virtualenv without re-installing; emitting them in runtime_env.pip would
# force a multi-minute re-install of the CUDA torch wheel on every deploy.
_BAKED_INTO_IMAGE: set[str] = {"torch"}


def filter_pip_freeze(freeze_output: str, keep_top_levels: set[str]) -> str:
    """Drop pip freeze lines whose distribution doesn't cover any top-level
    name in `keep_top_levels`, plus any distribution baked into the ray image.
    Lines that don't parse as a dist are dropped.

    Distribution -> top-level mapping comes from importlib.metadata; for
    editable installs that aren't in that map, the canonical dist name itself
    is treated as the top-level (s/-/_/), which holds for most packages.
    """
    dist_to_tops: dict[str, set[str]] = {}
    for top, dists in importlib.metadata.packages_distributions().items():
        for d in dists:
            dist_to_tops.setdefault(_canonicalize(d), set()).add(top)
    kept: list[str] = []
    for line in freeze_output.splitlines():
        match = _DEP_NAME_RE.match(line.strip())
        if not match:
            continue
        dist_canon = _canonicalize(match.group(0))
        if dist_canon in _BAKED_INTO_IMAGE:
            continue
        tops = dist_to_tops.get(dist_canon, {dist_canon.replace("-", "_")})
        if tops & keep_top_levels:
            kept.append(line)
    return "\n".join(kept)
