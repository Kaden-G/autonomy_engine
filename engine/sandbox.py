"""Sandbox — isolated temporary workspace for running verification commands.

Copies the generated project into a temp directory, creates a
virtualenv, optionally installs dependencies, and cleans up after.
Host files are never modified by sandboxed execution.
"""

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time as _time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import yaml

from engine.context import get_config_path, get_state_dir


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
        meta = {
            "sandboxed": True,
            "workspace": str(self.workspace),
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "venv": str(self.venv_path) if self.venv_path else None,
            "deps_installed": self._deps_installed,
        }
        if hasattr(self, "_venv_cache_hit"):
            meta["venv_cache_hit"] = self._venv_cache_hit
            meta["venv_cache_key"] = self._venv_cache_key
            meta["venv_create_time_s"] = self._venv_create_time
            meta["deps_install_time_s"] = self._deps_install_time
        return meta


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
    Uses a shared pip cache to speed up repeated installs.
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

    pip_cache_dir = str(get_state_dir() / "sandbox_cache" / "pip")
    env = os.environ.copy()
    env["PIP_CACHE_DIR"] = pip_cache_dir

    try:
        subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
            cwd=str(workspace),
            env=env,
        )
        return True
    except (subprocess.SubprocessError, OSError):
        return False


# ── Venv caching ────────────────────────────────────────────────────────────


def _compute_venv_cache_key(workspace: Path, sandbox_cfg: dict) -> str:
    """Compute a cache key for the venv based on deps spec + python version + config."""
    h = hashlib.sha256()

    req = workspace / "requirements.txt"
    pyproj = workspace / "pyproject.toml"
    if req.exists():
        h.update(b"requirements.txt:")
        h.update(req.read_bytes())
    elif pyproj.exists():
        h.update(b"pyproject.toml:")
        h.update(pyproj.read_bytes())
    else:
        h.update(b"no-deps")

    h.update(f":python={platform.python_version()}".encode())

    cfg_str = json.dumps(sandbox_cfg, sort_keys=True, separators=(",", ":"))
    h.update(f":config={cfg_str}".encode())

    return h.hexdigest()


def _get_venv_cache_dir() -> Path:
    """Return the venv cache directory: state/sandbox_cache/venvs/."""
    cache_dir = get_state_dir() / "sandbox_cache" / "venvs"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _try_reuse_venv(cache_key: str, workspace: Path) -> tuple[Path | None, bool]:
    """Try to reuse a cached venv. Returns (venv_path, cache_hit)."""
    cache_dir = _get_venv_cache_dir()
    cached_venv = cache_dir / cache_key

    if cached_venv.exists() and (cached_venv / "bin" / "python").exists():
        dest = workspace / ".venv"
        shutil.copytree(cached_venv, dest)
        return dest, True

    return None, False


def _save_venv_to_cache(cache_key: str, venv_path: Path) -> None:
    """Save a venv to the cache directory (best-effort, skip if exists)."""
    cache_dir = _get_venv_cache_dir()
    dest = cache_dir / cache_key
    if dest.exists():
        return
    try:
        shutil.copytree(venv_path, dest)
    except OSError:
        pass


# ── Context manager ──────────────────────────────────────────────────────────


@contextmanager
def create_sandbox(
    project_dir: Path,
    install_deps: bool = True,
    sandbox_cfg: dict | None = None,
) -> Iterator[Sandbox]:
    """Copy *project_dir* into a temp workspace with its own virtualenv.

    If a cached venv exists for the same dependency spec, reuse it.
    Yields a :class:`Sandbox` whose ``.workspace`` is the isolated copy
    and whose ``.env`` activates the virtualenv.  The temp directory is
    deleted when the context manager exits.
    """
    tmpdir = tempfile.mkdtemp(prefix="ae_sandbox_")
    workspace = Path(tmpdir) / "project"

    try:
        shutil.copytree(project_dir, workspace)

        venv_cache_hit = False
        venv_create_time = 0.0
        deps_install_time = 0.0
        deps_installed = False

        cfg = sandbox_cfg or {}
        cache_key = _compute_venv_cache_key(workspace, cfg)

        # Try reuse cached venv
        t0 = _time.monotonic()
        venv_path, venv_cache_hit = _try_reuse_venv(cache_key, workspace)
        if venv_cache_hit:
            venv_create_time = _time.monotonic() - t0
            deps_installed = True
        else:
            # Create fresh venv
            t0 = _time.monotonic()
            venv_path = _create_venv(workspace)
            venv_create_time = _time.monotonic() - t0

            if venv_path and install_deps:
                t1 = _time.monotonic()
                deps_installed = _install_deps(workspace, venv_path)
                deps_install_time = _time.monotonic() - t1

            # Cache the venv after creation (with or without deps)
            if venv_path:
                _save_venv_to_cache(cache_key, venv_path)

        sb = Sandbox(
            workspace=workspace,
            venv_path=venv_path,
            deps_installed=deps_installed,
        )
        sb._venv_cache_hit = venv_cache_hit
        sb._venv_cache_key = cache_key
        sb._venv_create_time = round(venv_create_time, 3)
        sb._deps_install_time = round(deps_install_time, 3)

        yield sb
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
