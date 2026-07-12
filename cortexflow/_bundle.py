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
import inspect
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator


def parse_imports(source_file: Path) -> list[str]:
    """Return absolute import targets from a Python source file.

    `import a.b` -> 'a.b'; `from a.b import c` -> 'a.b'.
    Relative imports (`from . import x`) are skipped: they don't cross
    package boundaries and add no new files to ship.
    """
    tree = ast.parse(source_file.read_text())
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                imports.append(node.module)
    return imports


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


def _find_in_workspace(import_name: str, workspace_root: Path) -> Path | None:
    """Probe the workspace filesystem for the .py file an import would resolve to."""
    parts = import_name.split(".")
    base = workspace_root.joinpath(*parts)
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate.resolve()
    return None


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


def collect_workspace(
    starts: list[Path], workspace_root: Path
) -> tuple[set[Path], set[str]]:
    """BFS from each path in `starts` through imports. Returns (files_to_ship, external).

    files_to_ship: workspace .py files reached transitively, plus each file's
    __init__.py chain back to its sys.path root.
    external: top-level names of imports that aren't stdlib and don't resolve
    to a workspace file. Their pinned versions come from pip freeze.
    """
    visited: set[Path] = set()
    external: set[str] = set()
    queue: list[Path] = [s.resolve() for s in starts]
    while queue:
        file = queue.pop(0)
        if file in visited:
            continue
        visited.add(file)
        # Ship AND trace the parent-package __init__.py chain: importing any
        # submodule executes these, so the modules they import are real deps.
        _, root = infer_module_path(file)
        cur = file.parent
        while cur != root and (cur / "__init__.py").exists():
            queue.append((cur / "__init__.py").resolve())
            cur = cur.parent
        for name in parse_imports(file):
            top = name.split(".")[0]
            if top in sys.stdlib_module_names:
                continue
            ws_file = _find_in_workspace(name, workspace_root)
            if ws_file is not None:
                queue.append(ws_file)
            else:
                external.add(top)
    return visited, external


def find_ship_root(files: set[Path]) -> Path:
    """All shipped files must agree on a single sys.path root. Returns it."""
    roots = {infer_module_path(f)[1] for f in files}
    if len(roots) != 1:
        raise RuntimeError(
            f"Workspace files resolve to inconsistent sys.path roots: {roots}"
        )
    return roots.pop()


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

    ship_root: Path
    staging_dir: Path
    external_deps: list[str]


@contextlib.contextmanager
def stage_bundle(entry: Callable[..., Any] | type) -> Iterator[StagedBundle]:
    """Bundle `entry`'s reachable workspace files into a tempdir; clean up on exit.

    Yields a StagedBundle whose `staging_dir` mirrors the ship root and
    contains only the files reachable from `entry`'s import graph. `entry`
    is the file-defining symbol Ray will load (a function for `cortexflow.remote`,
    a class for `cortexflow.deploy_model`).
    """
    ship_root, files, external_deps = bundle_for_entry(entry)
    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp)
        for f in files:
            rel = f.relative_to(ship_root)
            dst = staging / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dst)
        yield StagedBundle(
            ship_root=ship_root,
            staging_dir=staging,
            external_deps=sorted(external_deps),
        )


def bundle_for_entry(
    entry: Callable[..., Any] | type,
) -> tuple[Path, set[Path], set[str]]:
    """Compute (ship_root, files_to_ship, external_imports) for shipping `entry`.

    The pyproject.toml above the entry's source file only marks the workspace-root
    candidate. Its [project].dependencies is intentionally ignored: external
    imports are detected from the code, and pinned versions come from pip
    freeze. This favours the live local version of any in-tree library over
    a published one declared in pyproject.
    """
    entry_file = Path(inspect.getfile(entry)).resolve()
    pyproject = find_pyproject_for(entry_file)
    workspace_root = pyproject.parent
    seeds = [entry_file]
    # Ship the live cortexflow source only when it is genuinely in-tree (a
    # monorepo checkout / editable install). A wheel installed into a .venv that
    # happens to sit *under* the consumer's workspace is also is_relative_to the
    # root, but must be treated as an external dep (pip-installed on the cluster),
    # not globbed in — doing so mixes sys.path roots and breaks find_ship_root.
    if _CORTEXFLOW_DIR.is_relative_to(workspace_root) and not _is_installed_package(
        _CORTEXFLOW_DIR
    ):
        seeds.extend(_CORTEXFLOW_DIR.rglob("*.py"))
    files, external = collect_workspace(seeds, workspace_root)
    ship_root = find_ship_root(files)
    return ship_root, files, external


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
