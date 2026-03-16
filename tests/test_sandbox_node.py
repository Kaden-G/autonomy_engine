"""Tests for engine.sandbox — Node.js project support."""

import json
import subprocess
from pathlib import Path

import pytest

import engine.context
import engine.tracer as tracer
from engine.evidence import run_check
from engine.sandbox import (
    create_sandbox,
    detect_project_type,
)
from engine.tracer import GENESIS_HASH

pytestmark = pytest.mark.slow


@pytest.fixture(autouse=True)
def _isolated_context(tmp_path):
    """Point engine context at a temp dir and reset tracer state."""
    engine.context.init(tmp_path)
    (tmp_path / "state").mkdir()
    tracer._run_id = None
    tracer._prev_hash = GENESIS_HASH
    tracer._seq = 0
    yield
    tracer._run_id = None
    tracer._prev_hash = GENESIS_HASH
    tracer._seq = 0


def _make_project(tmp_path, files: dict[str, str] | None = None) -> Path:
    """Create a minimal project directory with given files."""
    project = tmp_path / "project"
    project.mkdir()
    if files:
        for name, content in files.items():
            p = project / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
    return project


def _npm_available() -> bool:
    """Check if npm is available on the system."""
    try:
        result = subprocess.run(
            ["npm", "--version"], capture_output=True, text=True, timeout=10
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.SubprocessError):
        return False


# ── Project type detection ───────────────────────────────────────────────────


class TestDetectProjectType:
    def test_node_project(self, tmp_path):
        project = _make_project(tmp_path, {"package.json": "{}"})
        assert detect_project_type(project) == "node"

    def test_python_requirements(self, tmp_path):
        project = _make_project(tmp_path, {"requirements.txt": "flask\n"})
        assert detect_project_type(project) == "python"

    def test_python_pyproject(self, tmp_path):
        project = _make_project(tmp_path, {"pyproject.toml": "[project]\n"})
        assert detect_project_type(project) == "python"

    def test_python_setup_py(self, tmp_path):
        project = _make_project(tmp_path, {"setup.py": "from setuptools import setup"})
        assert detect_project_type(project) == "python"

    def test_unknown_no_files(self, tmp_path):
        project = _make_project(tmp_path, {"README.md": "hi"})
        assert detect_project_type(project) == "unknown"

    def test_node_takes_precedence_over_python(self, tmp_path):
        project = _make_project(
            tmp_path,
            {"package.json": "{}", "requirements.txt": "flask\n"},
        )
        assert detect_project_type(project) == "node"

    def test_empty_directory(self, tmp_path):
        project = _make_project(tmp_path)
        assert detect_project_type(project) == "unknown"


# ── Node.js sandbox ─────────────────────────────────────────────────────────


@pytest.mark.skipif(not _npm_available(), reason="npm not available")
class TestNodeSandbox:
    def test_sandbox_detects_node_project(self, tmp_path):
        pkg = {"name": "test-project", "version": "1.0.0"}
        project = _make_project(tmp_path, {"package.json": json.dumps(pkg)})
        with create_sandbox(project, install_deps=False) as sb:
            assert sb.project_type == "node"

    def test_no_venv_for_node(self, tmp_path):
        pkg = {"name": "test-project", "version": "1.0.0"}
        project = _make_project(tmp_path, {"package.json": json.dumps(pkg)})
        with create_sandbox(project, install_deps=False) as sb:
            assert sb.venv_path is None

    def test_node_modules_bin_on_path(self, tmp_path):
        pkg = {"name": "test-project", "version": "1.0.0"}
        project = _make_project(tmp_path, {"package.json": json.dumps(pkg)})
        with create_sandbox(project, install_deps=False) as sb:
            env = sb.env
            assert "node_modules/.bin" in env["PATH"]

    def test_npm_install_creates_node_modules(self, tmp_path):
        # Use a real (tiny) npm package to verify npm install works
        pkg = {
            "name": "test-project",
            "version": "1.0.0",
            "dependencies": {"is-odd": "3.0.1"},
        }
        project = _make_project(tmp_path, {"package.json": json.dumps(pkg)})
        with create_sandbox(project, install_deps=True) as sb:
            assert sb._deps_installed is True
            assert (sb.workspace / "node_modules").is_dir()
            assert (sb.workspace / "node_modules" / "is-odd").is_dir()

    def test_install_deps_false_skips_npm_install(self, tmp_path):
        pkg = {
            "name": "test-project",
            "version": "1.0.0",
            "dependencies": {"is-odd": "3.0.1"},
        }
        project = _make_project(tmp_path, {"package.json": json.dumps(pkg)})
        with create_sandbox(project, install_deps=False) as sb:
            assert sb._deps_installed is False
            assert not (sb.workspace / "node_modules").exists()

    def test_node_metadata_fields(self, tmp_path):
        pkg = {"name": "test-project", "version": "1.0.0"}
        project = _make_project(tmp_path, {"package.json": json.dumps(pkg)})
        with create_sandbox(project, install_deps=False) as sb:
            meta = sb.metadata()
            assert meta["sandboxed"] is True
            assert meta["project_type"] == "node"
            assert "node_version" in meta
            # Should NOT have python-specific fields
            assert "venv" not in meta

    def test_check_runs_in_node_sandbox(self, tmp_path):
        pkg = {"name": "test-project", "version": "1.0.0"}
        project = _make_project(
            tmp_path,
            {
                "package.json": json.dumps(pkg),
                "index.js": "console.log('hello from node');",
            },
        )
        with create_sandbox(project, install_deps=False) as sb:
            record = run_check(
                "run_node",
                "node index.js",
                cwd=sb.workspace,
                env=sb.env,
            )
            assert record["exit_code"] == 0
            assert "hello from node" in record["stdout"]

    def test_workspace_cleaned_up(self, tmp_path):
        pkg = {"name": "test-project", "version": "1.0.0"}
        project = _make_project(tmp_path, {"package.json": json.dumps(pkg)})
        with create_sandbox(project, install_deps=False) as sb:
            workspace_path = sb.workspace
        assert not workspace_path.exists()


# ── Python projects still work ───────────────────────────────────────────────


class TestPythonStillWorks:
    """Verify that Python projects still get virtualenv treatment."""

    def test_python_project_gets_venv(self, tmp_path):
        project = _make_project(tmp_path, {"app.py": "print('hi')"})
        with create_sandbox(project, install_deps=False) as sb:
            assert sb.project_type == "python"
            assert sb.venv_path is not None

    def test_python_metadata_has_python_fields(self, tmp_path):
        project = _make_project(tmp_path, {"app.py": "x = 1"})
        with create_sandbox(project, install_deps=False) as sb:
            meta = sb.metadata()
            assert meta["project_type"] == "python"
            assert "python_version" in meta
            assert "venv" in meta
