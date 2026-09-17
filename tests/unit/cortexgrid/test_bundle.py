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

from cortexgrid._bundle import (
    BundleDesc,
    UnownedDependencyError,
    bundle,
    digest,
    distribution_closure,
    stage,
)


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


def _dist_info(
    site: str,
    name: str,
    version: str,
    files: tuple[str, ...] = (),
    requires: tuple[str, ...] = (),
) -> dict[str, str]:
    """The metadata an installer leaves in `site` for distribution `name`: its
    METADATA (with Requires-Dist lines) and a RECORD listing `files`, relative
    to `site`."""
    info = f"{site}/{name.replace('-', '_')}-{version}.dist-info"
    metadata = f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
    metadata += "".join(f"Requires-Dist: {spec}\n" for spec in requires)
    return {
        f"{info}/METADATA": metadata,
        f"{info}/RECORD": "".join(f"{file},,\n" for file in files),
    }


class TestBundle(unittest.TestCase):
    def _tree(self, files: dict[str, str], roots: tuple[str, ...] = ("",)) -> _Tree:
        tree = _Tree(files, roots)
        self.addCleanup(tree.cleanup)
        return tree

    def test_single_module(self) -> None:
        tree = self._tree({"main.py": "import json\ndef fn(): pass\n"})
        self.assertEqual(
            tree.rel(bundle(tree.path("main.py")).local_files), {"main.py"}
        )

    def test_standard_library_is_excluded(self) -> None:
        tree = self._tree({"main.py": "import os, sys, json\nfrom pathlib import Path\n"})
        self.assertEqual(
            tree.rel(bundle(tree.path("main.py")).local_files), {"main.py"}
        )

    def test_package_init_chain_is_included(self) -> None:
        tree = self._tree(
            {"a/__init__.py": "", "a/b/__init__.py": "", "a/b/main.py": "x = 1\n"}
        )
        self.assertEqual(
            tree.rel(bundle(tree.path("a/b/main.py")).local_files),
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
            tree.rel(bundle(tree.path("pkg/main.py")).local_files),
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
            tree.rel(bundle(tree.path("pkg/main.py")).local_files),
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
            tree.rel(bundle(tree.path("pkg/main.py")).local_files),
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
            tree.rel(bundle(tree.path("pkg/sub/main.py")).local_files),
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
        self.assertEqual(
            tree.rel(bundle(tree.path("a.py")).local_files), {"a.py", "b.py", "c.py"}
        )

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
            tree.rel(bundle(tree.path("pkg/core.py")).local_files),
            {"pkg/__init__.py", "pkg/core.py", "pkg/side.py"},
        )

    def test_dependency_in_a_separate_sys_path_root(self) -> None:
        # `lib` is local source under a second root (a src/ layout, a sibling
        # checkout); bundle follows the import into it.
        tree = self._tree(
            {
                "app/__init__.py": "",
                "app/main.py": "import lib\n",
                "libs/lib/__init__.py": "z = 1\n",
            },
            roots=("", "libs"),
        )
        self.assertEqual(
            tree.rel(bundle(tree.path("app/main.py")).local_files),
            {"app/__init__.py", "app/main.py", "libs/lib/__init__.py"},
        )

    def test_dependency_in_site_packages_is_not_followed(self) -> None:
        # `lib` is an installed package: neither it nor anything it imports
        # ships, even a module that lives in the local tree.
        tree = self._tree(
            {
                "app/__init__.py": "",
                "app/main.py": "import lib\n",
                "helper.py": "h = 1\n",
                "site-packages/lib/__init__.py": "import helper\n",
                **_dist_info("site-packages", "lib", "1.0", ("lib/__init__.py",)),
            },
            roots=("", "site-packages"),
        )
        desc = bundle(tree.path("app/main.py"))
        self.assertEqual(tree.rel(desc.local_files), {"app/__init__.py", "app/main.py"})
        self.assertEqual(desc.tp_deps, {"lib": "1.0"})

    def test_dependency_in_dist_packages_is_not_followed(self) -> None:
        tree = self._tree(
            {
                "app/__init__.py": "",
                "app/main.py": "import lib\n",
                "helper.py": "h = 1\n",
                "dist-packages/lib/__init__.py": "import helper\n",
                **_dist_info("dist-packages", "lib", "1.0", ("lib/__init__.py",)),
            },
            roots=("", "dist-packages"),
        )
        desc = bundle(tree.path("app/main.py"))
        self.assertEqual(tree.rel(desc.local_files), {"app/__init__.py", "app/main.py"})
        self.assertEqual(desc.tp_deps, {"lib": "1.0"})

    def test_bundling_does_not_import_packages(self) -> None:
        # Resolving `pkg.sub` must not execute pkg/__init__.py -- an installed
        # package's __init__ can raise on this platform (click._winconsole).
        tree = self._tree(
            {
                "main.py": "import pkg.sub\n",
                "pkg/__init__.py": "raise RuntimeError('must not be imported')\n",
                "pkg/sub.py": "s = 1\n",
            }
        )
        self.assertEqual(
            tree.rel(bundle(tree.path("main.py")).local_files),
            {"main.py", "pkg/__init__.py", "pkg/sub.py"},
        )

    def test_namespace_package_submodule(self) -> None:
        tree = self._tree(
            {
                "main.py": "import ns.sub.mod\n",
                "ns/sub/mod.py": "m = 1\n",
            }
        )
        self.assertEqual(
            tree.rel(bundle(tree.path("main.py")).local_files),
            {"main.py", "ns/sub/mod.py"},
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
            tree.rel(bundle(tree.path("a/b/c/main.py")).local_files),
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
            tree.rel(bundle(tree.path("a/b/main.py")).local_files),
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
            tree.rel(bundle(tree.path("pkg/main.py")).local_files),
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
            tree.rel(bundle(tree.path("pkg/main.py")).local_files),
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
            tree.rel(bundle(tree.path("pkg/main.py")).local_files),
            {"pkg/__init__.py", "pkg/main.py"},
        )

    def test_entry_defined_in_a_separate_sys_path_root(self) -> None:
        # The model-gateway shape: the entry function lives in a package that
        # sits under a separate local sys.path root, and importing it runs the
        # package __init__, which pulls in a sibling.
        tree = self._tree(
            {
                "libs/mypkg/__init__.py": "from mypkg.core import go\nfrom mypkg import extra\n",
                "libs/mypkg/core.py": "def go(): pass\n",
                "libs/mypkg/extra.py": "y = 1\n",
            },
            roots=("libs",),
        )
        self.assertEqual(
            tree.rel(bundle(tree.path("libs/mypkg/core.py")).local_files),
            {"libs/mypkg/__init__.py", "libs/mypkg/core.py", "libs/mypkg/extra.py"},
        )

    def test_entry_defined_in_an_installed_package(self) -> None:
        # The same shape installed into site-packages: the entry is itself
        # third-party, so nothing ships as a local file -- its distribution is
        # installed instead.
        tree = self._tree(
            {
                "site-packages/mypkg/__init__.py": "from mypkg.core import go\nfrom mypkg import extra\n",
                "site-packages/mypkg/core.py": "def go(): pass\n",
                "site-packages/mypkg/extra.py": "y = 1\n",
                **_dist_info(
                    "site-packages",
                    "mypkg",
                    "1.0",
                    ("mypkg/__init__.py", "mypkg/core.py", "mypkg/extra.py"),
                ),
            },
            roots=("site-packages",),
        )
        desc = bundle(tree.path("site-packages/mypkg/core.py"))
        self.assertEqual(desc.local_files, set())
        self.assertEqual(desc.tp_deps, {"mypkg": "1.0"})

    def test_third_party_dependency_is_keyed_by_canonical_distribution_name(self) -> None:
        # The import name (yaml) differs from the distribution name (PyYAML);
        # pip needs the distribution.
        tree = self._tree(
            {
                "main.py": "import yaml\n",
                "site-packages/yaml/__init__.py": "",
                **_dist_info("site-packages", "PyYAML", "6.0.1", ("yaml/__init__.py",)),
            },
            roots=("", "site-packages"),
        )
        self.assertEqual(bundle(tree.path("main.py")).tp_deps, {"pyyaml": "6.0.1"})

    def test_third_party_single_file_module(self) -> None:
        tree = self._tree(
            {
                "main.py": "from six import moves\n",
                "site-packages/six.py": "",
                **_dist_info("site-packages", "six", "1.16.0", ("six.py",)),
            },
            roots=("", "site-packages"),
        )
        self.assertEqual(bundle(tree.path("main.py")).tp_deps, {"six": "1.16.0"})

    def test_third_party_dependency_of_a_transitive_local_import(self) -> None:
        tree = self._tree(
            {
                "app/__init__.py": "",
                "app/main.py": "from app import util\n",
                "app/util.py": "import lib.sub\n",
                "site-packages/lib/__init__.py": "",
                "site-packages/lib/sub.py": "",
                **_dist_info(
                    "site-packages", "lib", "2.3", ("lib/__init__.py", "lib/sub.py")
                ),
            },
            roots=("", "site-packages"),
        )
        desc = bundle(tree.path("app/main.py"))
        self.assertEqual(
            tree.rel(desc.local_files), {"app/__init__.py", "app/main.py", "app/util.py"}
        )
        self.assertEqual(desc.tp_deps, {"lib": "2.3"})

    def test_installed_namespace_package_portion_maps_to_its_distribution(self) -> None:
        tree = self._tree(
            {
                "main.py": "import ns.plugin\n",
                "site-packages/ns/plugin/__init__.py": "",
                **_dist_info(
                    "site-packages", "ns-plugin", "0.4", ("ns/plugin/__init__.py",)
                ),
            },
            roots=("", "site-packages"),
        )
        self.assertEqual(bundle(tree.path("main.py")).tp_deps, {"ns-plugin": "0.4"})

    def test_installed_file_no_distribution_owns_raises(self) -> None:
        tree = self._tree(
            {"main.py": "import lib\n", "site-packages/lib/__init__.py": ""},
            roots=("", "site-packages"),
        )
        with self.assertRaises(UnownedDependencyError):
            bundle(tree.path("main.py"))



class TestDistributionClosure(unittest.TestCase):
    def _site(self, files: dict[str, str]) -> None:
        tree = _Tree(files, roots=("site-packages",))
        self.addCleanup(tree.cleanup)

    def test_includes_transitive_dependencies(self) -> None:
        self._site(
            {
                **_dist_info("site-packages", "cg-a", "1.0", requires=("cg-b>=1",)),
                **_dist_info("site-packages", "cg-b", "1.0", requires=("cg-c",)),
                **_dist_info("site-packages", "cg-c", "1.0"),
            }
        )
        self.assertEqual(distribution_closure(["cg-a"]), {"cg-a", "cg-b", "cg-c"})

    def test_extra_dependencies_only_when_the_extra_is_requested(self) -> None:
        self._site(
            {
                **_dist_info(
                    "site-packages", "cg-a", "1.0", requires=('cg-s3; extra == "s3"',)
                ),
                **_dist_info("site-packages", "cg-s3", "1.0"),
            }
        )
        self.assertEqual(distribution_closure(["cg-a"]), {"cg-a"})
        self.assertEqual(distribution_closure(["cg-a[s3]"]), {"cg-a", "cg-s3"})

    def test_dependency_with_a_false_environment_marker_is_skipped(self) -> None:
        self._site(
            {
                **_dist_info(
                    "site-packages", "cg-a", "1.0", requires=('cg-old; python_version < "3"',)
                ),
                **_dist_info("site-packages", "cg-old", "1.0"),
            }
        )
        self.assertEqual(distribution_closure(["cg-a"]), {"cg-a"})

    def test_requirement_not_installed_contributes_only_its_name(self) -> None:
        self.assertEqual(
            distribution_closure(["cg-not-installed[extra]>=2"]), {"cg-not-installed"}
        )

    def test_names_are_canonical(self) -> None:
        self.assertEqual(distribution_closure(["CG_Mixed.Case"]), {"cg-mixed-case"})


class TestBundleDescMerge(unittest.TestCase):
    def test_merge_unions_local_files(self) -> None:
        a = BundleDesc(local_files={Path("/a.py"), Path("/shared.py")}, tp_deps={})
        b = BundleDesc(local_files={Path("/b.py"), Path("/shared.py")}, tp_deps={})
        self.assertEqual(
            a.merge(b).local_files, {Path("/a.py"), Path("/b.py"), Path("/shared.py")}
        )

    def test_merge_combines_distinct_tp_deps(self) -> None:
        a = BundleDesc(local_files=set(), tp_deps={"numpy": "1.26.4"})
        b = BundleDesc(local_files=set(), tp_deps={"requests": "2.31.0"})
        self.assertEqual(
            a.merge(b).tp_deps, {"numpy": "1.26.4", "requests": "2.31.0"}
        )

    def test_merge_tp_dep_present_in_both(self) -> None:
        # Both bundles resolve against the same environment, so a shared
        # dependency carries the same version on each side.
        a = BundleDesc(local_files=set(), tp_deps={"numpy": "1.26.4"})
        b = BundleDesc(local_files=set(), tp_deps={"numpy": "1.26.4"})
        self.assertEqual(a.merge(b).tp_deps, {"numpy": "1.26.4"})

    def test_merge_leaves_operands_unchanged(self) -> None:
        a = BundleDesc(local_files={Path("/a.py")}, tp_deps={"numpy": "1.26.4"})
        b = BundleDesc(local_files={Path("/b.py")}, tp_deps={"requests": "2.31.0"})
        a.merge(b)
        self.assertEqual(
            a, BundleDesc(local_files={Path("/a.py")}, tp_deps={"numpy": "1.26.4"})
        )
        self.assertEqual(
            b, BundleDesc(local_files={Path("/b.py")}, tp_deps={"requests": "2.31.0"})
        )


class TestBundleDescPipRequirements(unittest.TestCase):
    def test_pins_and_sorts(self) -> None:
        desc = BundleDesc(local_files=set(), tp_deps={"tqdm": "4.67.3", "haikunator": "2.1.0"})
        self.assertEqual(
            desc.pip_requirements(frozenset()), ["haikunator==2.1.0", "tqdm==4.67.3"]
        )

    def test_excludes_provided_distributions(self) -> None:
        desc = BundleDesc(local_files=set(), tp_deps={"tqdm": "4.67.3", "ray": "2.55.1"})
        self.assertEqual(desc.pip_requirements(frozenset({"ray"})), ["tqdm==4.67.3"])


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

    def test_stage_creates_dest_even_with_no_files(self) -> None:
        root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, root, True)
        dest = root / "code"

        stage(set(), dest)

        self.assertTrue(dest.is_dir())


class TestDigest(unittest.TestCase):
    def _tree(self, files: dict[str, str]) -> Path:
        root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, root, True)
        for rel, content in files.items():
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        return root

    def _digest(self, root: Path, rels: tuple[str, ...]) -> str:
        return digest({(root / rel).resolve() for rel in rels})

    def test_same_layout_digests_the_same_wherever_it_lives(self) -> None:
        files = {"app/__init__.py": "", "app/main.py": "x = 1\n"}

        first, second = self._tree(files), self._tree(files)

        self.assertEqual(
            self._digest(first, tuple(files)), self._digest(second, tuple(files))
        )

    def test_changed_content_changes_the_digest(self) -> None:
        rels = ("app/__init__.py", "app/main.py")
        before = self._tree({"app/__init__.py": "", "app/main.py": "x = 1\n"})
        after = self._tree({"app/__init__.py": "", "app/main.py": "x = 2\n"})

        self.assertNotEqual(self._digest(before, rels), self._digest(after, rels))

    def test_changed_import_path_changes_the_digest(self) -> None:
        before = self._tree({"app/__init__.py": "", "app/main.py": "x = 1\n"})
        after = self._tree({"app/__init__.py": "", "app/other.py": "x = 1\n"})

        self.assertNotEqual(
            self._digest(before, ("app/__init__.py", "app/main.py")),
            self._digest(after, ("app/__init__.py", "app/other.py")),
        )


if __name__ == "__main__":
    unittest.main()
