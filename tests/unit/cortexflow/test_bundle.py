"""Tests for the code bundler.

Each test materializes a source tree, puts one or more directories on sys.path
(mirroring how the interpreter would find the modules), and asserts which files
`bundle` collects. Paths are compared relative to the tree root for readability.
"""

from __future__ import annotations

import importlib
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from cortexflow._bundle import bundle, stage


class _Tree:
    """Materialize `files` under a temp dir, put `roots` (relpaths) on sys.path,
    and restore sys.path / sys.modules on cleanup."""

    def __init__(self, files: dict[str, str], roots: tuple[str, ...] = ("",)) -> None:
        self.root = Path(tempfile.mkdtemp()).resolve()
        for rel, content in files.items():
            target = self.root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        self._modules_before = set(sys.modules)
        self._paths = [str(self.root / r) if r else str(self.root) for r in roots]
        for path in self._paths:
            sys.path.insert(0, path)
        importlib.invalidate_caches()

    def path(self, rel: str) -> Path:
        return (self.root / rel).resolve()

    def rel(self, files: set[Path]) -> set[str]:
        return {f.relative_to(self.root).as_posix() for f in files}

    def cleanup(self) -> None:
        for path in self._paths:
            if path in sys.path:
                sys.path.remove(path)
        for name in list(sys.modules):
            if name not in self._modules_before:
                sys.modules.pop(name, None)
        shutil.rmtree(self.root, ignore_errors=True)


class TestBundle(unittest.TestCase):
    def _tree(self, files: dict[str, str], roots: tuple[str, ...] = ("",)) -> _Tree:
        tree = _Tree(files, roots)
        self.addCleanup(tree.cleanup)
        return tree

    def test_single_module(self) -> None:
        tree = self._tree({"main.py": "import json\ndef fn(): pass\n"})
        self.assertEqual(tree.rel(bundle(tree.path("main.py"))), {"main.py"})

    def test_standard_library_is_excluded(self) -> None:
        tree = self._tree({"main.py": "import os, sys, json\nfrom pathlib import Path\n"})
        self.assertEqual(tree.rel(bundle(tree.path("main.py"))), {"main.py"})

    def test_package_init_chain_is_included(self) -> None:
        tree = self._tree(
            {"a/__init__.py": "", "a/b/__init__.py": "", "a/b/main.py": "x = 1\n"}
        )
        self.assertEqual(
            tree.rel(bundle(tree.path("a/b/main.py"))),
            {"a/__init__.py", "a/b/__init__.py", "a/b/main.py"},
        )

    def test_absolute_sibling_import(self) -> None:
        tree = self._tree(
            {
                "pkg/__init__.py": "",
                "pkg/helper.py": "def x(): return 1\n",
                "pkg/main.py": "from pkg.helper import x\n",
            }
        )
        self.assertEqual(
            tree.rel(bundle(tree.path("pkg/main.py"))),
            {"pkg/__init__.py", "pkg/helper.py", "pkg/main.py"},
        )

    def test_relative_sibling_import(self) -> None:
        tree = self._tree(
            {
                "pkg/__init__.py": "",
                "pkg/helper.py": "def x(): return 1\n",
                "pkg/main.py": "from .helper import x\n",
            }
        )
        self.assertEqual(
            tree.rel(bundle(tree.path("pkg/main.py"))),
            {"pkg/__init__.py", "pkg/helper.py", "pkg/main.py"},
        )

    def test_relative_from_dot_import(self) -> None:
        tree = self._tree(
            {
                "pkg/__init__.py": "from . import sub\n",
                "pkg/sub.py": "y = 2\n",
                "pkg/main.py": "def fn(): pass\n",
            }
        )
        self.assertEqual(
            tree.rel(bundle(tree.path("pkg/main.py"))),
            {"pkg/__init__.py", "pkg/sub.py", "pkg/main.py"},
        )

    def test_relative_parent_import(self) -> None:
        tree = self._tree(
            {
                "pkg/__init__.py": "",
                "pkg/util.py": "def u(): return 1\n",
                "pkg/sub/__init__.py": "",
                "pkg/sub/main.py": "from ..util import u\n",
            }
        )
        self.assertEqual(
            tree.rel(bundle(tree.path("pkg/sub/main.py"))),
            {"pkg/__init__.py", "pkg/util.py", "pkg/sub/__init__.py", "pkg/sub/main.py"},
        )

    def test_transitive_imports(self) -> None:
        tree = self._tree(
            {
                "a.py": "import b\n",
                "b.py": "import c\n",
                "c.py": "x = 1\n",
                "unused.py": "raise RuntimeError\n",
            }
        )
        self.assertEqual(tree.rel(bundle(tree.path("a.py"))), {"a.py", "b.py", "c.py"})

    def test_init_pulls_sibling_the_entry_never_imports(self) -> None:
        # The model-gateway shape: the package __init__ eagerly imports a sibling
        # the entry module never references. Importing the entry runs __init__,
        # so that sibling must ship.
        tree = self._tree(
            {
                "pkg/__init__.py": "from pkg.core import go\nfrom pkg.side import helper\n",
                "pkg/core.py": "def go(): pass\n",
                "pkg/side.py": "def helper(): return 1\n",
            }
        )
        self.assertEqual(
            tree.rel(bundle(tree.path("pkg/core.py"))),
            {"pkg/__init__.py", "pkg/core.py", "pkg/side.py"},
        )

    def test_dependency_in_a_separate_sys_path_root(self) -> None:
        # `lib` is installed under a second root (a .venv/site-packages stand-in);
        # bundle follows the import into it regardless of where it lives.
        tree = self._tree(
            {
                "app/__init__.py": "",
                "app/main.py": "import lib\n",
                "site/lib/__init__.py": "z = 1\n",
            },
            roots=("", "site"),
        )
        self.assertEqual(
            tree.rel(bundle(tree.path("app/main.py"))),
            {"app/__init__.py", "app/main.py", "site/lib/__init__.py"},
        )

    def test_deeply_nested_package(self) -> None:
        tree = self._tree(
            {
                "a/__init__.py": "",
                "a/b/__init__.py": "",
                "a/b/c/__init__.py": "",
                "a/b/c/main.py": "import json\n",
            }
        )
        self.assertEqual(
            tree.rel(bundle(tree.path("a/b/c/main.py"))),
            {"a/__init__.py", "a/b/__init__.py", "a/b/c/__init__.py", "a/b/c/main.py"},
        )

    def test_cross_subpackage_import(self) -> None:
        tree = self._tree(
            {
                "a/__init__.py": "",
                "a/util.py": "def y(): return 2\n",
                "a/b/__init__.py": "",
                "a/b/main.py": "from a.util import y\n",
            }
        )
        self.assertEqual(
            tree.rel(bundle(tree.path("a/b/main.py"))),
            {"a/__init__.py", "a/util.py", "a/b/__init__.py", "a/b/main.py"},
        )

    def test_from_package_import_submodule(self) -> None:
        tree = self._tree(
            {
                "pkg/__init__.py": "",
                "pkg/sub.py": "z = 1\n",
                "pkg/main.py": "from pkg import sub\n",
            }
        )
        self.assertEqual(
            tree.rel(bundle(tree.path("pkg/main.py"))),
            {"pkg/__init__.py", "pkg/sub.py", "pkg/main.py"},
        )

    def test_diamond_import_visits_leaf_once(self) -> None:
        tree = self._tree(
            {
                "pkg/__init__.py": "",
                "pkg/leaf.py": "z = 1\n",
                "pkg/left.py": "from pkg.leaf import z\n",
                "pkg/right.py": "from pkg.leaf import z\n",
                "pkg/main.py": "from pkg.left import z\nfrom pkg.right import z as z2\n",
            }
        )
        self.assertEqual(
            tree.rel(bundle(tree.path("pkg/main.py"))),
            {
                "pkg/__init__.py",
                "pkg/leaf.py",
                "pkg/left.py",
                "pkg/right.py",
                "pkg/main.py",
            },
        )

    def test_unimported_sibling_is_not_shipped(self) -> None:
        tree = self._tree(
            {
                "pkg/__init__.py": "",
                "pkg/main.py": "import json\n",
                "pkg/unused.py": "raise RuntimeError('never imported')\n",
            }
        )
        self.assertEqual(
            tree.rel(bundle(tree.path("pkg/main.py"))),
            {"pkg/__init__.py", "pkg/main.py"},
        )

    def test_entry_defined_in_an_installed_package(self) -> None:
        # The model-gateway shape: the entry function lives in a package that
        # sits under a separate sys.path root (site-packages), and importing it
        # runs the package __init__, which pulls in a sibling.
        tree = self._tree(
            {
                "site/mypkg/__init__.py": "from mypkg.core import go\nfrom mypkg import extra\n",
                "site/mypkg/core.py": "def go(): pass\n",
                "site/mypkg/extra.py": "y = 1\n",
            },
            roots=("site",),
        )
        self.assertEqual(
            tree.rel(bundle(tree.path("site/mypkg/core.py"))),
            {"site/mypkg/__init__.py", "site/mypkg/core.py", "site/mypkg/extra.py"},
        )

    def test_subtraction_drops_a_dependency(self) -> None:
        tree = self._tree(
            {
                "app/__init__.py": "",
                "app/main.py": "import lib\n",
                "lib/__init__.py": "from lib.core import c\n",
                "lib/core.py": "c = 1\n",
            }
        )
        needed = bundle(tree.path("app/main.py"))
        baked = bundle(tree.path("lib/__init__.py"))
        self.assertEqual(
            tree.rel(needed - baked), {"app/__init__.py", "app/main.py"}
        )

    def test_subtraction_also_drops_the_dependencys_own_deps(self) -> None:
        # Subtracting a baked package removes its whole subtree -- the reason a
        # baked package like torch also takes sympy/numpy off the ship list.
        tree = self._tree(
            {
                "app/__init__.py": "",
                "app/main.py": "import lib\n",
                "lib/__init__.py": "import lib.core\n",
                "lib/core.py": "import lib.deep\n",
                "lib/deep.py": "d = 1\n",
            }
        )
        needed = bundle(tree.path("app/main.py"))
        baked = bundle(tree.path("lib/__init__.py"))
        self.assertEqual(
            tree.rel(needed - baked), {"app/__init__.py", "app/main.py"}
        )


class TestStage(unittest.TestCase):
    def test_stage_lays_files_out_at_their_import_paths(self) -> None:
        root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, root, True)
        for rel, content in {
            "app/__init__.py": "",
            "app/main.py": "x = 1\n",
            ".venv/site-packages/lib/__init__.py": "y = 2\n",
        }.items():
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        sys.path.insert(0, str(root))
        sys.path.insert(0, str(root / ".venv/site-packages"))
        self.addCleanup(lambda: sys.path.remove(str(root)))
        self.addCleanup(lambda: sys.path.remove(str(root / ".venv/site-packages")))

        files = {
            (root / "app/main.py").resolve(),
            (root / "app/__init__.py").resolve(),
            (root / ".venv/site-packages/lib/__init__.py").resolve(),
        }
        dest = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, dest, True)
        stage(files, dest)

        staged = {f.relative_to(dest).as_posix() for f in dest.rglob("*") if f.is_file()}
        # Both roots collapse into one importable tree: app/ from the project,
        # lib/ from site-packages, side by side.
        self.assertEqual(staged, {"app/__init__.py", "app/main.py", "lib/__init__.py"})


if __name__ == "__main__":
    unittest.main()
