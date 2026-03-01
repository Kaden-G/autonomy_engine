"""Tests for selective verify — auto/always_llm/never_llm modes."""

import json
from unittest.mock import MagicMock, patch

import pytest
import yaml

import engine.context
import engine.tracer as tracer
from engine.tracer import GENESIS_HASH, init_run


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path):
    """Point engine context at a temp dir and reset tracer module state."""
    engine.context.init(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    (state / "inputs").mkdir()
    (state / "tests").mkdir()
    (state / "inputs" / "ACCEPTANCE_CRITERIA.md").write_text("criteria")
    (state / "inputs" / "REQUIREMENTS.md").write_text("requirements")
    tracer._run_id = None
    tracer._prev_hash = GENESIS_HASH
    tracer._seq = 0
    yield
    tracer._run_id = None
    tracer._prev_hash = GENESIS_HASH
    tracer._seq = 0


def _make_evidence(exit_codes):
    """Build minimal evidence records with given exit codes."""
    return [
        {
            "name": f"check_{i}",
            "exit_code": ec,
            "command": "cmd",
            "started_at": "t",
            "finished_at": "t",
            "stdout": "",
            "stderr": "",
        }
        for i, ec in enumerate(exit_codes)
    ]


def _write_config(tmp_path, verify_cfg):
    """Write a config.yml with a verify section."""
    config = {"llm": {"provider": "claude", "claude": {"model": "test"}}, "verify": verify_cfg}
    (tmp_path / "config.yml").write_text(yaml.dump(config))


class TestAutoModeSkipsLlmOnPass:
    def test_generate_not_called(self, tmp_path):
        init_run()
        _write_config(tmp_path, {"mode": "auto", "llm_on_pass_summary": False})

        evidence = _make_evidence([0, 0])

        provider = MagicMock()
        provider.model = "test-model"
        provider.provider = "claude"
        provider.max_tokens = 16384

        with (
            patch("tasks.verify.load_all_evidence", return_value=evidence),
            patch(
                "tasks.verify.format_evidence_for_llm",
                return_value="evidence text",
            ),
            patch("tasks.verify.get_provider", return_value=provider),
        ):
            from tasks.verify import verify_system

            verify_system.fn()

        provider.generate.assert_not_called()

        # Verify output contains PASSED
        state = tmp_path / "state"
        content = (state / "tests" / "VERIFICATION.md").read_text()
        assert "PASSED" in content


class TestAutoModeSkipsLlmOnFail:
    def test_generate_not_called(self, tmp_path):
        init_run()
        _write_config(tmp_path, {"mode": "auto", "llm_on_fail_summary": False})

        evidence = _make_evidence([0, 1])

        provider = MagicMock()
        provider.model = "test-model"
        provider.provider = "claude"
        provider.max_tokens = 16384

        with (
            patch("tasks.verify.load_all_evidence", return_value=evidence),
            patch(
                "tasks.verify.format_evidence_for_llm",
                return_value="evidence text",
            ),
            patch("tasks.verify.get_provider", return_value=provider),
        ):
            from tasks.verify import verify_system

            verify_system.fn()

        provider.generate.assert_not_called()

        state = tmp_path / "state"
        content = (state / "tests" / "VERIFICATION.md").read_text()
        assert "FAILED" in content


class TestAlwaysLlmCallsModel:
    def test_generate_called(self, tmp_path):
        init_run()
        _write_config(tmp_path, {"mode": "always_llm"})

        evidence = _make_evidence([0, 0])

        # Set up prompt template
        templates = tmp_path / "templates" / "prompts"
        templates.mkdir(parents=True)
        (templates / "verify.txt").write_text("{evidence}{acceptance_criteria}{requirements}")

        provider = MagicMock()
        provider.generate.return_value = "LLM verification text"
        provider.model = "test-model"
        provider.provider = "claude"
        provider.max_tokens = 16384

        with (
            patch("tasks.verify.load_all_evidence", return_value=evidence),
            patch(
                "tasks.verify.format_evidence_for_llm",
                return_value="evidence text",
            ),
            patch("tasks.verify.get_provider", return_value=provider),
        ):
            from tasks.verify import verify_system

            verify_system.fn()

        provider.generate.assert_called_once()


class TestTraceRecordsVerifyDecision:
    def test_trace_has_verify_metadata(self, tmp_path):
        init_run()
        _write_config(tmp_path, {"mode": "auto", "llm_on_pass_summary": False})

        evidence = _make_evidence([0])

        with (
            patch("tasks.verify.load_all_evidence", return_value=evidence),
            patch(
                "tasks.verify.format_evidence_for_llm",
                return_value="evidence text",
            ),
        ):
            from tasks.verify import verify_system

            verify_system.fn()

        # Read trace and check extra fields
        run_id = tracer.get_run_id()
        trace_path = tmp_path / "state" / "runs" / run_id / "trace.jsonl"
        lines = trace_path.read_text().strip().splitlines()
        entry = json.loads(lines[-1])

        assert "extra" in entry
        extra = entry["extra"]
        assert extra["verify_mode"] == "auto"
        assert extra["llm_called"] is False
        assert "rationale" in extra
        assert extra["all_checks_passed"] is True
