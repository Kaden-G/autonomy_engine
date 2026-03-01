"""Tests for sandbox venv caching."""

from pathlib import Path

import pytest

import engine.context
import engine.tracer as tracer
from engine.sandbox import _compute_venv_cache_key, create_sandbox
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
    project.mkdir(exist_ok=True)
    if files:
        for name, content in files.items():
            p = project / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
    return project


class TestVenvCacheKeyDeterministic:
    def test_same_deps_same_key(self, tmp_path):
        dir1 = tmp_path / "a"
        dir1.mkdir()
        (dir1 / "requirements.txt").write_text("requests==2.31.0\n")

        dir2 = tmp_path / "b"
        dir2.mkdir()
        (dir2 / "requirements.txt").write_text("requests==2.31.0\n")

        key1 = _compute_venv_cache_key(dir1, {})
        key2 = _compute_venv_cache_key(dir2, {})
        assert key1 == key2


class TestVenvCacheKeyChangesWithDeps:
    def test_different_deps_different_key(self, tmp_path):
        dir1 = tmp_path / "a"
        dir1.mkdir()
        (dir1 / "requirements.txt").write_text("requests==2.31.0\n")

        dir2 = tmp_path / "b"
        dir2.mkdir()
        (dir2 / "requirements.txt").write_text("flask==3.0.0\n")

        key1 = _compute_venv_cache_key(dir1, {})
        key2 = _compute_venv_cache_key(dir2, {})
        assert key1 != key2


class TestSandboxReusesVenv:
    def test_second_sandbox_hits_cache(self, tmp_path):
        project = _make_project(tmp_path, {"app.py": "print('hi')"})

        # First run — cold
        with create_sandbox(project, install_deps=False) as sb1:
            assert sb1._venv_cache_hit is False

        # Second run — should hit cache
        with create_sandbox(project, install_deps=False) as sb2:
            assert sb2._venv_cache_hit is True
            assert sb2.venv_path is not None
            assert (sb2.venv_path / "bin" / "python").exists()


class TestSandboxMetadataIncludesCacheInfo:
    def test_metadata_has_cache_fields(self, tmp_path):
        project = _make_project(tmp_path, {"app.py": "x = 1"})
        with create_sandbox(project, install_deps=False) as sb:
            meta = sb.metadata()
            assert "venv_cache_hit" in meta
            assert "venv_cache_key" in meta
            assert "venv_create_time_s" in meta
            assert "deps_install_time_s" in meta
            assert isinstance(meta["venv_cache_hit"], bool)
            assert isinstance(meta["venv_create_time_s"], float)
