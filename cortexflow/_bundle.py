"""Static analysis for cortexflow.remote() job bundling.

Walks the import graph from a function's source file. Imports that resolve to
files inside the workspace are shipped; imports that don't are recorded as
external (their pinned versions come from pip freeze, not from pyproject).
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
        for name in parse_imports(file):
            top = name.split(".")[0]
            if top in sys.stdlib_module_names:
                continue
            ws_file = _find_in_workspace(name, workspace_root)
            if ws_file is not None:
                queue.append(ws_file)
            else:
                external.add(top)
    chain: set[Path] = set()
    for file in visited:
        _, root = infer_module_path(file)
        cur = file.parent
        while cur != root and (cur / "__init__.py").exists():
            chain.add((cur / "__init__.py").resolve())
            cur = cur.parent
    return visited | chain, external


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
    """A staged on-disk copy of the files needed to ship a function."""

    ship_root: Path
    staging_dir: Path
    external_deps: list[str]


@contextlib.contextmanager
def stage_bundle(fn: Callable[..., Any]) -> Iterator[StagedBundle]:
    """Bundle `fn`'s reachable workspace files into a tempdir; clean up on exit.

    Yields a StagedBundle whose `staging_dir` mirrors the ship root and
    contains only the files reachable from `fn`'s import graph.
    """
    ship_root, files, external_deps = bundle_for_function(fn)
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


def bundle_for_function(
    fn: Callable[..., Any],
) -> tuple[Path, set[Path], set[str]]:
    """Compute (ship_root, files_to_ship, external_imports) for shipping `fn`.

    The pyproject.toml above the entry function only marks the workspace-root
    candidate. Its [project].dependencies is intentionally ignored: external
    imports are detected from the code, and pinned versions come from pip
    freeze. This favours the live local version of any in-tree library over
    a published one declared in pyproject.
    """
    fn_file = Path(inspect.getfile(fn)).resolve()
    pyproject = find_pyproject_for(fn_file)
    workspace_root = pyproject.parent
    seeds = [fn_file]
    if _CORTEXFLOW_DIR.is_relative_to(workspace_root):
        seeds.extend(_CORTEXFLOW_DIR.rglob("*.py"))
    files, external = collect_workspace(seeds, workspace_root)
    ship_root = find_ship_root(files)
    return ship_root, files, external


def filter_pip_freeze(freeze_output: str, keep_top_levels: set[str]) -> str:
    """Drop pip freeze lines whose distribution doesn't cover any top-level
    name in `keep_top_levels`. Lines that don't parse as a dist are dropped.

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
        tops = dist_to_tops.get(dist_canon, {dist_canon.replace("-", "_")})
        if tops & keep_top_levels:
            kept.append(line)
    return "\n".join(kept)
