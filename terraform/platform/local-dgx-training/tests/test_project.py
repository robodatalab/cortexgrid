from __future__ import annotations

import os
import tempfile
import textwrap
import unittest
from pathlib import Path

from cortexflow.project import (
    DEFAULT_EXCLUDES,
    _source_to_pip,
    build_runtime_env,
    find_pyproject,
    read_pyproject,
)


class TestFindPyproject(unittest.TestCase):
    def test_finds_in_current_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pyproject = Path(tmp) / "pyproject.toml"
            pyproject.write_text("[project]\nname = 'test'\n")
            result = find_pyproject(Path(tmp))
            self.assertEqual(result, pyproject)

    def test_finds_in_parent_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pyproject = Path(tmp) / "pyproject.toml"
            pyproject.write_text("[project]\nname = 'test'\n")
            child = Path(tmp) / "src" / "deep"
            child.mkdir(parents=True)
            result = find_pyproject(child)
            self.assertEqual(result, pyproject)

    def test_raises_when_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                find_pyproject(Path(tmp))


class TestReadPyproject(unittest.TestCase):
    def test_reads_valid_toml(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".toml", mode="wb", delete=False) as f:
            f.write(b'[project]\nname = "hello"\n')
            f.flush()
            data = read_pyproject(Path(f.name))
            self.assertEqual(data["project"]["name"], "hello")
            os.unlink(f.name)


class TestSourceToPip(unittest.TestCase):
    def test_git_source(self) -> None:
        result = _source_to_pip("my-pkg", {"git": "https://github.com/org/repo.git"})
        self.assertEqual(result, "my-pkg @ git+https://github.com/org/repo.git")

    def test_git_source_with_subdirectory(self) -> None:
        result = _source_to_pip("my-pkg", {
            "git": "https://github.com/org/repo.git",
            "subdirectory": "libs/pkg",
        })
        self.assertEqual(
            result,
            "my-pkg @ git+https://github.com/org/repo.git#subdirectory=libs/pkg",
        )

    def test_path_source(self) -> None:
        result = _source_to_pip("my-pkg", {"path": "/tmp/my-pkg"})
        self.assertEqual(result, "my-pkg @ file:///tmp/my-pkg")

    def test_unknown_source_returns_name(self) -> None:
        result = _source_to_pip("my-pkg", {"url": "https://example.com"})
        self.assertEqual(result, "my-pkg")


class TestBuildRuntimeEnv(unittest.TestCase):
    def _write_pyproject(self, tmp: str, content: str) -> Path:
        p = Path(tmp) / "pyproject.toml"
        p.write_text(textwrap.dedent(content))
        return p

    def test_simple_deps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._write_pyproject(tmp, """\
                [project]
                name = "test"
                dependencies = [
                    "numpy>=1.26",
                    "pandas>=2.0",
                ]
            """)
            old_cwd = os.getcwd()
            os.chdir(tmp)
            try:
                env = build_runtime_env()
            finally:
                os.chdir(old_cwd)

            self.assertEqual(
                os.path.realpath(env["working_dir"]),
                os.path.realpath(tmp),
            )
            self.assertIn("numpy>=1.26", env["pip"])
            self.assertIn("pandas>=2.0", env["pip"])
            self.assertEqual(env["excludes"], DEFAULT_EXCLUDES)

    def test_deps_with_uv_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._write_pyproject(tmp, """\
                [project]
                name = "test"
                dependencies = [
                    "numpy>=1.26",
                    "my-lib",
                ]

                [tool.uv.sources]
                my-lib = { git = "https://github.com/org/my-lib.git" }
            """)
            old_cwd = os.getcwd()
            os.chdir(tmp)
            try:
                env = build_runtime_env()
            finally:
                os.chdir(old_cwd)

            self.assertIn("numpy>=1.26", env["pip"])
            self.assertIn(
                "my-lib @ git+https://github.com/org/my-lib.git",
                env["pip"],
            )

    def test_uv_source_with_subdirectory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._write_pyproject(tmp, """\
                [project]
                name = "test"
                dependencies = ["cortexflow"]

                [tool.uv.sources]
                cortexflow = { git = "https://github.com/org/infra.git", subdirectory = "lib" }
            """)
            old_cwd = os.getcwd()
            os.chdir(tmp)
            try:
                env = build_runtime_env()
            finally:
                os.chdir(old_cwd)

            self.assertEqual(
                env["pip"],
                ["cortexflow @ git+https://github.com/org/infra.git#subdirectory=lib"],
            )

    def test_name_normalization(self) -> None:
        """Deps with hyphens should match sources with hyphens."""
        with tempfile.TemporaryDirectory() as tmp:
            self._write_pyproject(tmp, """\
                [project]
                name = "test"
                dependencies = ["model-gateway"]

                [tool.uv.sources]
                model-gateway = { git = "https://github.com/org/mg.git" }
            """)
            old_cwd = os.getcwd()
            os.chdir(tmp)
            try:
                env = build_runtime_env()
            finally:
                os.chdir(old_cwd)

            self.assertIn(
                "model-gateway @ git+https://github.com/org/mg.git",
                env["pip"],
            )

    def test_deps_with_version_specifiers_match_sources(self) -> None:
        """A dep like 'my-lib>=1.0' should still match the source for 'my-lib'."""
        with tempfile.TemporaryDirectory() as tmp:
            self._write_pyproject(tmp, """\
                [project]
                name = "test"
                dependencies = ["my-lib>=1.0"]

                [tool.uv.sources]
                my-lib = { git = "https://github.com/org/ml.git" }
            """)
            old_cwd = os.getcwd()
            os.chdir(tmp)
            try:
                env = build_runtime_env()
            finally:
                os.chdir(old_cwd)

            self.assertIn(
                "my-lib @ git+https://github.com/org/ml.git",
                env["pip"],
            )

    def test_no_dependencies_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._write_pyproject(tmp, """\
                [project]
                name = "test"
            """)
            old_cwd = os.getcwd()
            os.chdir(tmp)
            try:
                env = build_runtime_env()
            finally:
                os.chdir(old_cwd)

            self.assertEqual(env["pip"], [])


if __name__ == "__main__":
    unittest.main()
