"""Bundler tracing tests.

The bundler's job: starting from the file that defines an entry function,
collect every file the Python interpreter needs to import and run that function
on a bare worker (no first-party packages installed, no private index).

Each Case lays out a submitter project on disk. `project_files` are the
submitter's own source (under the project root, which holds pyproject.toml).
`installed_files` are packages "installed" into a simulated
`.venv/site-packages` under that root -- this mirrors how a dependency such as
model-gateway actually reaches the entry point: git-installed into
site-packages, not sitting in the submitter's own tree.

Behavior the cases pin down:
  - Submitter's own source ships only the files the trace reaches; unimported
    files stay out.
  - An installed first-party package (VCS or editable install, identified by
    its `direct_url.json`) ships as its whole directory -- that is the unit pip
    would otherwise have delivered, so it also captures package data, compiled
    extensions, and dynamically imported submodules.
  - An installed public wheel (plain dist-info, no `direct_url.json`) is left to
    pip: recorded as external, never shipped.

expected_shipped is every shipped file expressed as its import path (relative to
its own sys.path root), so a bundle that spans more than one root is still
expressible. expected_external is the top-level names left to pip.
"""

from __future__ import annotations

import importlib
import shutil
import sys
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from parameterized import parameterized  # type: ignore

from cortexflow._bundle import bundle_for_entry, filter_pip_freeze, infer_module_path


PYPROJECT = '[project]\nname="app"\nversion="0"\n'
SITE = ".venv/site-packages"


def _vcs_dist_info(name: str) -> dict[str, str]:
    """dist-info pip writes for `pip install git+https://...`. Its presence is
    how a first-party install is told apart from a public wheel."""
    return {
        f"{name}-1.0.dist-info/METADATA": (
            f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n"
        ),
        f"{name}-1.0.dist-info/direct_url.json": (
            '{"url": "https://github.com/org/' + name + '.git", '
            '"vcs_info": {"vcs": "git", "commit_id": "abc123"}}\n'
        ),
    }


def _wheel_dist_info(name: str) -> dict[str, str]:
    """dist-info for a normal PyPI wheel install: no direct_url.json."""
    return {
        f"{name}-2.0.dist-info/METADATA": (
            f"Metadata-Version: 2.1\nName: {name}\nVersion: 2.0\n"
        ),
    }


@dataclass
class Case:
    name: str
    # Submitter's own source, under the project root. Must include pyproject.toml.
    project_files: dict[str, str]
    # "<dotted.module>:<function>" -- the entry passed to the bundler.
    entry: str
    # Every shipped file as its import path (relpath from its own sys.path root).
    expected_shipped: set[str]
    # Top-level names left to the worker to pip-install.
    expected_external: set[str]
    # Packages "installed" under .venv/site-packages (dependency, not own source).
    installed_files: dict[str, str] = field(default_factory=dict)
    # Relpath under the project root that goes on sys.path (the source root).
    source_root: str = ""


CASES: list[Case] = [
    # ---- Submitter source, traced (works today) -----------------------------
    Case(
        name="single_module_top_level",
        project_files={"pyproject.toml": PYPROJECT, "main.py": "import json\ndef fn(): pass\n"},
        entry="main:fn",
        expected_shipped={"main.py"},
        expected_external=set(),
    ),
    Case(
        name="single_package_stdlib_only",
        project_files={
            "pyproject.toml": PYPROJECT,
            "pkg/__init__.py": "",
            "pkg/main.py": "import json\ndef fn(): pass\n",
        },
        entry="pkg.main:fn",
        expected_shipped={"pkg/__init__.py", "pkg/main.py"},
        expected_external=set(),
    ),
    Case(
        name="package_with_sibling_helper",
        project_files={
            "pyproject.toml": PYPROJECT,
            "pkg/__init__.py": "",
            "pkg/helper.py": "def x(): return 1\n",
            "pkg/main.py": "from pkg.helper import x\ndef fn(): return x()\n",
        },
        entry="pkg.main:fn",
        expected_shipped={"pkg/__init__.py", "pkg/helper.py", "pkg/main.py"},
        expected_external=set(),
    ),
    Case(
        name="deeply_nested_package",
        project_files={
            "pyproject.toml": PYPROJECT,
            "a/__init__.py": "",
            "a/b/__init__.py": "",
            "a/b/c/__init__.py": "",
            "a/b/c/main.py": "import json\ndef fn(): pass\n",
        },
        entry="a.b.c.main:fn",
        expected_shipped={
            "a/__init__.py",
            "a/b/__init__.py",
            "a/b/c/__init__.py",
            "a/b/c/main.py",
        },
        expected_external=set(),
    ),
    Case(
        name="cross_subpackage_import",
        project_files={
            "pyproject.toml": PYPROJECT,
            "a/__init__.py": "",
            "a/util.py": "def y(): return 2\n",
            "a/b/__init__.py": "",
            "a/b/main.py": "from a.util import y\ndef fn(): return y()\n",
        },
        entry="a.b.main:fn",
        expected_shipped={"a/__init__.py", "a/util.py", "a/b/__init__.py", "a/b/main.py"},
        expected_external=set(),
    ),
    Case(
        name="unimported_files_in_own_source_are_not_shipped",
        project_files={
            "pyproject.toml": PYPROJECT,
            "pkg/__init__.py": "",
            "pkg/main.py": "import json\ndef fn(): pass\n",
            "pkg/unused_sibling.py": "raise RuntimeError('should not import')\n",
            "pkg/also_unused/__init__.py": "",
            "pkg/also_unused/x.py": "x = 1\n",
            "unrelated_top_level.py": "y = 2\n",
        },
        entry="pkg.main:fn",
        expected_shipped={"pkg/__init__.py", "pkg/main.py"},
        expected_external=set(),
    ),
    Case(
        name="ship_root_is_first_dir_without_init",
        project_files={
            "outer/pyproject.toml": PYPROJECT,
            "outer/inner/__init__.py": "",
            "outer/inner/main.py": "def fn(): pass\n",
        },
        entry="inner.main:fn",
        expected_shipped={"inner/__init__.py", "inner/main.py"},
        expected_external=set(),
        source_root="outer",
    ),
    Case(
        name="diamond_import_visits_leaf_only_once",
        project_files={
            "pyproject.toml": PYPROJECT,
            "pkg/__init__.py": "",
            "pkg/leaf.py": "def z(): return 3\n",
            "pkg/left.py": "from pkg.leaf import z\n",
            "pkg/right.py": "from pkg.leaf import z\n",
            "pkg/main.py": (
                "from pkg.left import z\nfrom pkg.right import z as z2\ndef fn(): pass\n"
            ),
        },
        entry="pkg.main:fn",
        expected_shipped={
            "pkg/__init__.py",
            "pkg/leaf.py",
            "pkg/left.py",
            "pkg/right.py",
            "pkg/main.py",
        },
        expected_external=set(),
    ),
    Case(
        name="in_workspace_first_party_lib_shipped_not_pinned",
        project_files={
            "pyproject.toml": PYPROJECT,
            "mylib/__init__.py": "VERSION = '0'\n",
            "app/__init__.py": "",
            "app/main.py": "import mylib\ndef fn(): return mylib\n",
        },
        entry="app.main:fn",
        expected_shipped={"mylib/__init__.py", "app/__init__.py", "app/main.py"},
        expected_external=set(),
    ),
    # ---- Third-party, left to pip (works today) -----------------------------
    Case(
        name="third_party_import_recognized_as_external",
        project_files={
            "pyproject.toml": PYPROJECT,
            "pkg/__init__.py": "",
            "pkg/main.py": "import cloudpickle\ndef fn(): pass\n",
        },
        entry="pkg.main:fn",
        expected_shipped={"pkg/__init__.py", "pkg/main.py"},
        expected_external={"cloudpickle"},
    ),
    Case(
        name="third_party_used_only_via_workspace_helper",
        project_files={
            "pyproject.toml": PYPROJECT,
            "pkg/__init__.py": "",
            "pkg/helper.py": "import cloudpickle\ndef ser(x): return cloudpickle.dumps(x)\n",
            "pkg/main.py": "from pkg.helper import ser\ndef fn(): pass\n",
        },
        entry="pkg.main:fn",
        expected_shipped={"pkg/__init__.py", "pkg/helper.py", "pkg/main.py"},
        expected_external={"cloudpickle"},
    ),
    Case(
        name="multiple_third_party_deps",
        project_files={
            "pyproject.toml": PYPROJECT,
            "pkg/__init__.py": "",
            "pkg/main.py": "import cloudpickle\nfrom tqdm import tqdm\ndef fn(): pass\n",
        },
        entry="pkg.main:fn",
        expected_shipped={"pkg/__init__.py", "pkg/main.py"},
        expected_external={"cloudpickle", "tqdm"},
    ),
    Case(
        # A public wheel installed in site-packages must stay external -- the
        # fix must NOT start shipping PyPI packages as source.
        name="installed_public_wheel_stays_external",
        project_files={
            "pyproject.toml": PYPROJECT,
            "app/__init__.py": "",
            "app/main.py": "import widget\ndef fn(): pass\n",
        },
        installed_files={
            "widget/__init__.py": "",
            "widget/w.py": "def go(): pass\n",
            **_wheel_dist_info("widget"),
        },
        entry="app.main:fn",
        expected_shipped={"app/__init__.py", "app/main.py"},
        expected_external={"widget"},
    ),
    # ---- __init__ imports are traced (fixed in #386) ------------------------
    Case(
        name="sibling_imported_only_by_init_is_shipped",
        project_files={
            "pyproject.toml": PYPROJECT,
            "pkg/__init__.py": "from pkg.sibling import helper\n",
            "pkg/sibling.py": "def helper(): return 1\n",
            "pkg/main.py": "def fn(): pass\n",
        },
        entry="pkg.main:fn",
        expected_shipped={"pkg/__init__.py", "pkg/sibling.py", "pkg/main.py"},
        expected_external=set(),
    ),
    # ---- Relative imports name real files --------------------------------
    Case(
        name="relative_import_sibling_is_shipped",
        project_files={
            "pyproject.toml": PYPROJECT,
            "pkg/__init__.py": "",
            "pkg/sibling.py": "def h(): return 1\n",
            "pkg/core.py": "from .sibling import h\ndef fn(): return h()\n",
        },
        entry="pkg.core:fn",
        expected_shipped={"pkg/__init__.py", "pkg/sibling.py", "pkg/core.py"},
        expected_external=set(),
    ),
    Case(
        name="relative_import_from_init_is_shipped",
        project_files={
            "pyproject.toml": PYPROJECT,
            "pkg/__init__.py": "from . import sibling\n",
            "pkg/sibling.py": "def h(): return 1\n",
            "pkg/main.py": "def fn(): pass\n",
        },
        entry="pkg.main:fn",
        expected_shipped={"pkg/__init__.py", "pkg/sibling.py", "pkg/main.py"},
        expected_external=set(),
    ),
    Case(
        name="relative_import_from_parent_package_is_shipped",
        project_files={
            "pyproject.toml": PYPROJECT,
            "pkg/__init__.py": "",
            "pkg/util.py": "def u(): return 1\n",
            "pkg/sub/__init__.py": "",
            "pkg/sub/main.py": "from ..util import u\ndef fn(): return u()\n",
        },
        entry="pkg.sub.main:fn",
        expected_shipped={
            "pkg/__init__.py",
            "pkg/util.py",
            "pkg/sub/__init__.py",
            "pkg/sub/main.py",
        },
        expected_external=set(),
    ),
    # ---- Installed first-party package: ship the whole directory ------------
    Case(
        # The model-gateway case: entry lives in a first-party package
        # git-installed into site-packages. Its __init__ pulls in siblings.
        name="entry_in_installed_package_ships_whole",
        project_files={"pyproject.toml": PYPROJECT},
        installed_files={
            "mypkg/__init__.py": "from mypkg.core import entry\nfrom mypkg.util import helper\n",
            "mypkg/core.py": "def entry(): return 1\n",
            "mypkg/util.py": "def helper(): return 2\n",
            **_vcs_dist_info("mypkg"),
        },
        entry="mypkg.core:entry",
        expected_shipped={"mypkg/__init__.py", "mypkg/core.py", "mypkg/util.py"},
        expected_external=set(),
    ),
    Case(
        # Tracing must not dead-end at an installed package: a third-party dep
        # imported by a submodule reached only through __init__ must still be
        # found. Shipping the package whole also brings that submodule's source.
        name="transitive_external_through_installed_package",
        project_files={"pyproject.toml": PYPROJECT},
        installed_files={
            "lib/__init__.py": "from lib.core import go\nfrom lib import heavy\n",
            "lib/core.py": "def go(): pass\n",
            "lib/heavy.py": "import cloudpickle\n",
            **_vcs_dist_info("lib"),
        },
        entry="lib.core:go",
        expected_shipped={"lib/__init__.py", "lib/core.py", "lib/heavy.py"},
        expected_external={"cloudpickle"},
    ),
    Case(
        # Non-.py runtime files: a package data file the code opens at runtime.
        # Whole-directory shipping of the installed package carries it.
        name="installed_package_data_file_is_shipped",
        project_files={"pyproject.toml": PYPROJECT},
        installed_files={
            "lib/__init__.py": "",
            "lib/loader.py": (
                "import pathlib\n"
                "def load(): return (pathlib.Path(__file__).parent / 'data.json').read_text()\n"
            ),
            "lib/data.json": '{"k": 1}\n',
            **_vcs_dist_info("lib"),
        },
        entry="lib.loader:load",
        expected_shipped={"lib/__init__.py", "lib/loader.py", "lib/data.json"},
        expected_external=set(),
    ),
    Case(
        # Dynamic import inside an installed package: static analysis cannot see
        # the computed name, but whole-directory shipping carries the submodule.
        name="dynamic_import_within_installed_package_is_shipped",
        project_files={"pyproject.toml": PYPROJECT},
        installed_files={
            "lib/__init__.py": "import importlib\nimportlib.import_module('lib.plugin')\n",
            "lib/plugin.py": "x = 1\n",
            "lib/core.py": "def go(): pass\n",
            **_vcs_dist_info("lib"),
        },
        entry="lib.core:go",
        expected_shipped={"lib/__init__.py", "lib/plugin.py", "lib/core.py"},
        expected_external=set(),
    ),
    Case(
        # Graph spans two sys.path roots: the entry is the submitter's own
        # source, which imports a first-party package installed in
        # site-packages. Both must ship, each under its own import root.
        name="graph_spans_project_source_and_installed_package",
        project_files={
            "pyproject.toml": PYPROJECT,
            "app/__init__.py": "",
            "app/main.py": "import mypkg\ndef fn(): return mypkg\n",
        },
        installed_files={
            "mypkg/__init__.py": "from mypkg.core import entry\n",
            "mypkg/core.py": "def entry(): return 1\n",
            **_vcs_dist_info("mypkg"),
        },
        entry="app.main:fn",
        expected_shipped={
            "app/__init__.py",
            "app/main.py",
            "mypkg/__init__.py",
            "mypkg/core.py",
        },
        expected_external=set(),
    ),
]


class _Env:
    """Materialize a Case on disk, put its roots on sys.path, expose the entry
    function, and restore sys.path / sys.modules on cleanup."""

    def __init__(self, case: Case) -> None:
        self.root = Path(tempfile.mkdtemp()).resolve()
        for rel, content in case.project_files.items():
            self._write(self.root / rel, content)
        self.site = self.root / SITE
        for rel, content in case.installed_files.items():
            self._write(self.site / rel, content)

        self._entry = case.entry
        self._modules_before = set(sys.modules)
        self._paths: list[str] = []
        source = self.root / case.source_root if case.source_root else self.root
        roots = ([self.site] if case.installed_files else []) + [source]
        for p in roots:
            sys.path.insert(0, str(p))
            self._paths.append(str(p))

    @staticmethod
    def _write(target: Path, content: str) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def fn(self) -> Callable[..., Any]:
        module_name, _, fn_name = self._entry.partition(":")
        return getattr(importlib.import_module(module_name), fn_name)

    def cleanup(self) -> None:
        for p in self._paths:
            if p in sys.path:
                sys.path.remove(p)
        for name in list(sys.modules):
            if name not in self._modules_before:
                sys.modules.pop(name, None)
        shutil.rmtree(self.root, ignore_errors=True)


def _import_paths(files: set[Path]) -> set[str]:
    """Each shipped file as its import path -- relative to its own sys.path
    root -- so a bundle spanning several roots stays expressible."""
    return {f.relative_to(infer_module_path(f)[1]).as_posix() for f in files}


class TestTracing(unittest.TestCase):
    def _env(self, case: Case) -> _Env:
        env = _Env(case)
        self.addCleanup(env.cleanup)
        return env

    @parameterized.expand([(c.name, c) for c in CASES])
    def test_shipped_files(self, _name: str, case: Case) -> None:
        env = self._env(case)
        _, files, _ = bundle_for_entry(env.fn())
        self.assertEqual(_import_paths(files), case.expected_shipped)

    @parameterized.expand([(c.name, c) for c in CASES])
    def test_external_deps(self, _name: str, case: Case) -> None:
        env = self._env(case)
        _, _, external = bundle_for_entry(env.fn())
        self.assertEqual(external, case.expected_external)


SAMPLE_PIP_FREEZE = (
    "cloudpickle==3.0.0\n"
    "tqdm==4.66.0\n"
    "numpy==1.26.0\n"
    "cortexflow @ git+https://github.com/robodatalab/robolab-infra.git@abc123\n"
)


class TestFilterPipFreeze(unittest.TestCase):
    def test_keeps_only_external_top_levels(self) -> None:
        filtered = filter_pip_freeze(SAMPLE_PIP_FREEZE, {"cloudpickle"})
        self.assertEqual(filtered, "cloudpickle==3.0.0")

    def test_keeps_multiple(self) -> None:
        filtered = filter_pip_freeze(SAMPLE_PIP_FREEZE, {"cloudpickle", "tqdm"})
        self.assertEqual(filtered, "cloudpickle==3.0.0\ntqdm==4.66.0")

    def test_empty_when_nothing_external(self) -> None:
        self.assertEqual(filter_pip_freeze(SAMPLE_PIP_FREEZE, set()), "")


if __name__ == "__main__":
    unittest.main()
