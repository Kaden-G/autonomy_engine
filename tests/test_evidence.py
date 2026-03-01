"""Tests for engine.evidence — execution evidence capture."""

import json

import pytest

import engine.context
import engine.tracer as tracer
from engine.tracer import GENESIS_HASH, init_run
from engine.evidence import (
    format_evidence_for_llm,
    load_all_evidence,
    load_configured_checks,
    no_checks_record,
    run_check,
    save_evidence,
)


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


# ── run_check ────────────────────────────────────────────────────────────────


class TestRunCheck:
    def test_success_captures_stdout(self, tmp_path):
        record = run_check("echo_test", "echo hello", cwd=tmp_path)
        assert record["exit_code"] == 0
        assert "hello" in record["stdout"]
        assert record["name"] == "echo_test"
        assert record["command"] == "echo hello"

    def test_failure_captures_exit_code(self, tmp_path):
        record = run_check("fail", "exit 1", cwd=tmp_path)
        assert record["exit_code"] == 1

    def test_captures_stderr(self, tmp_path):
        record = run_check("err", "echo oops >&2", cwd=tmp_path)
        assert "oops" in record["stderr"]

    def test_captures_both_streams(self, tmp_path):
        record = run_check("both", "echo out && echo err >&2", cwd=tmp_path)
        assert "out" in record["stdout"]
        assert "err" in record["stderr"]

    def test_timestamps_present_and_ordered(self, tmp_path):
        record = run_check("ts", "echo hi", cwd=tmp_path)
        assert record["started_at"] <= record["finished_at"]

    def test_stdout_hash_is_sha256(self, tmp_path):
        record = run_check("hash", "echo hi", cwd=tmp_path)
        assert len(record["stdout_hash"]) == 64
        int(record["stdout_hash"], 16)  # valid hex

    def test_stderr_hash_is_sha256(self, tmp_path):
        record = run_check("hash", "echo err >&2", cwd=tmp_path)
        assert len(record["stderr_hash"]) == 64
        int(record["stderr_hash"], 16)

    def test_cwd_recorded(self, tmp_path):
        record = run_check("cwd", "pwd", cwd=tmp_path)
        assert record["cwd"] == str(tmp_path)

    def test_timeout_captured(self, tmp_path):
        record = run_check("slow", "sleep 999", cwd=tmp_path, timeout=1)
        assert record["exit_code"] == -1
        assert "timed out" in record["stderr"].lower()

    def test_bad_cwd_captured(self, tmp_path):
        bad_dir = tmp_path / "nonexistent"
        record = run_check("bad_cwd", "echo hi", cwd=bad_dir)
        assert record["exit_code"] == -1
        assert record["stderr"]  # some error message

    def test_multiline_output(self, tmp_path):
        record = run_check("multi", "echo line1 && echo line2", cwd=tmp_path)
        assert "line1" in record["stdout"]
        assert "line2" in record["stdout"]

    def test_nonzero_exit_code_preserved(self, tmp_path):
        record = run_check("exit42", "exit 42", cwd=tmp_path)
        assert record["exit_code"] == 42


# ── save_evidence / load_all_evidence ────────────────────────────────────────


class TestSaveLoadEvidence:
    def test_save_creates_json_file(self, tmp_path):
        init_run()
        record = run_check("test", "echo hi", cwd=tmp_path)
        path = save_evidence(record)
        assert path.exists()
        assert path.name == "test.json"

    def test_saved_file_is_valid_json(self, tmp_path):
        init_run()
        record = run_check("test", "echo hi", cwd=tmp_path)
        path = save_evidence(record)
        data = json.loads(path.read_text())
        assert data["name"] == "test"

    def test_load_returns_saved_records(self, tmp_path):
        init_run()
        r1 = run_check("alpha", "echo a", cwd=tmp_path)
        r2 = run_check("beta", "echo b", cwd=tmp_path)
        save_evidence(r1)
        save_evidence(r2)
        loaded = load_all_evidence()
        assert len(loaded) == 2
        names = [r["name"] for r in loaded]
        assert "alpha" in names
        assert "beta" in names

    def test_load_empty_when_no_evidence(self, tmp_path):
        init_run()
        loaded = load_all_evidence()
        assert loaded == []

    def test_records_sorted_by_name(self, tmp_path):
        init_run()
        save_evidence(run_check("zulu", "echo z", cwd=tmp_path))
        save_evidence(run_check("alpha", "echo a", cwd=tmp_path))
        loaded = load_all_evidence()
        assert loaded[0]["name"] == "alpha"
        assert loaded[1]["name"] == "zulu"


# ── no_checks_record ────────────────────────────────────────────────────────


class TestNoChecksRecord:
    def test_has_sentinel_name(self):
        record = no_checks_record()
        assert record["name"] == "no_checks_configured"

    def test_exit_code_is_minus_one(self):
        record = no_checks_record()
        assert record["exit_code"] == -1

    def test_stderr_explains_situation(self):
        record = no_checks_record()
        assert "No checks configured" in record["stderr"]

    def test_has_all_required_fields(self):
        record = no_checks_record()
        required = [
            "name", "command", "cwd", "started_at", "finished_at",
            "exit_code", "stdout", "stderr", "stdout_hash", "stderr_hash",
        ]
        for field in required:
            assert field in record


# ── format_evidence_for_llm ─────────────────────────────────────────────────


class TestFormatEvidence:
    def test_success_shows_passed(self, tmp_path):
        record = run_check("lint", "echo ok", cwd=tmp_path)
        text = format_evidence_for_llm([record])
        assert "PASSED" in text
        assert "lint" in text

    def test_failure_shows_failed(self, tmp_path):
        record = run_check("lint", "exit 1", cwd=tmp_path)
        text = format_evidence_for_llm([record])
        assert "FAILED" in text

    def test_empty_returns_no_evidence_message(self):
        text = format_evidence_for_llm([])
        assert "No evidence collected" in text

    def test_no_checks_sentinel_handled(self):
        text = format_evidence_for_llm([no_checks_record()])
        assert "No checks configured" in text
        # Should NOT show PASSED or FAILED
        assert "PASSED" not in text
        assert "FAILED" not in text

    def test_includes_stdout(self, tmp_path):
        record = run_check("t", "echo important_output", cwd=tmp_path)
        text = format_evidence_for_llm([record])
        assert "important_output" in text

    def test_includes_stderr(self, tmp_path):
        record = run_check("t", "echo warning_msg >&2", cwd=tmp_path)
        text = format_evidence_for_llm([record])
        assert "warning_msg" in text

    def test_multiple_records_all_shown(self, tmp_path):
        r1 = run_check("tests", "echo ok", cwd=tmp_path)
        r2 = run_check("lint", "exit 1", cwd=tmp_path)
        text = format_evidence_for_llm([r1, r2])
        assert "tests" in text
        assert "lint" in text
        assert "PASSED" in text
        assert "FAILED" in text


# ── load_configured_checks ──────────────────────────────────────────────────


class TestLoadConfiguredChecks:
    def test_returns_empty_when_no_config_file(self, tmp_path):
        # context is already pointing at tmp_path which has no config.yml
        result = load_configured_checks()
        assert result == []

    def test_returns_empty_when_no_checks_section(self, tmp_path):
        (tmp_path / "config.yml").write_text("llm:\n  provider: claude\n")
        result = load_configured_checks()
        assert result == []

    def test_returns_empty_when_checks_is_null(self, tmp_path):
        (tmp_path / "config.yml").write_text("checks:\n")
        result = load_configured_checks()
        assert result == []

    def test_returns_empty_when_checks_is_empty_list(self, tmp_path):
        (tmp_path / "config.yml").write_text("checks: []\n")
        result = load_configured_checks()
        assert result == []

    def test_returns_checks_from_config(self, tmp_path):
        config = (
            "checks:\n"
            '  - name: "tests"\n'
            '    command: "python -m pytest"\n'
            '  - name: "lint"\n'
            '    command: "ruff check ."\n'
        )
        (tmp_path / "config.yml").write_text(config)
        result = load_configured_checks()
        assert len(result) == 2
        assert result[0]["name"] == "tests"
        assert result[0]["command"] == "python -m pytest"
        assert result[1]["name"] == "lint"
