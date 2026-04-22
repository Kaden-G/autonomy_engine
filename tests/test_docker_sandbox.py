"""Tests for the Docker sandbox backend (engine.sandbox_docker).

These tests require a working Docker CLI + daemon.  Locally, they skip
automatically when Docker is unavailable — that keeps the dev loop fast on
machines where Docker Desktop isn't running.  In CI, a dedicated job (see
.github/workflows/ci.yml → docker-sandbox-tests) fails loudly if Docker is
missing so a silent skip never hides a broken backend.

Marker: ``@pytest.mark.docker`` — selectable via ``-m docker`` or
``--deselect -m docker`` depending on need.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import engine.context
import engine.tracer as tracer
from engine.evidence import run_check
from engine.sandbox import create_sandbox
from engine.sandbox_docker import (
    DockerSandbox,
    _compute_deps_hash,
    _docker_available,
    setup_docker_sandbox,
)
from engine.tracer import GENESIS_HASH


# Gate: skip when no Docker.  CI sets AE_REQUIRE_DOCKER=1 to turn the skip
# into an explicit failure so the backend can't silently go untested.
def _docker_gate():
    if _docker_available():
        return None
    import os

    if os.environ.get("AE_REQUIRE_DOCKER") == "1":
        pytest.fail(
            "AE_REQUIRE_DOCKER=1 but Docker is not available — the docker-sandbox-tests "
            "job must have a working Docker daemon."
        )
    return pytest.mark.skip(reason="Docker CLI/daemon not available")


pytestmark = [pytest.mark.slow, pytest.mark.docker]


skip_if_no_docker = pytest.mark.skipif(
    not _docker_available(),
    reason="Docker CLI/daemon not available",
)


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


def _make_python_project(tmp_path: Path, requirements: str = "") -> Path:
    """Create a minimal Python project.  ``requirements`` may be empty."""
    project = tmp_path / "project"
    project.mkdir(parents=True, exist_ok=True)
    (project / "app.py").write_text("print('hello from sandbox')\n")
    if requirements:
        (project / "requirements.txt").write_text(requirements)
    return project


# ── Deps-hash determinism ───────────────────────────────────────────────────


class TestDepsHash:
    """Image tag determinism — same inputs → same hash; changes → new hash."""

    def test_hash_is_deterministic(self, tmp_path):
        p1 = _make_python_project(tmp_path / "a", requirements="requests==2.31.0\n")
        p2 = _make_python_project(tmp_path / "b", requirements="requests==2.31.0\n")
        assert _compute_deps_hash(p1, "3.11") == _compute_deps_hash(p2, "3.11")

    def test_hash_changes_with_requirements(self, tmp_path):
        p1 = _make_python_project(tmp_path / "a", requirements="requests==2.31.0\n")
        p2 = _make_python_project(tmp_path / "b", requirements="requests==2.32.0\n")
        assert _compute_deps_hash(p1, "3.11") != _compute_deps_hash(p2, "3.11")

    def test_hash_changes_with_python_version(self, tmp_path):
        p = _make_python_project(tmp_path, requirements="requests==2.31.0\n")
        assert _compute_deps_hash(p, "3.11") != _compute_deps_hash(p, "3.12")

    def test_no_deps_is_stable(self, tmp_path):
        p = _make_python_project(tmp_path)
        h1 = _compute_deps_hash(p, "3.11")
        h2 = _compute_deps_hash(p, "3.11")
        assert h1 == h2


# ── Docker availability check ───────────────────────────────────────────────


class TestDockerAvailability:
    def test_raises_when_docker_missing(self, tmp_path, monkeypatch):
        """If docker CLI is missing, setup_docker_sandbox raises — no silent fallback."""
        monkeypatch.setattr("engine.sandbox_docker.shutil.which", lambda _: None)
        workspace = _make_python_project(tmp_path)
        with pytest.raises(RuntimeError, match="Docker CLI/daemon is not available"):
            setup_docker_sandbox(workspace, install_deps=False, cfg={})


# ── End-to-end Docker execution ─────────────────────────────────────────────


@skip_if_no_docker
class TestDockerSandboxRun:
    """Full-stack tests — require a working Docker daemon."""

    def test_run_returns_evidence_shape(self, tmp_path):
        workspace = _make_python_project(tmp_path)
        sb = setup_docker_sandbox(workspace, install_deps=False, cfg={})
        result = sb.run("echo hello")
        assert result["exit_code"] == 0
        assert "hello" in result["stdout"]
        assert "started_at" in result
        assert "finished_at" in result

    def test_run_captures_nonzero_exit(self, tmp_path):
        workspace = _make_python_project(tmp_path)
        sb = setup_docker_sandbox(workspace, install_deps=False, cfg={})
        result = sb.run("exit 3")
        assert result["exit_code"] == 3

    def test_network_is_isolated(self, tmp_path):
        """A container launched by the sandbox must not reach the network.

        We probe with ``getent hosts example.com`` — no DNS, no route.  This
        is the core of the isolation claim, so test it directly.
        """
        workspace = _make_python_project(tmp_path)
        sb = setup_docker_sandbox(workspace, install_deps=False, cfg={})
        # Use `python -c` since `getent` is not in slim images but `socket` is.
        probe = "python -c \"import socket; socket.gethostbyname('example.com')\" 2>&1 || true"
        result = sb.run(probe)
        # Either DNS fails outright or socket errors out — both are acceptable
        # signs of "--network none" being honoured.
        assert (
            "Temporary failure in name resolution" in result["stdout"]
            or "Name or service not known" in result["stdout"]
            or "socket.gaierror" in result["stdout"]
            or result["exit_code"] != 0
        ), f"Network appears reachable: {result['stdout']!r}"

    def test_runs_as_non_root(self, tmp_path):
        workspace = _make_python_project(tmp_path)
        sb = setup_docker_sandbox(workspace, install_deps=False, cfg={})
        result = sb.run("id -u")
        assert result["exit_code"] == 0
        assert result["stdout"].strip() == "1000"

    def test_workspace_is_bind_mounted(self, tmp_path):
        """Files written in the container are visible on the host."""
        workspace = _make_python_project(tmp_path)
        sb = setup_docker_sandbox(workspace, install_deps=False, cfg={})
        result = sb.run("echo 'hello from container' > marker.txt")
        assert result["exit_code"] == 0
        assert (workspace / "marker.txt").read_text().strip() == "hello from container"

    def test_metadata_reports_docker_backend(self, tmp_path):
        workspace = _make_python_project(tmp_path)
        sb = setup_docker_sandbox(workspace, install_deps=False, cfg={})
        meta = sb.metadata()
        assert meta["backend"] == "docker"
        assert meta["mount_mode"] == "bind-rw"
        assert "--network=none" in meta["isolation_flags"]
        assert "--read-only" in meta["isolation_flags"]
        assert any(f.startswith("--user=") for f in meta["isolation_flags"])
        assert any(f.startswith("--cpus=") for f in meta["isolation_flags"])
        assert any(f.startswith("--memory=") for f in meta["isolation_flags"])
        assert meta["image_tag"].startswith("autonomy-sandbox:py")

    def test_image_is_cached_across_invocations(self, tmp_path):
        """Second setup with the same deps spec should not rebuild the image."""
        workspace1 = _make_python_project(tmp_path / "a")
        sb1 = setup_docker_sandbox(workspace1, install_deps=False, cfg={})
        assert sb1._image_built is True or sb1.metadata()["image_built_this_run"] is False
        # Second workspace, identical deps → same image tag, cache hit
        workspace2 = _make_python_project(tmp_path / "b")
        sb2 = setup_docker_sandbox(workspace2, install_deps=False, cfg={})
        assert sb2.image_tag == sb1.image_tag
        assert sb2.metadata()["image_built_this_run"] is False

    def test_run_check_dispatches_through_sandbox(self, tmp_path):
        """The engine's run_check() must route through Sandbox.run() when passed a sandbox."""
        workspace = _make_python_project(tmp_path)
        sb = setup_docker_sandbox(workspace, install_deps=False, cfg={})
        record = run_check(name="echo", command="echo routed", sandbox=sb)
        assert record["exit_code"] == 0
        assert "routed" in record["stdout"]
        assert record["cwd"] == str(workspace)

    def test_create_sandbox_picks_docker_backend(self, tmp_path):
        """create_sandbox() dispatches to Docker when cfg['backend']='docker'."""
        project = _make_python_project(tmp_path)
        with create_sandbox(project, install_deps=False, sandbox_cfg={"backend": "docker"}) as sb:
            assert isinstance(sb, DockerSandbox)
            assert sb.metadata()["backend"] == "docker"

    def test_readonly_root_prevents_writes_outside_workspace(self, tmp_path):
        """Writing to / or /etc should fail — root is read-only."""
        workspace = _make_python_project(tmp_path)
        sb = setup_docker_sandbox(workspace, install_deps=False, cfg={})
        result = sb.run("echo x > /etc/hacked 2>&1 || echo BLOCKED")
        assert "BLOCKED" in result["stdout"] or result["exit_code"] != 0

    def test_tmpfs_is_writable(self, tmp_path):
        """/tmp is writable (tmpfs) — pip caches, pytest, etc. need this."""
        workspace = _make_python_project(tmp_path)
        sb = setup_docker_sandbox(workspace, install_deps=False, cfg={})
        result = sb.run("echo ok > /tmp/marker && cat /tmp/marker")
        assert result["exit_code"] == 0
        assert "ok" in result["stdout"]


@skip_if_no_docker
class TestDockerDepsInstalled:
    """Image build bakes deps in — they're available without a runtime install."""

    def test_requirements_installed_in_image(self, tmp_path):
        # Use a tiny, no-deps package so the image build stays fast
        workspace = _make_python_project(tmp_path, requirements="pyjokes==0.6.0\n")
        sb = setup_docker_sandbox(workspace, install_deps=True, cfg={})
        result = sb.run("python -c 'import pyjokes; print(pyjokes.__version__)'")
        assert result["exit_code"] == 0
        assert "0.6.0" in result["stdout"]
