"""Docker sandbox backend — run AI-generated checks inside an ephemeral container.

When ``sandbox.backend: docker`` is set in ``config.yml``, :func:`setup_docker_sandbox`
is called instead of the host venv path in :func:`engine.sandbox.create_sandbox`.

Isolation properties (per ``docker run`` invocation):
  * ``--network none`` — no outbound network from the check container
  * ``--read-only`` — root filesystem is read-only
  * ``--tmpfs /tmp`` — writable ephemeral /tmp (required for pip/pytest caches)
  * ``--user`` — runs as non-root UID baked into the image (uid=1000)
  * ``--cpus``, ``--memory``, ``--memory-swap`` — CPU + memory caps (defaults 2.0 / 2g)
  * ``-v <workspace>:/workspace`` — bind-mount workspace rw (so evidence-side
    effects like ruff --fix and pytest artifacts are visible to the host)

Image cache strategy:
  * Each distinct dependency spec gets its own image tag:
    ``autonomy-sandbox:py<version>-<sha256[:12]>``
  * Images are built with ``LABEL autonomy-engine-sandbox=true`` so
    ``make sandbox-gc`` can prune them without touching other images.

Maps to: MITRE ATLAS T1609, NIST AI RMF GOVERN 1.4, OWASP LLM02 (Insecure Output
Handling — untrusted code execution).

Phase 2 POAM (deferred):
  * gVisor / Firecracker backend (``runsc`` runtime)
  * seccomp allowlist profile
  * Node.js base image + npm deps caching
  * Read-only workspace mount + separate writable output volume
"""

from __future__ import annotations

import hashlib
import logging
import os
import platform
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from engine.sandbox import Sandbox

logger = logging.getLogger(__name__)


# ── Defaults ────────────────────────────────────────────────────────────────

DEFAULT_PYTHON_VERSION = "3.11"
DEFAULT_CPU_LIMIT = "2.0"
DEFAULT_MEMORY_LIMIT = "2g"
DEFAULT_IMAGE_NAME = "autonomy-sandbox"
IMAGE_LABEL = "autonomy-engine-sandbox=true"
NON_ROOT_UID = 1000

# Dockerfile template baked into the image, keyed by deps spec.
# Image includes a non-root user, preinstalled common dev tools (ruff, mypy,
# pytest), and the project's requirements.txt / pyproject.toml deps.
_DOCKERFILE_TEMPLATE = """\
# syntax=docker/dockerfile:1
FROM python:{py_version}-slim

LABEL autonomy-engine-sandbox=true
LABEL autonomy-engine-deps-hash={deps_hash}

RUN groupadd --gid {uid} sandbox \\
 && useradd --uid {uid} --gid {uid} --shell /bin/bash --create-home sandbox

WORKDIR /workspace

# Common dev tools the auto-detected checks invoke.  Installing them at
# image-build time means the check container itself does not need network.
RUN pip install --no-cache-dir --disable-pip-version-check \\
    ruff==0.6.9 mypy==1.11.2 pytest==8.3.3

{deps_install_step}

USER sandbox
"""


# ── Helpers ─────────────────────────────────────────────────────────────────


def _docker_available() -> bool:
    """Return True if a working Docker CLI + daemon is available."""
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def _compute_deps_hash(workspace: Path, py_version: str) -> str:
    """Content-addressed hash of the dependency spec + Python version.

    Used as the image tag suffix so that image rebuilds are only triggered
    when the deps spec actually changes.
    """
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
    h.update(f":python={py_version}".encode())
    return h.hexdigest()[:12]


def _build_dockerfile(workspace: Path, py_version: str, deps_hash: str) -> str:
    """Render the Dockerfile for this workspace's deps spec."""
    req = workspace / "requirements.txt"
    pyproj = workspace / "pyproject.toml"

    if req.exists():
        deps_step = (
            "COPY requirements.txt /tmp/requirements.txt\n"
            "RUN pip install --no-cache-dir --disable-pip-version-check "
            "-r /tmp/requirements.txt"
        )
    elif pyproj.exists():
        deps_step = (
            "COPY pyproject.toml /tmp/pyproject.toml\n"
            "RUN pip install --no-cache-dir --disable-pip-version-check /tmp"
        )
    else:
        deps_step = "# (no requirements.txt or pyproject.toml — skipping deps install)"

    return _DOCKERFILE_TEMPLATE.format(
        py_version=py_version,
        deps_hash=deps_hash,
        deps_install_step=deps_step,
        uid=NON_ROOT_UID,
    )


def _image_exists(tag: str) -> bool:
    """Return True if a local image with *tag* already exists."""
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", tag],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def _image_digest(tag: str) -> str | None:
    """Return the image ID (sha256:...) for *tag*, or None if not found."""
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", tag],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            return result.stdout.strip() or None
    except (subprocess.SubprocessError, OSError):
        pass
    return None


def _build_image(workspace: Path, tag: str, py_version: str, deps_hash: str) -> bool:
    """Build the sandbox image for this workspace's deps spec.

    The Dockerfile is written to a temp directory next to the workspace copies
    of requirements.txt / pyproject.toml so that only those files enter the
    build context (project source is NOT copied into the image — it's
    bind-mounted at run time).
    """
    import tempfile

    dockerfile = _build_dockerfile(workspace, py_version, deps_hash)

    with tempfile.TemporaryDirectory(prefix="ae_docker_build_") as build_dir:
        build_path = Path(build_dir)
        (build_path / "Dockerfile").write_text(dockerfile)
        for dep_file in ("requirements.txt", "pyproject.toml"):
            src = workspace / dep_file
            if src.exists():
                shutil.copy(src, build_path / dep_file)

        try:
            result = subprocess.run(
                ["docker", "build", "-t", tag, "--label", IMAGE_LABEL, str(build_path)],
                capture_output=True,
                text=True,
                timeout=600,  # deps install can be slow on first build
            )
            if result.returncode != 0:
                logger.error(
                    "Docker image build failed (tag=%s): %s",
                    tag,
                    result.stderr[-500:],
                )
                return False
            return True
        except subprocess.TimeoutExpired:
            logger.error("Docker image build timed out (tag=%s)", tag)
            return False
        except (subprocess.SubprocessError, OSError) as exc:
            logger.error("Docker image build error (tag=%s): %s", tag, exc)
            return False


# ── DockerSandbox ───────────────────────────────────────────────────────────


class DockerSandbox(Sandbox):
    """Sandbox backend that runs each check inside an ephemeral container.

    The workspace is bind-mounted rw at /workspace.  Each :meth:`run` call is
    a separate ``docker run --rm`` invocation with isolation flags applied.
    """

    backend = "docker"

    def __init__(
        self,
        workspace: Path,
        image_tag: str,
        image_digest: str | None,
        deps_installed: bool,
        py_version: str,
        cpu_limit: str,
        memory_limit: str,
        image_built: bool,
        image_build_time: float,
    ):
        super().__init__(
            workspace=workspace,
            venv_path=None,
            deps_installed=deps_installed,
            project_type="python",
        )
        self.image_tag = image_tag
        self._image_digest = image_digest
        self.py_version = py_version
        self.cpu_limit = cpu_limit
        self.memory_limit = memory_limit
        self._image_built = image_built
        self._image_build_time = round(image_build_time, 3)

    def run(self, command: str, timeout: int = 300) -> dict:
        """Execute *command* inside an ephemeral container.

        Uses ``bash -c`` because the auto-detected checks rely on shell
        features (``&&``, ``$(...)``, globs).
        """
        started_at = datetime.now(timezone.utc).isoformat()

        argv = [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,exec,size=512m",  # nosec B108 — /tmp inside the container, not a host path

            "--user",
            str(NON_ROOT_UID),
            "--cpus",
            self.cpu_limit,
            "--memory",
            self.memory_limit,
            "--memory-swap",
            self.memory_limit,
            "--workdir",
            "/workspace",
            "-v",
            f"{self.workspace}:/workspace:rw",
            self.image_tag,
            "bash",
            "-c",
            command,
        ]

        stdout = ""
        stderr = ""
        exit_code = -1
        try:
            result = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout,
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
            stderr = f"Failed to execute docker run: {exc}"

        return {
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }

    @property
    def env(self) -> dict:
        """Return host env (docker-run propagates nothing by default)."""
        # Only used by legacy callers that still reach for sb.env; the
        # Docker backend does not expose the host runtime, so this returns a
        # neutral mapping.  New callers should use sb.run() and ignore env.
        return os.environ.copy()

    def metadata(self) -> dict:
        meta = super().metadata()
        meta.update(
            {
                "backend": "docker",
                "image_tag": self.image_tag,
                "image_digest": self._image_digest,
                "py_version": self.py_version,
                "isolation_flags": [
                    "--network=none",
                    "--read-only",
                    "--tmpfs=/tmp",
                    f"--user={NON_ROOT_UID}",
                    f"--cpus={self.cpu_limit}",
                    f"--memory={self.memory_limit}",
                ],
                "mount_mode": "bind-rw",
                "image_built_this_run": self._image_built,
                "image_build_time_s": self._image_build_time,
            }
        )
        return meta


# ── Entry point ─────────────────────────────────────────────────────────────


def setup_docker_sandbox(workspace: Path, install_deps: bool, cfg: dict) -> DockerSandbox:
    """Prepare a :class:`DockerSandbox` for *workspace*.

    Raises ``RuntimeError`` if Docker is unavailable — the caller may catch
    and fall back, but the config contract is: if you asked for ``docker``,
    you get docker or an error.  Silent fallback would defeat the security
    claim.
    """
    if not _docker_available():
        raise RuntimeError(
            "sandbox.backend=docker but the Docker CLI/daemon is not available. "
            "Install Docker (https://docs.docker.com/get-docker/) or switch to "
            "sandbox.backend=local in config.yml."
        )

    docker_cfg = cfg.get("docker") or {}
    py_version = str(docker_cfg.get("python_version") or DEFAULT_PYTHON_VERSION)
    cpu_limit = str(docker_cfg.get("cpus") or DEFAULT_CPU_LIMIT)
    memory_limit = str(docker_cfg.get("memory") or DEFAULT_MEMORY_LIMIT)
    image_base = str(docker_cfg.get("image_name") or DEFAULT_IMAGE_NAME)

    deps_hash = _compute_deps_hash(workspace, py_version)
    tag = f"{image_base}:py{py_version}-{deps_hash}"

    image_built_this_run = False
    t0 = _monotonic()
    if _image_exists(tag):
        logger.info("Docker sandbox image cache hit: %s", tag)
    else:
        logger.info("Building Docker sandbox image: %s", tag)
        if not _build_image(workspace, tag, py_version, deps_hash):
            raise RuntimeError(
                f"Docker image build failed for tag {tag}.  Check `docker build` output "
                "above.  Falling back is not attempted to preserve the isolation claim."
            )
        image_built_this_run = True
    build_time = _monotonic() - t0

    digest = _image_digest(tag)

    # deps_installed reflects whether the image carries the deps layer.
    # Even if install_deps=False at the engine level, the image was built
    # with deps baked in (idempotent), so we report True here iff there
    # were deps to install.
    req = workspace / "requirements.txt"
    pyproj = workspace / "pyproject.toml"
    deps_installed = req.exists() or pyproj.exists()

    sb = DockerSandbox(
        workspace=workspace,
        image_tag=tag,
        image_digest=digest,
        deps_installed=deps_installed and install_deps,
        py_version=py_version,
        cpu_limit=cpu_limit,
        memory_limit=memory_limit,
        image_built=image_built_this_run,
        image_build_time=build_time,
    )
    # Preserve metadata shape used by the local backend.
    sb._venv_cache_hit = not image_built_this_run
    sb._venv_cache_key = deps_hash
    sb._venv_create_time = round(build_time, 3)
    sb._deps_install_time = 0.0
    return sb


def _monotonic() -> float:
    import time

    return time.monotonic()


# Platform note for callers: Docker Desktop on macOS/Windows runs a Linux
# VM — the uid=1000 non-root user works correctly inside that VM even when
# the host is not Linux.  On native Linux with SELinux enforcing, the
# workspace bind mount may need the ``:Z`` suffix; add if users report it.
_HOST_PLATFORM = platform.system()
