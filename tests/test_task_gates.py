"""Tests for gate triggers in tasks.test and tasks.verify."""

from unittest.mock import MagicMock, patch

import pytest

import engine.context
import engine.tracer as tracer
from engine.decision_gates import DecisionRequired, save_decision
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


# ── test_system gate triggers ──────────────────────────────────────────────


class TestTestSystemGate:
    """Gate triggers in tasks.test.test_system."""

    def _make_evidence(self, exit_codes):
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

    @patch("tasks.test.trace")
    @patch("tasks.test.save_state_file")
    @patch("tasks.test.load_all_evidence")
    @patch("tasks.test.save_evidence")
    @patch("tasks.test.auto_detect_checks", return_value=[])
    @patch("tasks.test._get_project_dir")
    @patch("tasks.test.load_configured_checks")
    def test_raises_on_failure(
        self, mock_checks, mock_proj_dir, mock_auto, mock_save_ev, mock_load_ev, mock_save_state, mock_trace
    ):
        """When checks fail and no decision exists, DecisionRequired is raised."""
        init_run()
        mock_checks.return_value = []  # no checks to run
        mock_save_ev.return_value = None
        evidence = self._make_evidence([0, 1])  # one pass, one fail
        mock_load_ev.return_value = evidence

        from tasks.test import test_system

        with pytest.raises(DecisionRequired, match="test_failure_triage"):
            test_system.fn()

    @patch("tasks.test.trace")
    @patch("tasks.test.save_state_file")
    @patch("tasks.test.load_all_evidence")
    @patch("tasks.test.save_evidence")
    @patch("tasks.test.auto_detect_checks", return_value=[])
    @patch("tasks.test._get_project_dir")
    @patch("tasks.test.load_configured_checks")
    def test_no_raise_when_all_pass(
        self, mock_checks, mock_proj_dir, mock_auto, mock_save_ev, mock_load_ev, mock_save_state, mock_trace
    ):
        """When all checks pass, no exception is raised."""
        init_run()
        mock_checks.return_value = []
        mock_save_ev.return_value = None
        evidence = self._make_evidence([0, 0])
        mock_load_ev.return_value = evidence

        from tasks.test import test_system

        test_system.fn()  # should not raise

    @patch("tasks.test.trace")
    @patch("tasks.test.save_state_file")
    @patch("tasks.test.load_all_evidence")
    @patch("tasks.test.save_evidence")
    @patch("tasks.test.auto_detect_checks", return_value=[])
    @patch("tasks.test._get_project_dir")
    @patch("tasks.test.load_configured_checks")
    def test_no_raise_when_decision_exists(
        self, mock_checks, mock_proj_dir, mock_auto, mock_save_ev, mock_load_ev, mock_save_state, mock_trace
    ):
        """When a continue decision already exists, the task runs without raising."""
        init_run()
        save_decision("test_failure_triage", "test", ["continue", "abort"], "continue")
        mock_checks.return_value = []
        mock_save_ev.return_value = None
        evidence = self._make_evidence([0, 1])  # failures present
        mock_load_ev.return_value = evidence

        from tasks.test import test_system

        test_system.fn()  # should not raise — decision exists

    def test_abort_decision_raises_runtime_error(self):
        """When a previous triage decision was abort, RuntimeError is raised."""
        init_run()
        save_decision("test_failure_triage", "test", ["continue", "abort"], "abort")

        from tasks.test import test_system

        with pytest.raises(RuntimeError, match="abort"):
            test_system.fn()


# ── verify_system gate triggers ────────────────────────────────────────────


class TestVerifySystemGate:
    """Gate triggers in tasks.verify.verify_system."""

    @patch("tasks.verify.trace")
    @patch("tasks.verify.save_state_file")
    @patch("tasks.verify.hash_prompt", return_value="abc123")
    @patch("tasks.verify.get_provider")
    @patch("tasks.verify.load_state_file")
    @patch("tasks.verify.format_evidence_for_llm", return_value="evidence text")
    @patch("tasks.verify.load_all_evidence", return_value=[])
    @patch("tasks.verify.get_prompts_dir")
    def test_raises_on_rejected(
        self,
        mock_prompts,
        mock_load_ev,
        mock_fmt,
        mock_load_state,
        mock_provider,
        mock_hash,
        mock_save_state,
        mock_trace,
        tmp_path,
    ):
        """When LLM output contains REJECTED, DecisionRequired is raised."""
        init_run()
        # Set up prompt template
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "verify.txt").write_text("{evidence}{acceptance_criteria}{requirements}")
        mock_prompts.return_value = prompts_dir
        mock_load_state.return_value = "criteria"

        provider = MagicMock()
        provider.generate.return_value = "Verdict: REJECTED — does not meet criteria"
        provider.model = "test-model"
        mock_provider.return_value = provider

        from tasks.verify import verify_system

        with pytest.raises(DecisionRequired, match="verification_review"):
            verify_system.fn()

    @patch("tasks.verify.trace")
    @patch("tasks.verify.save_state_file")
    @patch("tasks.verify.hash_prompt", return_value="abc123")
    @patch("tasks.verify.get_provider")
    @patch("tasks.verify.load_state_file")
    @patch("tasks.verify.format_evidence_for_llm", return_value="evidence text")
    @patch("tasks.verify.load_all_evidence", return_value=[])
    @patch("tasks.verify.get_prompts_dir")
    def test_no_raise_on_approved(
        self,
        mock_prompts,
        mock_load_ev,
        mock_fmt,
        mock_load_state,
        mock_provider,
        mock_hash,
        mock_save_state,
        mock_trace,
        tmp_path,
    ):
        """When LLM output contains APPROVED (not REJECTED), no exception."""
        init_run()
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "verify.txt").write_text("{evidence}{acceptance_criteria}{requirements}")
        mock_prompts.return_value = prompts_dir
        mock_load_state.return_value = "criteria"

        provider = MagicMock()
        provider.generate.return_value = "Verdict: APPROVED — meets all criteria"
        provider.model = "test-model"
        mock_provider.return_value = provider

        from tasks.verify import verify_system

        verify_system.fn()  # should not raise

    @patch("tasks.verify.trace")
    @patch("tasks.verify.save_state_file")
    @patch("tasks.verify.hash_prompt", return_value="abc123")
    @patch("tasks.verify.get_provider")
    @patch("tasks.verify.load_state_file")
    @patch("tasks.verify.format_evidence_for_llm", return_value="evidence text")
    @patch("tasks.verify.load_all_evidence", return_value=[])
    @patch("tasks.verify.get_prompts_dir")
    def test_no_raise_on_approved_with_caveats(
        self,
        mock_prompts,
        mock_load_ev,
        mock_fmt,
        mock_load_state,
        mock_provider,
        mock_hash,
        mock_save_state,
        mock_trace,
        tmp_path,
    ):
        """APPROVED_WITH_CAVEATS does not contain 'REJECTED' so no exception."""
        init_run()
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "verify.txt").write_text("{evidence}{acceptance_criteria}{requirements}")
        mock_prompts.return_value = prompts_dir
        mock_load_state.return_value = "criteria"

        provider = MagicMock()
        provider.generate.return_value = "Verdict: APPROVED_WITH_CAVEATS"
        provider.model = "test-model"
        mock_provider.return_value = provider

        from tasks.verify import verify_system

        verify_system.fn()  # should not raise

    def test_reject_decision_raises_runtime_error(self):
        """When a previous review decision was reject, RuntimeError is raised."""
        init_run()
        save_decision("verification_review", "verify", ["accept", "reject"], "reject")

        from tasks.verify import verify_system

        with pytest.raises(RuntimeError, match="reject"):
            verify_system.fn()
