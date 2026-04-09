"""Read pyproject.toml + uv.sources to build a Ray runtime_env automatically."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]


DEFAULT_EXCLUDES = [
    ".venv/",
    ".git/",
    "__pycache__/",
    "*.pyc",
    ".mypy_cache/",
    ".pytest_cache/",
    ".ruff_cache/",
    "node_modules/",
]


def find_pyproject(start: Path | None = None) -> Path:
    """Walk up from start (default: cwd) to find pyproject.toml."""
    current = start or Path.cwd()
    for parent in [current, *current.parents]:
        candidate = parent / "pyproject.toml"
        if candidate.exists():
            return candidate
    raise FileNotFoundError("No pyproject.toml found in any parent directory")


def read_pyproject(path: Path) -> dict[str, Any]:
    with open(path, "rb") as f:
        return tomllib.load(f)


def _source_to_pip(name: str, source: dict[str, str]) -> str:
    """Convert a [tool.uv.sources] entry to a pip install specifier."""
    if "git" in source:
        url = source["git"]
        spec = f"{name} @ git+{url}"
        if "subdirectory" in source:
            spec += f"#subdirectory={source['subdirectory']}"
        return spec
    if "path" in source:
        return f"{name} @ file://{os.path.abspath(source['path'])}"
    return name


def build_runtime_env(
    extra_excludes: list[str] | None = None,
) -> dict[str, Any]:
    """Build a Ray runtime_env from the current project's pyproject.toml.

    Reads [project.dependencies] and [tool.uv.sources] to construct pip
    install specs. Sets working_dir to the project root and applies
    standard excludes.
    """
    pyproject_path = find_pyproject()
    project_root = str(pyproject_path.parent)
    data = read_pyproject(pyproject_path)

    project = data.get("project", {})
    deps: list[str] = project.get("dependencies", [])
    uv_sources: dict[str, dict] = data.get("tool", {}).get("uv", {}).get("sources", {})

    # Build pip list: for deps with a uv source, use the git/path URL.
    # For deps without a source, pass them through as-is.
    pip_deps: list[str] = []
    for dep in deps:
        # Extract bare package name (strip version specifiers)
        name = re.split(r"[>=<!\[;]", dep)[0].strip()
        normalized = name.lower().replace("-", "_")

        source = None
        for source_name, source_val in uv_sources.items():
            if source_name.lower().replace("-", "_") == normalized:
                source = source_val
                break

        if source:
            pip_deps.append(_source_to_pip(name, source))
        else:
            pip_deps.append(dep)

    excludes = DEFAULT_EXCLUDES.copy()
    if extra_excludes:
        excludes.extend(extra_excludes)

    return {
        "working_dir": project_root,
        "excludes": excludes,
        "pip": pip_deps,
    }
