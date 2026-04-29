"""Tests for run lifecycle wiring and bootstrap task."""

import json

import pytest

import engine.context
import engine.tracer as tracer
from engine.tracer import GENESIS_HASH, get_run_id, init_run, trace
from tasks.bootstrap import REQUIRED_FILES, bootstrap_project


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


def _create_intake_artifacts(tmp_path):
    """Create all required intake files so bootstrap succeeds."""
    inputs_dir = tmp_path / "state" / "inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    for f in REQUIRED_FILES:
        path = tmp_path / "state" / f
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {f}\n")


# ── init_run() before trace ──────────────────────────────────────────────────


class TestInitRunBeforeTrace:
    def test_trace_without_init_run_raises(self, tmp_path):
        """trace() must fail if init_run() was not called."""
        with pytest.raises(RuntimeError, match="No active run"):
            trace(task="test", inputs=[], outputs=[])

    def test_get_run_id_without_init_run_raises(self, tmp_path):
        """get_run_id() must fail if init_run() was not called."""
        with pytest.raises(RuntimeError, match="No active run"):
            get_run_id()

    def test_trace_succeeds_after_init_run(self, tmp_path):
        """trace() works once init_run() has been called."""
        init_run()
        trace(task="test", inputs=[], outputs=[])
        run_id = get_run_id()
        trace_file = tmp_path / "state" / "runs" / run_id / "trace.jsonl"
        assert trace_file.exists()
        entries = [json.loads(line) for line in trace_file.read_text().strip().splitlines()]
        assert len(entries) == 1
        assert entries[0]["task"] == "test"


# ── Bootstrap no longer writes TRACE.json ────────────────────────────────────


class TestBootstrapNoLegacyTrace:
    def test_no_trace_json_created(self, tmp_path):
        """bootstrap_project() must not create state/TRACE.json."""
        _create_intake_artifacts(tmp_path)
        init_run()
        bootstrap_project()
        assert not (tmp_path / "state" / "TRACE.json").exists()

    def test_existing_trace_json_not_touched(self, tmp_path):
        """If a stale TRACE.json exists, bootstrap must not modify it."""
        _create_intake_artifacts(tmp_path)
        legacy = tmp_path / "state" / "TRACE.json"
        legacy.write_text("[]\n")
        init_run()
        bootstrap_project()
        # File untouched — still the original content
        assert legacy.read_text() == "[]\n"


# ── Run-scoped directories ──────────────────────────────────────────────────


class TestRunScopedDirectories:
    def test_init_run_creates_run_directory(self, tmp_path):
        """init_run() must create state/runs/<run_id>/."""
        run_id = init_run()
        run_dir = tmp_path / "state" / "runs" / run_id
        assert run_dir.is_dir()

    def test_bootstrap_creates_evidence_dir(self, tmp_path):
        """bootstrap must create state/runs/<run_id>/evidence/."""
        _create_intake_artifacts(tmp_path)
        run_id = init_run()
        bootstrap_project()
        assert (tmp_path / "state" / "runs" / run_id / "evidence").is_dir()

    def test_bootstrap_creates_decisions_dir(self, tmp_path):
        """bootstrap must create state/runs/<run_id>/decisions/."""
        _create_intake_artifacts(tmp_path)
        run_id = init_run()
        bootstrap_project()
        assert (tmp_path / "state" / "runs" / run_id / "decisions").is_dir()

    def test_bootstrap_creates_global_output_dirs(self, tmp_path):
        """bootstrap must scaffold designs/, implementations/, tests/, build/."""
        _create_intake_artifacts(tmp_path)
        init_run()
        bootstrap_project()
        state_dir = tmp_path / "state"
        for subdir in ("designs", "implementations", "tests", "build"):
            assert (state_dir / subdir).is_dir()

    def test_bootstrap_requires_active_run(self, tmp_path):
        """bootstrap must fail if init_run() was not called first."""
        _create_intake_artifacts(tmp_path)
        with pytest.raises(RuntimeError, match="No active run"):
            bootstrap_project()


# ── Bootstrap trace entry ────────────────────────────────────────────────────


class TestBootstrapTrace:
    def test_bootstrap_writes_trace_entry(self, tmp_path):
        """bootstrap must write a trace entry to the run's trace.jsonl."""
        _create_intake_artifacts(tmp_path)
        run_id = init_run()
        bootstrap_project()
        trace_file = tmp_path / "state" / "runs" / run_id / "trace.jsonl"
        assert trace_file.exists()
        entries = [json.loads(line) for line in trace_file.read_text().strip().splitlines()]
        assert len(entries) == 1
        assert entries[0]["task"] == "bootstrap"
        assert entries[0]["seq"] == 0
        assert entries[0]["prev_hash"] == GENESIS_HASH

    def test_bootstrap_trace_has_valid_chain(self, tmp_path):
        """The bootstrap trace entry must have a valid entry_hash."""
        _create_intake_artifacts(tmp_path)
        run_id = init_run()
        bootstrap_project()
        trace_file = tmp_path / "state" / "runs" / run_id / "trace.jsonl"
        entry = json.loads(trace_file.read_text().strip())
        assert "entry_hash" in entry
        assert len(entry["entry_hash"]) == 64

    def test_bootstrap_inputs_match_present_files(self, tmp_path):
        """Bootstrap trace must list the intake files that are actually present."""
        _create_intake_artifacts(tmp_path)
        run_id = init_run()
        bootstrap_project()
        trace_file = tmp_path / "state" / "runs" / run_id / "trace.jsonl"
        entry = json.loads(trace_file.read_text().strip())
        # All required files should be listed as inputs
        for f in REQUIRED_FILES:
            assert f in entry["inputs"]

    def test_bootstrap_outputs_empty_without_config(self, tmp_path):
        """Bootstrap trace outputs should be empty when no config.yml exists."""
        _create_intake_artifacts(tmp_path)
        run_id = init_run()
        bootstrap_project()
        trace_file = tmp_path / "state" / "runs" / run_id / "trace.jsonl"
        entry = json.loads(trace_file.read_text().strip())
        assert entry["outputs"] == {}

    def test_bootstrap_outputs_include_config_snapshot(self, tmp_path):
        """Bootstrap trace outputs include config_snapshot.yml when config exists."""
        _create_intake_artifacts(tmp_path)
        (tmp_path / "config.yml").write_text("llm:\n  provider: test\n")
        run_id = init_run()
        bootstrap_project()
        trace_file = tmp_path / "state" / "runs" / run_id / "trace.jsonl"
        entry = json.loads(trace_file.read_text().strip())
        output_keys = list(entry["outputs"].keys())
        assert len(output_keys) == 1
        assert f"runs/{run_id}/config_snapshot.yml" in output_keys[0]
