"""Tests for engine.sandbox — isolated workspace execution."""

import subprocess
from pathlib import Path

import pytest

import engine.context
import engine.tracer as tracer
from engine.evidence import run_check
from engine.sandbox import (
    collect_host_metadata,
    create_sandbox,
    load_sandbox_config,
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


# ── create_sandbox: workspace lifecycle ─────────────────────────────────────


class TestSandboxLifecycle:
    def test_workspace_exists_inside_context(self, tmp_path):
        project = _make_project(tmp_path, {"app.py": "print('hi')"})
        with create_sandbox(project, install_deps=False) as sb:
            assert sb.workspace.is_dir()

    def test_workspace_cleaned_up_after_context(self, tmp_path):
        project = _make_project(tmp_path, {"app.py": "x = 1"})
        with create_sandbox(project, install_deps=False) as sb:
            workspace_path = sb.workspace
        assert not workspace_path.exists()

    def test_cleanup_on_exception(self, tmp_path):
        project = _make_project(tmp_path, {"app.py": "x = 1"})
        workspace_path = None
        with pytest.raises(RuntimeError):
            with create_sandbox(project, install_deps=False) as sb:
                workspace_path = sb.workspace
                raise RuntimeError("boom")
        assert not workspace_path.exists()


# ── File isolation ──────────────────────────────────────────────────────────


class TestFileIsolation:
    def test_project_files_copied(self, tmp_path):
        project = _make_project(
            tmp_path,
            {
                "app.py": "print('hello')",
                "src/lib.py": "x = 42",
            },
        )
        with create_sandbox(project, install_deps=False) as sb:
            assert (sb.workspace / "app.py").read_text() == "print('hello')"
            assert (sb.workspace / "src" / "lib.py").read_text() == "x = 42"

    def test_host_files_not_modified(self, tmp_path):
        project = _make_project(tmp_path, {"data.txt": "original"})
        with create_sandbox(project, install_deps=False) as sb:
            # Modify file in sandbox
            (sb.workspace / "data.txt").write_text("modified")
            # Create new file in sandbox
            (sb.workspace / "new_file.txt").write_text("new")
        # Host project is untouched
        assert (project / "data.txt").read_text() == "original"
        assert not (project / "new_file.txt").exists()

    def test_command_runs_in_workspace_not_host(self, tmp_path):
        project = _make_project(tmp_path, {"marker.txt": "original"})
        with create_sandbox(project, install_deps=False) as sb:
            # Write a file via shell command in the sandbox
            record = run_check(
                "write_test",
                "echo sandbox_output > output.txt",
                cwd=sb.workspace,
            )
            assert record["exit_code"] == 0
            assert (sb.workspace / "output.txt").exists()
        # Host project has no output.txt
        assert not (project / "output.txt").exists()


# ── Virtualenv creation ─────────────────────────────────────────────────────


class TestVenvCreation:
    def test_venv_created(self, tmp_path):
        project = _make_project(tmp_path)
        with create_sandbox(project, install_deps=False) as sb:
            assert sb.venv_path is not None
            assert (sb.venv_path / "bin" / "python").exists()

    def test_venv_python_is_functional(self, tmp_path):
        project = _make_project(tmp_path)
        with create_sandbox(project, install_deps=False) as sb:
            result = subprocess.run(
                [str(sb.venv_path / "bin" / "python"), "-c", "print('ok')"],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0
            assert "ok" in result.stdout

    def test_env_has_venv_on_path(self, tmp_path):
        project = _make_project(tmp_path)
        with create_sandbox(project, install_deps=False) as sb:
            env = sb.env
            assert str(sb.venv_path / "bin") in env["PATH"]
            assert env["VIRTUAL_ENV"] == str(sb.venv_path)

    def test_sandboxed_python_differs_from_host_env(self, tmp_path):
        project = _make_project(tmp_path)
        with create_sandbox(project, install_deps=False) as sb:
            # Running "which python" in sandbox env should point to the venv
            record = run_check("which_py", "which python", cwd=sb.workspace, env=sb.env)
            assert record["exit_code"] == 0
            assert ".venv" in record["stdout"]


# ── Dependency installation ─────────────────────────────────────────────────


class TestDepsInstallation:
    def test_deps_installed_from_requirements_txt(self, tmp_path):
        project = _make_project(
            tmp_path,
            {
                "requirements.txt": "pip-install-test==0.5\n",
            },
        )
        with create_sandbox(project, install_deps=True) as sb:
            assert sb._deps_installed is True

    def test_no_deps_file_means_not_installed(self, tmp_path):
        project = _make_project(tmp_path, {"app.py": "x = 1"})
        with create_sandbox(project, install_deps=True) as sb:
            assert sb._deps_installed is False

    def test_install_deps_false_skips(self, tmp_path):
        project = _make_project(
            tmp_path,
            {
                "requirements.txt": "pip-install-test==0.5\n",
            },
        )
        with create_sandbox(project, install_deps=False) as sb:
            assert sb._deps_installed is False


# ── Metadata ────────────────────────────────────────────────────────────────


class TestMetadata:
    def test_sandbox_metadata_fields(self, tmp_path):
        project = _make_project(tmp_path)
        with create_sandbox(project, install_deps=False) as sb:
            meta = sb.metadata()
            assert meta["sandboxed"] is True
            assert "workspace" in meta
            assert "python_version" in meta
            assert "platform" in meta
            assert "venv" in meta
            assert "deps_installed" in meta

    def test_host_metadata_not_sandboxed(self):
        meta = collect_host_metadata()
        assert meta["sandboxed"] is False
        assert "python_version" in meta
        assert "platform" in meta

    def test_metadata_workspace_matches_sandbox(self, tmp_path):
        project = _make_project(tmp_path)
        with create_sandbox(project, install_deps=False) as sb:
            meta = sb.metadata()
            assert meta["workspace"] == str(sb.workspace)


# ── Config loading ──────────────────────────────────────────────────────────


class TestLoadSandboxConfig:
    def test_returns_empty_when_no_config(self, tmp_path):
        assert load_sandbox_config() == {}

    def test_returns_empty_when_no_sandbox_section(self, tmp_path):
        (tmp_path / "config.yml").write_text("llm:\n  provider: claude\n")
        assert load_sandbox_config() == {}

    def test_returns_config(self, tmp_path):
        (tmp_path / "config.yml").write_text("sandbox:\n  enabled: false\n  install_deps: false\n")
        cfg = load_sandbox_config()
        assert cfg["enabled"] is False
        assert cfg["install_deps"] is False

    def test_defaults_enabled_true(self, tmp_path):
        (tmp_path / "config.yml").write_text("sandbox:\n  enabled: true\n")
        cfg = load_sandbox_config()
        assert cfg["enabled"] is True


# ── run_check with sandbox env ──────────────────────────────────────────────


class TestRunCheckWithSandboxEnv:
    def test_check_uses_sandbox_python(self, tmp_path):
        project = _make_project(tmp_path, {"app.py": "print('hello')"})
        with create_sandbox(project, install_deps=False) as sb:
            record = run_check(
                "run_app",
                "python app.py",
                cwd=sb.workspace,
                env=sb.env,
            )
            assert record["exit_code"] == 0
            assert "hello" in record["stdout"]

    def test_check_cwd_is_workspace(self, tmp_path):
        project = _make_project(tmp_path)
        with create_sandbox(project, install_deps=False) as sb:
            record = run_check("pwd", "pwd", cwd=sb.workspace, env=sb.env)
            assert record["exit_code"] == 0
            assert str(sb.workspace) in record["stdout"]
