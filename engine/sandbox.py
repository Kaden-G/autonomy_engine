"""Sandbox — run AI-generated code in an isolated workspace, not your real files.

When the engine tests AI-generated code, it doesn't run it in-place.  Instead,
this module copies the project into a temporary directory with its own isolated
environment (Python virtualenv or Node.js node_modules), optionally installs
dependencies, runs the tests there, and cleans up afterward.

Backends (selectable via ``sandbox.backend`` in ``config.yml``):
  * ``local`` — default.  Copies project to a host tempdir + venv / node_modules.
    Provides filesystem isolation only; checks run as the host user with full
    network access.  Suitable for trusted specs / dev-loop speed.
  * ``docker`` — runs each check inside an ephemeral container with network
    dropped, read-only root, non-root UID, tmpfs /tmp, CPU + memory caps.
    Suitable for less-trusted specs and the hosted demo.  See
    :mod:`engine.sandbox_docker`.

This means the AI's code can't accidentally overwrite engine files or pollute
your system's installed packages.  See the Security Model in README.md for
what this does and doesn't protect against.
"""

import hashlib
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time as _time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import yaml

from engine.context import get_config_path, get_state_dir

logger = logging.getLogger(__name__)


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
    """An isolated workspace with its own virtualenv or node_modules.

    Created by :func:`create_sandbox`.  Call :meth:`run` to execute a check
    inside the sandbox — backends (local, docker) override ``run`` but share
    the rest of the interface.  ``env`` is exposed for backward compatibility
    with code that still calls ``subprocess.run`` directly; new callers should
    use :meth:`run` so the backend can dispatch correctly.
    """

    backend: str = "local"

    def __init__(
        self,
        workspace: Path,
        venv_path: Path | None,
        deps_installed: bool,
        project_type: str = "python",
    ):
        self.workspace = workspace
        self.venv_path = venv_path
        self._deps_installed = deps_installed
        self.project_type = project_type

    @property
    def env(self) -> dict:
        """Return a copy of ``os.environ`` with the sandbox runtime activated."""
        env = os.environ.copy()
        if self.project_type == "node":
            # Add node_modules/.bin to PATH for npx-style tool access
            node_bin = str(self.workspace / "node_modules" / ".bin")
            env["PATH"] = node_bin + os.pathsep + env.get("PATH", "")
        elif self.venv_path:
            venv_bin = str(self.venv_path / "bin")
            env["PATH"] = venv_bin + os.pathsep + env.get("PATH", "")
            env["VIRTUAL_ENV"] = str(self.venv_path)
            env.pop("PYTHONHOME", None)
        return env

    def run(self, command: str, timeout: int = 300) -> dict:
        """Execute *command* inside the sandbox.

        Returns a dict with keys: ``exit_code``, ``stdout``, ``stderr``,
        ``started_at``, ``finished_at``.  The caller (``run_check``) wraps
        this with the command name, argv, cwd, and output hashes.

        Local backend: runs via ``subprocess.run(shell=True)`` on the host
        with the sandbox runtime on PATH.  Docker backend overrides this.
        """
        started_at = datetime.now(timezone.utc).isoformat()
        stdout = ""
        stderr = ""
        exit_code = -1

        try:
            result = subprocess.run(
                command,
                shell=True,  # nosec B602 — commands come from config.yml, never from AI output
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(self.workspace),
                env=self.env,
            )
            exit_code = result.returncode
            stdout = result.stdout
            stderr = result.stderr
        except subprocess.TimeoutExpired as exc:
            stdout = (
                (exc.stdout or b"").decode(errors="replace")
                if isinstance(exc.stdout, bytes)
                else (exc.stdout or "")
            )
            stderr = f"Command timed out after {timeout} seconds: {command}"
        except OSError as exc:
            stderr = f"Failed to execute command: {exc}"

        return {
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }

    def metadata(self) -> dict:
        """Return environment metadata for evidence records."""
        meta = {
            "sandboxed": True,
            "backend": self.backend,
            "workspace": str(self.workspace),
            "project_type": self.project_type,
            "platform": platform.platform(),
            "deps_installed": self._deps_installed,
        }
        if self.project_type == "node":
            node_ver = _find_node_version(self.workspace)
            if node_ver:
                meta["node_version"] = node_ver
        else:
            meta["python_version"] = platform.python_version()
            meta["venv"] = str(self.venv_path) if self.venv_path else None

        if hasattr(self, "_venv_cache_hit"):
            meta["venv_cache_hit"] = self._venv_cache_hit
            meta["venv_cache_key"] = self._venv_cache_key
            meta["venv_create_time_s"] = self._venv_create_time
            meta["deps_install_time_s"] = self._deps_install_time
        return meta


# ── Project type detection ────────────────────────────────────────────────────


def detect_project_type(workspace: Path) -> str:
    """Detect the project runtime from files in *workspace*.

    Returns ``"node"``, ``"python"``, or ``"unknown"``.
    ``package.json`` takes precedence when both Node and Python files exist
    (same priority rule as ``auto_detect_checks``).
    """
    if (workspace / "package.json").exists():
        return "node"
    if any((workspace / f).exists() for f in ("requirements.txt", "pyproject.toml", "setup.py")):
        return "python"
    return "unknown"


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


def _install_node_deps(workspace: Path, sandbox_cfg: dict) -> bool:
    """Install Node.js dependencies via ``npm install`` in *workspace*.

    Uses a shared npm cache to speed up repeated installs.
    Returns ``True`` if installation succeeded.
    """
    pkg = workspace / "package.json"
    if not pkg.exists():
        return False

    npm_cache_dir = str(get_state_dir() / "sandbox_cache" / "npm")
    env = os.environ.copy()
    env["npm_config_cache"] = npm_cache_dir

    try:
        subprocess.run(
            ["npm", "install", "--no-audit", "--no-fund"],
            capture_output=True,
            text=True,
            timeout=180,  # npm can be slow
            check=True,
            cwd=str(workspace),
            env=env,
        )
        return True
    except FileNotFoundError:
        logger.warning(
            "npm not found on PATH — cannot install Node.js dependencies. "
            "Install Node.js (https://nodejs.org) to enable Node.js sandbox support."
        )
        return False
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("npm install failed: %s", exc)
        return False


def _find_node_version(workspace: Path) -> str | None:
    """Return the Node.js version string, or None if not available."""
    try:
        result = subprocess.run(
            ["node", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(workspace),
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.SubprocessError):
        pass
    return None


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


# Default TTL for cached venvs (in days).
# Override via config.yml: cache.venv_ttl_days
DEFAULT_VENV_CACHE_TTL_DAYS = 7


def evict_stale_venv_cache(ttl_days: int = DEFAULT_VENV_CACHE_TTL_DAYS) -> int:
    """Delete cached venvs older than *ttl_days*.

    Uses filesystem mtime as the age indicator — when a cached venv is
    copied out by ``_try_reuse_venv``, the destination gets a fresh
    mtime, but the source in the cache dir retains its original mtime.
    This means unused venvs age out naturally, while actively reused
    ones stay until their deps spec changes (which generates a new
    cache key anyway).

    Returns the number of venv directories deleted.
    """
    cache_dir = _get_venv_cache_dir()
    if not cache_dir.exists():
        return 0

    now = _time.time()
    ttl_seconds = ttl_days * 86400
    deleted = 0

    for entry in cache_dir.iterdir():
        if not entry.is_dir():
            continue
        try:
            age = now - entry.stat().st_mtime
            if age > ttl_seconds:
                shutil.rmtree(entry, ignore_errors=True)
                deleted += 1
        except OSError:
            pass  # Best-effort — skip entries we can't stat or delete

    if deleted:
        logger.info(
            "Venv cache eviction: removed %d cached venvs older than %d days",
            deleted,
            ttl_days,
        )
    return deleted


# ── Context manager ──────────────────────────────────────────────────────────


@contextmanager
def create_sandbox(
    project_dir: Path,
    install_deps: bool = True,
    sandbox_cfg: dict | None = None,
) -> Iterator[Sandbox]:
    """Copy *project_dir* into a temp workspace with the appropriate runtime.

    Detects project type (Node.js or Python) and sets up either
    ``node_modules`` or a virtualenv accordingly.

    Backend is selected via ``sandbox_cfg["backend"]`` (default ``"local"``).
    The ``"docker"`` backend runs each check inside an ephemeral container —
    see :mod:`engine.sandbox_docker`.  Node.js is supported only by the
    ``local`` backend in Phase 1.

    If a cached venv exists for the same dependency spec, reuse it (local
    backend only).  Yields a :class:`Sandbox` whose ``.workspace`` is the
    isolated copy.  The temp directory is deleted when the context manager
    exits.
    """
    cfg = sandbox_cfg or {}
    backend = (cfg.get("backend") or "local").lower()

    tmpdir = tempfile.mkdtemp(prefix="ae_sandbox_")
    workspace = Path(tmpdir) / "project"

    try:
        shutil.copytree(project_dir, workspace)

        project_type = detect_project_type(workspace)
        logger.info("Sandbox project type detected: %s (backend=%s)", project_type, backend)

        if backend == "docker":
            if project_type == "node":
                logger.warning(
                    "Docker backend does not yet support Node.js projects; falling back to local."
                )
            else:
                from engine.sandbox_docker import setup_docker_sandbox

                sb = setup_docker_sandbox(workspace, install_deps, cfg)
                yield sb
                return

        if project_type == "node":
            sb = _setup_node_sandbox(workspace, install_deps, cfg)
        else:
            sb = _setup_python_sandbox(workspace, install_deps, cfg)

        yield sb
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _setup_node_sandbox(
    workspace: Path,
    install_deps: bool,
    cfg: dict,
) -> Sandbox:
    """Set up a Node.js sandbox — npm install in the workspace."""
    deps_installed = False
    deps_install_time = 0.0

    if install_deps:
        t0 = _time.monotonic()
        deps_installed = _install_node_deps(workspace, cfg)
        deps_install_time = _time.monotonic() - t0

    sb = Sandbox(
        workspace=workspace,
        venv_path=None,
        deps_installed=deps_installed,
        project_type="node",
    )
    sb._deps_install_time = round(deps_install_time, 3)
    return sb


def _setup_python_sandbox(
    workspace: Path,
    install_deps: bool,
    cfg: dict,
) -> Sandbox:
    """Set up a Python sandbox — virtualenv + pip install."""
    venv_cache_hit = False
    venv_create_time = 0.0
    deps_install_time = 0.0
    deps_installed = False

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
        project_type="python",
    )
    sb._venv_cache_hit = venv_cache_hit
    sb._venv_cache_key = cache_key
    sb._venv_create_time = round(venv_create_time, 3)
    sb._deps_install_time = round(deps_install_time, 3)
    return sb
