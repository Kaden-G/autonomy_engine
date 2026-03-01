"""Sandbox — isolated temporary workspace for running verification commands.

Copies the generated project into a temp directory, creates a
virtualenv, optionally installs dependencies, and cleans up after.
Host files are never modified by sandboxed execution.
"""

import os
import platform
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import yaml

from engine.context import get_config_path


# ── Config ───────────────────────────────────────────────────────────────────

def load_sandbox_config() -> dict:
    """Load the ``sandbox`` section from ``config.yml``.

    Returns an empty dict if the section or file is missing.
    """
    config_path = get_config_path()
    if not config_path.exists():
        return {}
    with open(config_path) as f:
        config = yaml.safe_load(f) or {}
    return config.get("sandbox") or {}


# ── Host metadata (non-sandboxed fallback) ───────────────────────────────────

def collect_host_metadata() -> dict:
    """Return environment metadata for evidence when running without a sandbox."""
    return {
        "sandboxed": False,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
    }


# ── Sandbox class ────────────────────────────────────────────────────────────

class Sandbox:
    """An isolated workspace with its own virtualenv.

    Created by :func:`create_sandbox`.  Use ``env`` when calling
    ``subprocess.run`` so the sandboxed Python/pip are on ``PATH``.
    """

    def __init__(
        self,
        workspace: Path,
        venv_path: Path | None,
        deps_installed: bool,
    ):
        self.workspace = workspace
        self.venv_path = venv_path
        self._deps_installed = deps_installed

    @property
    def env(self) -> dict:
        """Return a copy of ``os.environ`` with the venv activated."""
        env = os.environ.copy()
        if self.venv_path:
            venv_bin = str(self.venv_path / "bin")
            env["PATH"] = venv_bin + os.pathsep + env.get("PATH", "")
            env["VIRTUAL_ENV"] = str(self.venv_path)
            env.pop("PYTHONHOME", None)
        return env

    def metadata(self) -> dict:
        """Return environment metadata for evidence records."""
        return {
            "sandboxed": True,
            "workspace": str(self.workspace),
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "venv": str(self.venv_path) if self.venv_path else None,
            "deps_installed": self._deps_installed,
        }


# ── Internal helpers ─────────────────────────────────────────────────────────

def _create_venv(workspace: Path) -> Path | None:
    """Create a virtualenv at ``<workspace>/.venv``.

    Returns the venv path on success, ``None`` on failure.
    """
    venv_path = workspace / ".venv"
    try:
        subprocess.run(
            [sys.executable, "-m", "venv", str(venv_path)],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        return venv_path
    except (subprocess.SubprocessError, OSError):
        return None


def _install_deps(workspace: Path, venv_path: Path) -> bool:
    """Install dependencies using the sandboxed pip.

    Tries ``requirements.txt`` first, then ``pyproject.toml``.
    Returns ``True`` if installation succeeded.
    """
    python = str(venv_path / "bin" / "python")

    requirements = workspace / "requirements.txt"
    pyproject = workspace / "pyproject.toml"

    if requirements.exists():
        cmd = [python, "-m", "pip", "install", "-q", "-r", str(requirements)]
    elif pyproject.exists():
        cmd = [python, "-m", "pip", "install", "-q", str(workspace)]
    else:
        return False

    try:
        subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
            cwd=str(workspace),
        )
        return True
    except (subprocess.SubprocessError, OSError):
        return False


# ── Context manager ──────────────────────────────────────────────────────────

@contextmanager
def create_sandbox(
    project_dir: Path,
    install_deps: bool = True,
) -> Iterator[Sandbox]:
    """Copy *project_dir* into a temp workspace with its own virtualenv.

    Yields a :class:`Sandbox` whose ``.workspace`` is the isolated copy
    and whose ``.env`` activates the virtualenv.  The temp directory is
    deleted when the context manager exits.
    """
    tmpdir = tempfile.mkdtemp(prefix="ae_sandbox_")
    workspace = Path(tmpdir) / "project"

    try:
        shutil.copytree(project_dir, workspace)

        venv_path = _create_venv(workspace)

        deps_installed = False
        if venv_path and install_deps:
            deps_installed = _install_deps(workspace, venv_path)

        yield Sandbox(
            workspace=workspace,
            venv_path=venv_path,
            deps_installed=deps_installed,
        )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
