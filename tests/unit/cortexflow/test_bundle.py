"""Each Case has three inputs (workspace files, entry function, sample pip
freeze) and four expected outputs (bundled files, ship root, external
imports, filtered pip freeze). All test methods are parameterized over the
same CASES list.
"""

from __future__ import annotations

import importlib
import shutil
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from parameterized import parameterized  # type: ignore

from cortexflow._bundle import bundle_for_entry, filter_pip_freeze


SAMPLE_PIP_FREEZE = (
    "cloudpickle==3.0.0\n"
    "tqdm==4.66.0\n"
    "numpy==1.26.0\n"
    "cortexflow @ git+https://github.com/robodatalab/robolab-infra.git@abc123\n"
)


@dataclass
class Case:
    name: str

    # --- inputs ------------------------------------------------------------
    # File tree to materialize under a temp dir. Includes pyproject.toml.
    input_files: dict[str, str]
    # "<dotted.module>:<function>" — the entry function passed to the bundler.
    input_entry: str
    # Sample pip freeze output to feed through filter_pip_freeze.
    input_pip_freeze: str

    # --- expected outputs --------------------------------------------------
    # Posix relpaths from the ship root of every file the bundler should ship.
    expected_files: set[str]
    # Posix relpath from the workspace root to the ship root.
    expected_ship_root: str
    # Top-level names of imports that are external (not stdlib, not workspace).
    expected_external_deps: set[str]
    # filter_pip_freeze(input_pip_freeze, expected_external_deps) must equal this.
    expected_filtered_pip_freeze: str


CASES: list[Case] = [
    Case(
        name="single_module_top_level",
        input_files={
            "pyproject.toml": '[project]\nname="x"\nversion="0"\n',
            "main.py": "import json\ndef fn(): pass\n",
        },
        input_entry="main:fn",
        input_pip_freeze=SAMPLE_PIP_FREEZE,
        expected_files={"main.py"},
        expected_ship_root="",
        expected_external_deps=set(),
        expected_filtered_pip_freeze="",
    ),
    Case(
        name="single_package_stdlib_only",
        input_files={
            "pyproject.toml": '[project]\nname="x"\nversion="0"\n',
            "pkg/__init__.py": "",
            "pkg/main.py": "import json\ndef fn(): pass\n",
        },
        input_entry="pkg.main:fn",
        input_pip_freeze=SAMPLE_PIP_FREEZE,
        expected_files={"pkg/__init__.py", "pkg/main.py"},
        expected_ship_root="",
        expected_external_deps=set(),
        expected_filtered_pip_freeze="",
    ),
    Case(
        name="package_with_sibling_helper",
        input_files={
            "pyproject.toml": '[project]\nname="x"\nversion="0"\n',
            "pkg/__init__.py": "",
            "pkg/helper.py": "def x(): return 1\n",
            "pkg/main.py": "from pkg.helper import x\ndef fn(): return x()\n",
        },
        input_entry="pkg.main:fn",
        input_pip_freeze=SAMPLE_PIP_FREEZE,
        expected_files={"pkg/__init__.py", "pkg/helper.py", "pkg/main.py"},
        expected_ship_root="",
        expected_external_deps=set(),
        expected_filtered_pip_freeze="",
    ),
    Case(
        name="deeply_nested_package",
        input_files={
            "pyproject.toml": '[project]\nname="x"\nversion="0"\n',
            "a/__init__.py": "",
            "a/b/__init__.py": "",
            "a/b/c/__init__.py": "",
            "a/b/c/main.py": "import json\ndef fn(): pass\n",
        },
        input_entry="a.b.c.main:fn",
        input_pip_freeze=SAMPLE_PIP_FREEZE,
        expected_files={
            "a/__init__.py",
            "a/b/__init__.py",
            "a/b/c/__init__.py",
            "a/b/c/main.py",
        },
        expected_ship_root="",
        expected_external_deps=set(),
        expected_filtered_pip_freeze="",
    ),
    Case(
        name="cross_subpackage_import",
        input_files={
            "pyproject.toml": '[project]\nname="x"\nversion="0"\n',
            "a/__init__.py": "",
            "a/util.py": "def y(): return 2\n",
            "a/b/__init__.py": "",
            "a/b/main.py": "from a.util import y\ndef fn(): return y()\n",
        },
        input_entry="a.b.main:fn",
        input_pip_freeze=SAMPLE_PIP_FREEZE,
        expected_files={
            "a/__init__.py",
            "a/util.py",
            "a/b/__init__.py",
            "a/b/main.py",
        },
        expected_ship_root="",
        expected_external_deps=set(),
        expected_filtered_pip_freeze="",
    ),
    Case(
        name="third_party_import_recognized_as_external",
        input_files={
            "pyproject.toml": '[project]\nname="x"\nversion="0"\n',
            "pkg/__init__.py": "",
            "pkg/main.py": "import cloudpickle\ndef fn(): pass\n",
        },
        input_entry="pkg.main:fn",
        input_pip_freeze=SAMPLE_PIP_FREEZE,
        expected_files={"pkg/__init__.py", "pkg/main.py"},
        expected_ship_root="",
        expected_external_deps={"cloudpickle"},
        expected_filtered_pip_freeze="cloudpickle==3.0.0",
    ),
    Case(
        name="third_party_used_only_via_workspace_helper",
        input_files={
            "pyproject.toml": '[project]\nname="x"\nversion="0"\n',
            "pkg/__init__.py": "",
            "pkg/helper.py": "import cloudpickle\ndef ser(x): return cloudpickle.dumps(x)\n",
            "pkg/main.py": "from pkg.helper import ser\ndef fn(): pass\n",
        },
        input_entry="pkg.main:fn",
        input_pip_freeze=SAMPLE_PIP_FREEZE,
        expected_files={"pkg/__init__.py", "pkg/helper.py", "pkg/main.py"},
        expected_ship_root="",
        expected_external_deps={"cloudpickle"},
        expected_filtered_pip_freeze="cloudpickle==3.0.0",
    ),
    Case(
        name="multiple_third_party_deps",
        input_files={
            "pyproject.toml": '[project]\nname="x"\nversion="0"\n',
            "pkg/__init__.py": "",
            "pkg/main.py": (
                "import cloudpickle\nfrom tqdm import tqdm\ndef fn(): pass\n"
            ),
        },
        input_entry="pkg.main:fn",
        input_pip_freeze=SAMPLE_PIP_FREEZE,
        expected_files={"pkg/__init__.py", "pkg/main.py"},
        expected_ship_root="",
        expected_external_deps={"cloudpickle", "tqdm"},
        expected_filtered_pip_freeze="cloudpickle==3.0.0\ntqdm==4.66.0",
    ),
    Case(
        # If the same in-tree library shows up as both workspace files AND
        # in pip freeze (an editable install), we must favour the local
        # version: don't ship it via files AND don't pin it via freeze.
        # Either it's reachable in workspace (shipped as files) or it's not
        # used at all. The pip freeze line for it gets dropped because the
        # bundler never adds it to external.
        name="editable_install_in_workspace_is_shipped_as_files_not_pinned",
        input_files={
            "pyproject.toml": '[project]\nname="x"\nversion="0"\n',
            "cortexflow/__init__.py": "VERSION = '0'\n",
            "myapp/__init__.py": "",
            "myapp/main.py": "import cortexflow\ndef fn(): return cortexflow\n",
        },
        input_entry="myapp.main:fn",
        input_pip_freeze=SAMPLE_PIP_FREEZE,
        expected_files={
            "cortexflow/__init__.py",
            "myapp/__init__.py",
            "myapp/main.py",
        },
        expected_ship_root="",
        expected_external_deps=set(),
        expected_filtered_pip_freeze="",
    ),
    Case(
        name="diamond_import_visits_leaf_only_once",
        input_files={
            "pyproject.toml": '[project]\nname="x"\nversion="0"\n',
            "pkg/__init__.py": "",
            "pkg/leaf.py": "def z(): return 3\n",
            "pkg/left.py": "from pkg.leaf import z\n",
            "pkg/right.py": "from pkg.leaf import z\n",
            "pkg/main.py": (
                "from pkg.left import z\n"
                "from pkg.right import z as z2\n"
                "def fn(): pass\n"
            ),
        },
        input_entry="pkg.main:fn",
        input_pip_freeze=SAMPLE_PIP_FREEZE,
        expected_files={
            "pkg/__init__.py",
            "pkg/leaf.py",
            "pkg/left.py",
            "pkg/right.py",
            "pkg/main.py",
        },
        expected_ship_root="",
        expected_external_deps=set(),
        expected_filtered_pip_freeze="",
    ),
    Case(
        # The first ancestor dir WITHOUT __init__.py is what goes on the
        # worker's sys.path — that's how Python resolves absolute imports.
        # The pyproject lives further up; it's only a workspace root candidate.
        name="ship_root_is_first_dir_without_init",
        input_files={
            "outer/pyproject.toml": '[project]\nname="x"\nversion="0"\n',
            "outer/inner/__init__.py": "",
            "outer/inner/main.py": "def fn(): pass\n",
        },
        input_entry="inner.main:fn",
        input_pip_freeze=SAMPLE_PIP_FREEZE,
        expected_files={"inner/__init__.py", "inner/main.py"},
        expected_ship_root="outer",
        expected_external_deps=set(),
        expected_filtered_pip_freeze="",
    ),
    Case(
        # A sibling reachable ONLY through the package __init__.py (the entry
        # graph never imports it directly) must still ship: importing the entry
        # executes __init__.py, which imports the sibling at load time. The
        # bundler has to trace __init__.py's own imports, not just staple it on.
        name="sibling_imported_only_by_init_is_shipped",
        input_files={
            "pyproject.toml": '[project]\nname="x"\nversion="0"\n',
            "pkg/__init__.py": "from pkg.sibling import helper\n",
            "pkg/sibling.py": "def helper(): return 1\n",
            "pkg/main.py": "def fn(): pass\n",
        },
        input_entry="pkg.main:fn",
        input_pip_freeze=SAMPLE_PIP_FREEZE,
        expected_files={"pkg/__init__.py", "pkg/sibling.py", "pkg/main.py"},
        expected_ship_root="",
        expected_external_deps=set(),
        expected_filtered_pip_freeze="",
    ),
    Case(
        # Files under workspace_root that the entry doesn't import must NOT
        # land in the bundle. Without this case, a buggy bundler that ships
        # everything under workspace_root would still pass every other case.
        name="unimported_files_are_not_shipped",
        input_files={
            "pyproject.toml": '[project]\nname="x"\nversion="0"\n',
            "pkg/__init__.py": "",
            "pkg/main.py": "import json\ndef fn(): pass\n",
            "pkg/unused_sibling.py": "raise RuntimeError('should not import')\n",
            "pkg/also_unused/__init__.py": "",
            "pkg/also_unused/x.py": "x = 1\n",
            "unrelated_top_level.py": "y = 2\n",
        },
        input_entry="pkg.main:fn",
        input_pip_freeze=SAMPLE_PIP_FREEZE,
        expected_files={"pkg/__init__.py", "pkg/main.py"},
        expected_ship_root="",
        expected_external_deps=set(),
        expected_filtered_pip_freeze="",
    ),
]


class _Workspace:
    """Materialize a Case's input_files into a tempdir, expose its entry fn,
    and tear sys.path/sys.modules state back down on cleanup."""

    def __init__(
        self, input_files: dict[str, str], input_entry: str, ship_root_rel: str
    ) -> None:
        self.root = Path(tempfile.mkdtemp()).resolve()
        for rel, content in input_files.items():
            target = self.root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        self.ship_root = self.root / ship_root_rel if ship_root_rel else self.root
        self._entry = input_entry
        self._modules_before = set(sys.modules)
        sys.path.insert(0, str(self.ship_root))

    def fn(self) -> Callable[..., Any]:
        module_name, _, fn_name = self._entry.partition(":")
        return getattr(importlib.import_module(module_name), fn_name)

    def cleanup(self) -> None:
        sys.path.remove(str(self.ship_root))
        for name in list(sys.modules):
            if name not in self._modules_before:
                sys.modules.pop(name, None)
        shutil.rmtree(self.root, ignore_errors=True)


class TestBundle(unittest.TestCase):
    def _build(self, case: Case) -> _Workspace:
        ws = _Workspace(case.input_files, case.input_entry, case.expected_ship_root)
        self.addCleanup(ws.cleanup)
        return ws

    @parameterized.expand([(c.name, c) for c in CASES])
    def test_bundle_includes_expected_files(self, _name: str, case: Case) -> None:
        ws = self._build(case)
        ship_root, files, _ = bundle_for_entry(ws.fn())
        rel = {f.relative_to(ship_root).as_posix() for f in files}
        self.assertEqual(rel, case.expected_files)

    @parameterized.expand([(c.name, c) for c in CASES])
    def test_bundle_identifies_ship_root(self, _name: str, case: Case) -> None:
        ws = self._build(case)
        ship_root, _, _ = bundle_for_entry(ws.fn())
        self.assertEqual(ship_root, ws.ship_root)

    @parameterized.expand([(c.name, c) for c in CASES])
    def test_bundle_identifies_external_deps(self, _name: str, case: Case) -> None:
        ws = self._build(case)
        _, _, external = bundle_for_entry(ws.fn())
        self.assertEqual(external, case.expected_external_deps)

    @parameterized.expand([(c.name, c) for c in CASES])
    def test_filter_pip_freeze(self, _name: str, case: Case) -> None:
        filtered = filter_pip_freeze(
            case.input_pip_freeze, case.expected_external_deps
        )
        self.assertEqual(filtered, case.expected_filtered_pip_freeze)
