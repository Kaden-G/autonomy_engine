"""Tests for project-dir resolution in extract and test stages."""

import json

import pytest
import yaml

import engine.context
import engine.tracer as tracer
from engine.context import ENGINE_ROOT, get_project_dir
from engine.tracer import GENESIS_HASH, init_run


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path):
    """Point engine context at a temp dir and reset tracer module state."""
    engine.context.init(tmp_path)
    (tmp_path / "state").mkdir()
    tracer._run_id = None
    tracer._prev_hash = GENESIS_HASH
    tracer._seq = 0
    yield
    tracer._run_id = None
    tracer._prev_hash = GENESIS_HASH
    tracer._seq = 0


# ── get_project_dir accessor ───────────────────────────────────────────────


class TestGetProjectDir:
    def test_defaults_to_engine_root(self):
        engine.context.init(None)
        assert get_project_dir() == ENGINE_ROOT

    def test_returns_custom_dir(self, tmp_path):
        engine.context.init(tmp_path)
        assert get_project_dir() == tmp_path

    def test_resolves_relative_path(self, tmp_path):
        """Relative paths should be resolved to absolute."""
        engine.context.init(tmp_path / "subdir" / ".." / "other")
        result = get_project_dir()
        assert result.is_absolute()
        assert result == (tmp_path / "other").resolve()


# ── Extract output resolution ──────────────────────────────────────────────


class TestExtractOutputResolution:
    def _setup_for_extract(self, tmp_path, project_name="My Test App"):
        """Set up state files needed for extraction."""
        engine.context.init(tmp_path)
        state_dir = tmp_path / "state"
        state_dir.mkdir(exist_ok=True)

        # Write project spec
        spec = {
            "project": {"name": project_name, "description": "test", "domain": "software"},
            "requirements": {"functional": ["req"]},
            "acceptance_criteria": ["crit"],
            "outputs": {"expected_artifacts": ["app.py"]},
        }
        inputs_dir = state_dir / "inputs"
        inputs_dir.mkdir(parents=True, exist_ok=True)
        (inputs_dir / "project_spec.yml").write_text(yaml.dump(spec))

        # Write FILE_MANIFEST.json
        manifest = {"files": [{"path": "app.py", "content": "print('hi')"}]}
        impl_dir = state_dir / "implementations"
        impl_dir.mkdir(parents=True, exist_ok=True)
        (impl_dir / "FILE_MANIFEST.json").write_text(json.dumps(manifest))

        # Build dir
        (state_dir / "build").mkdir(parents=True, exist_ok=True)

    def test_extract_resolves_to_project_dir_sibling(self, tmp_path):
        """Extract should write to a sibling of the project dir, not ENGINE_ROOT."""
        project_dir = tmp_path / "my_project"
        project_dir.mkdir()
        self._setup_for_extract(project_dir)
        init_run()

        from tasks.extract import extract_project

        extract_project()

        # Output should be at project_dir.parent / slug, which is tmp_path / "my-test-app"
        output_dir = tmp_path / "my-test-app"
        assert output_dir.is_dir()
        assert (output_dir / "app.py").exists()
        assert "print('hi')" in (output_dir / "app.py").read_text()

    def test_extract_with_default_context_uses_engine_root_parent(self, tmp_path):
        """When project dir = ENGINE_ROOT, extract writes to ENGINE_ROOT.parent / slug."""
        # This just verifies the path math: get_project_dir().parent == ENGINE_ROOT.parent
        engine.context.init(None)
        assert get_project_dir().parent == ENGINE_ROOT.parent

    def test_extract_different_project_dirs_produce_different_outputs(self, tmp_path):
        """Two different project dirs should produce sibling output dirs."""
        proj_a = tmp_path / "proj_a"
        proj_b = tmp_path / "proj_b"
        proj_a.mkdir()
        proj_b.mkdir()

        # Extract for project A
        self._setup_for_extract(proj_a, "Alpha App")
        init_run()
        from tasks.extract import extract_project

        extract_project()

        # Extract for project B
        self._setup_for_extract(proj_b, "Beta App")
        init_run()
        extract_project()

        assert (tmp_path / "alpha-app").is_dir()
        assert (tmp_path / "beta-app").is_dir()


# ── Test-stage project resolution ──────────────────────────────────────────


class TestTestStageResolution:
    def test_get_project_dir_resolves_from_context(self, tmp_path):
        """_get_project_dir in tasks.test should use the context, not ENGINE_ROOT."""
        project_dir = tmp_path / "my_project"
        project_dir.mkdir()
        engine.context.init(project_dir)

        state_dir = project_dir / "state"
        inputs_dir = state_dir / "inputs"
        inputs_dir.mkdir(parents=True, exist_ok=True)

        spec = {
            "project": {"name": "My App", "description": "test", "domain": "software"},
            "requirements": {"functional": ["req"]},
            "acceptance_criteria": ["crit"],
            "outputs": {"expected_artifacts": ["app.py"]},
        }
        (inputs_dir / "project_spec.yml").write_text(yaml.dump(spec))
        init_run()

        from tasks.test import _get_project_dir as get_test_project_dir

        result = get_test_project_dir()

        # Should be project_dir.parent / "my-app", i.e. tmp_path / "my-app"
        assert result == tmp_path / "my-app"

    def test_extract_and_test_agree_on_project_dir(self, tmp_path):
        """Extract output dir and test project dir must point to the same place."""
        project_dir = tmp_path / "engine_project"
        project_dir.mkdir()
        engine.context.init(project_dir)

        state_dir = project_dir / "state"
        inputs_dir = state_dir / "inputs"
        inputs_dir.mkdir(parents=True, exist_ok=True)

        spec = {
            "project": {"name": "Shared App", "description": "test", "domain": "software"},
            "requirements": {"functional": ["req"]},
            "acceptance_criteria": ["crit"],
            "outputs": {"expected_artifacts": ["app.py"]},
        }
        (inputs_dir / "project_spec.yml").write_text(yaml.dump(spec))
        init_run()

        from tasks.extract import _slugify
        from tasks.test import _get_project_dir as get_test_project_dir

        # Extract would write to:
        extract_output = get_project_dir().parent / _slugify("Shared App")

        # Test stage resolves to:
        test_dir = get_test_project_dir()

        assert extract_output == test_dir
