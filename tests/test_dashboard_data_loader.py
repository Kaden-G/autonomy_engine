"""Tests for dashboard.data_loader — verify data access logic."""

import hashlib
import json

import pytest
import yaml

from dashboard.data_loader import (
    get_cache_stats,
    get_intake_status,
    get_latest_run_id,
    get_pipeline_status,
    is_intake_complete,
    list_runs,
    load_config,
    load_trace,
    verify_trace_integrity,
)


@pytest.fixture
def project(tmp_path):
    """Create a minimal project structure."""
    state = tmp_path / "state"
    (state / "inputs").mkdir(parents=True)
    (state / "designs").mkdir(parents=True)
    (state / "implementations").mkdir(parents=True)
    (state / "tests").mkdir(parents=True)
    (state / "build").mkdir(parents=True)
    (state / "runs").mkdir(parents=True)
    (state / "cache" / "llm").mkdir(parents=True)

    # Write config
    config = {
        "llm": {
            "provider": "claude",
            "max_tokens": 16384,
            "claude": {"model": "test"},
        }
    }
    (tmp_path / "config.yml").write_text(yaml.dump(config))

    return tmp_path


def _create_run(project, run_id, entries=None):
    """Helper: create a run directory with optional trace entries."""
    run_dir = project / "state" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "evidence").mkdir(exist_ok=True)
    (run_dir / "decisions").mkdir(exist_ok=True)

    if entries:
        prev_hash = "0" * 64
        lines = []
        for i, entry in enumerate(entries):
            entry["seq"] = i
            entry["prev_hash"] = prev_hash
            entry.setdefault("timestamp", "2026-01-01T00:00:00+00:00")
            entry.setdefault("inputs", {})
            entry.setdefault("outputs", {})
            entry.setdefault("model", None)
            entry.setdefault("prompt_hash", None)
            entry.setdefault("provider", None)
            entry.setdefault("max_tokens", None)

            canonical = json.dumps(entry, sort_keys=True, separators=(",", ":"))
            entry_hash = hashlib.sha256(canonical.encode()).hexdigest()
            entry["entry_hash"] = entry_hash
            prev_hash = entry_hash
            lines.append(json.dumps(entry, separators=(",", ":")))

        (run_dir / "trace.jsonl").write_text("\n".join(lines) + "\n")

    return run_dir


class TestListRuns:
    def test_empty(self, project):
        assert list_runs(project) == []

    def test_finds_runs(self, project):
        _create_run(project, "abc123", [{"task": "design"}])
        runs = list_runs(project)
        assert len(runs) == 1
        assert runs[0]["run_id"] == "abc123"
        assert runs[0]["trace_entries"] == 1


class TestTraceIntegrity:
    def test_valid_chain(self, project):
        _create_run(project, "run1", [{"task": "design"}, {"task": "implement"}])
        valid, errors = verify_trace_integrity(project, "run1")
        assert valid is True
        assert errors == []

    def test_missing_run(self, project):
        valid, errors = verify_trace_integrity(project, "nonexistent")
        assert valid is False


class TestLoadTrace:
    def test_loads_entries(self, project):
        _create_run(project, "run1", [{"task": "design"}, {"task": "implement"}])
        entries = load_trace(project, "run1")
        assert len(entries) == 2
        assert entries[0]["task"] == "design"
        assert entries[1]["task"] == "implement"

    def test_empty_run(self, project):
        entries = load_trace(project, "nonexistent")
        assert entries == []


class TestIntakeStatus:
    def test_incomplete(self, project):
        status = get_intake_status(project)
        assert not all(status.values())

    def test_complete(self, project):
        inputs = project / "state" / "inputs"
        for f in [
            "project_spec.yml",
            "REQUIREMENTS.md",
            "CONSTRAINTS.md",
            "NON_GOALS.md",
            "ACCEPTANCE_CRITERIA.md",
        ]:
            (inputs / f).write_text("content")
        status = get_intake_status(project)
        assert all(status.values())


class TestPipelineStatus:
    def test_empty_state(self, project):
        status = get_pipeline_status(project)
        assert status["intake_complete"] is False
        assert status["has_architecture"] is False

    def test_with_artifacts(self, project):
        (project / "state" / "designs" / "ARCHITECTURE.md").write_text("arch")
        status = get_pipeline_status(project)
        assert status["has_architecture"] is True


class TestCacheStats:
    def test_empty_cache(self, project):
        stats = get_cache_stats(project)
        assert stats["total_entries"] == 0

    def test_cache_entries(self, project):
        cache_dir = project / "state" / "cache" / "llm"
        (cache_dir / "abc.json").write_text(
            json.dumps(
                {
                    "response": "cached",
                    "stage": "design",
                    "model": "test",
                    "created_at": "2026-01-01",
                }
            )
        )
        stats = get_cache_stats(project)
        assert stats["total_entries"] == 1
        assert stats["by_stage"]["design"] == 1


class TestIsIntakeComplete:
    def test_incomplete(self, project):
        assert is_intake_complete(project) is False

    def test_complete(self, project):
        inputs = project / "state" / "inputs"
        for f in [
            "project_spec.yml",
            "REQUIREMENTS.md",
            "CONSTRAINTS.md",
            "NON_GOALS.md",
            "ACCEPTANCE_CRITERIA.md",
        ]:
            (inputs / f).write_text("content")
        assert is_intake_complete(project) is True


class TestGetLatestRunId:
    def test_empty(self, project):
        assert get_latest_run_id(project) is None

    def test_no_runs_dir(self, tmp_path):
        # No state/runs directory at all
        (tmp_path / "state").mkdir()
        assert get_latest_run_id(tmp_path) is None

    def test_finds_latest(self, project):
        import time

        _create_run(project, "run_old", [{"task": "design"}])
        time.sleep(0.05)  # ensure different mtime
        _create_run(project, "run_new", [{"task": "implement"}])
        assert get_latest_run_id(project) == "run_new"


class TestLoadConfig:
    def test_loads_config(self, project):
        config = load_config(project)
        assert config["llm"]["provider"] == "claude"

    def test_missing_config(self, tmp_path):
        config = load_config(tmp_path)
        assert config == {}
