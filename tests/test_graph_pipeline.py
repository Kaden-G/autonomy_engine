"""Tests for the LangGraph pipeline orchestration.

These tests verify:
1. Graph structure: nodes exist, edges route correctly
2. State management: PipelineState flows through nodes properly
3. Routing logic: conditional edges make correct decisions
4. Error handling: failures short-circuit to END
5. Retry loop: test failures route back to implement when retries available

Note: These are unit tests for the orchestration layer. They mock the actual
task functions (which have their own test suites in test_bootstrap.py, etc.)
to test graph behavior in isolation.
"""

import pytest
from unittest.mock import patch, MagicMock

from graph.state import Decision, PipelineState, StageResult, StageStatus
from graph.pipeline import (
    build_graph,
    route_after_init,
    route_after_bootstrap,
    route_after_design,
    route_after_implement,
    route_after_extract,
    route_after_test,
    route_after_verify,
)
from graph.nodes import implement_node, _handle_decision_gate, _pending_gate_path


# ═══════════════════════════════════════════════════════════════════════════════
# Routing function tests
# These are pure functions — no mocking needed, just state in → string out.
# ═══════════════════════════════════════════════════════════════════════════════


class TestRouteAfterInit:
    """Routing after initialization node."""

    def test_proceeds_on_success(self):
        state: PipelineState = {
            "stage_results": {
                "init": StageResult(status=StageStatus.PASSED),
            },
        }
        assert route_after_init(state) == "bootstrap"

    def test_aborts_on_failure(self):
        state: PipelineState = {
            "stage_results": {
                "init": StageResult(
                    status=StageStatus.FAILED,
                    error="Missing intake files",
                ),
            },
        }
        assert route_after_init(state) == "__end__"

    def test_proceeds_when_no_result(self):
        """Edge case: if init somehow doesn't set a result, proceed anyway."""
        state: PipelineState = {"stage_results": {}}
        assert route_after_init(state) == "bootstrap"


class TestRouteAfterBootstrap:
    def test_proceeds_on_success(self):
        state: PipelineState = {
            "stage_results": {
                "bootstrap": StageResult(status=StageStatus.PASSED),
            },
        }
        assert route_after_bootstrap(state) == "design"

    def test_aborts_on_failure(self):
        state: PipelineState = {
            "stage_results": {
                "bootstrap": StageResult(status=StageStatus.FAILED, error="boom"),
            },
        }
        assert route_after_bootstrap(state) == "__end__"


class TestRouteAfterDesign:
    def test_proceeds_on_success(self):
        state: PipelineState = {
            "stage_results": {
                "design": StageResult(status=StageStatus.PASSED),
            },
        }
        assert route_after_design(state) == "implement"

    def test_aborts_on_failure(self):
        state: PipelineState = {
            "stage_results": {
                "design": StageResult(status=StageStatus.FAILED, error="LLM error"),
            },
        }
        assert route_after_design(state) == "__end__"


class TestRouteAfterImplement:
    def test_proceeds_on_success(self):
        state: PipelineState = {
            "stage_results": {
                "implement": StageResult(status=StageStatus.PASSED),
            },
        }
        assert route_after_implement(state) == "extract"

    def test_aborts_on_failure(self):
        state: PipelineState = {
            "stage_results": {
                "implement": StageResult(status=StageStatus.FAILED),
            },
        }
        assert route_after_implement(state) == "__end__"


class TestRouteAfterExtract:
    def test_proceeds_on_success(self):
        state: PipelineState = {
            "stage_results": {
                "extract": StageResult(status=StageStatus.PASSED),
            },
        }
        assert route_after_extract(state) == "test"

    def test_aborts_on_failure(self):
        state: PipelineState = {
            "stage_results": {
                "extract": StageResult(
                    status=StageStatus.FAILED,
                    error="Circuit breaker tripped",
                ),
            },
        }
        assert route_after_extract(state) == "__end__"


class TestRouteAfterTest:
    """The most complex routing — handles retries, aborts, and pass-through."""

    def test_proceeds_to_verify_on_pass(self):
        state: PipelineState = {
            "stage_results": {
                "test": StageResult(status=StageStatus.PASSED),
            },
            "retry_count": 0,
            "max_retries": 1,
        }
        assert route_after_test(state) == "verify"

    def test_proceeds_to_verify_on_skip(self):
        state: PipelineState = {
            "stage_results": {
                "test": StageResult(status=StageStatus.SKIPPED),
            },
            "retry_count": 0,
            "max_retries": 1,
        }
        assert route_after_test(state) == "verify"

    def test_proceeds_to_verify_with_continue_decision(self):
        """Tests failed but human chose 'continue' — treat as passed."""
        state: PipelineState = {
            "stage_results": {
                "test": StageResult(
                    status=StageStatus.PASSED,
                    metadata={"decision": "continue", "had_failures": True},
                ),
            },
            "retry_count": 0,
            "max_retries": 1,
        }
        assert route_after_test(state) == "verify"

    def test_aborts_on_abort_decision(self):
        """Tests failed and human chose 'abort'."""
        state: PipelineState = {
            "stage_results": {
                "test": StageResult(
                    status=StageStatus.FAILED,
                    error="Aborted by human",
                    metadata={"decision": "abort"},
                ),
            },
            "error": "Pipeline aborted at test triage",
            "retry_count": 0,
            "max_retries": 1,
        }
        assert route_after_test(state) == "__end__"

    def test_retries_implementation_when_budget_available(self):
        """Tests failed, no human decision, retries remaining → loop back."""
        state: PipelineState = {
            "stage_results": {
                "test": StageResult(status=StageStatus.FAILED, error="tests failed"),
            },
            "retry_count": 0,
            "max_retries": 1,
        }
        assert route_after_test(state) == "implement"

    def test_ends_when_retry_budget_exhausted(self):
        """Tests failed, no retries left → END."""
        state: PipelineState = {
            "stage_results": {
                "test": StageResult(status=StageStatus.FAILED, error="tests failed"),
            },
            "retry_count": 1,
            "max_retries": 1,
        }
        assert route_after_test(state) == "__end__"

    def test_ends_when_retries_disabled(self):
        """Tests failed, max_retries=0 → no retry, END."""
        state: PipelineState = {
            "stage_results": {
                "test": StageResult(status=StageStatus.FAILED, error="tests failed"),
            },
            "retry_count": 0,
            "max_retries": 0,
        }
        assert route_after_test(state) == "__end__"

    def test_no_result_ends(self):
        state: PipelineState = {
            "stage_results": {},
            "retry_count": 0,
            "max_retries": 1,
        }
        assert route_after_test(state) == "__end__"


class TestImplementNodeRetryBudget:
    """Regression tests: implement_node must increment retry_count on re-entry.

    Without this, route_after_test can never exhaust the retry budget —
    retry_count stays at 0 forever and the test → implement loop runs until
    LangGraph's recursion limit trips. See graph/state.py::retry_count docstring.
    """

    def test_first_call_does_not_increment(self):
        """Initial implement run has no prior implement result — retry_count stays 0."""
        state: PipelineState = {
            "stage_results": {},
            "retry_count": 0,
            "max_retries": 1,
        }
        with patch("graph.nodes.implement_system"):
            update = implement_node(state)
        assert "retry_count" not in update

    def test_retry_call_increments(self):
        """Re-entry (prior implement result in state) must increment retry_count."""
        state: PipelineState = {
            "stage_results": {
                "implement": StageResult(status=StageStatus.PASSED),
                "test": StageResult(status=StageStatus.FAILED, error="tests failed"),
            },
            "retry_count": 0,
            "max_retries": 1,
        }
        with patch("graph.nodes.implement_system"):
            update = implement_node(state)
        assert update["retry_count"] == 1

    def test_retry_increment_on_implement_failure(self):
        """Even if implement itself raises, retry_count must still increment."""
        state: PipelineState = {
            "stage_results": {
                "implement": StageResult(status=StageStatus.PASSED),
                "test": StageResult(status=StageStatus.FAILED, error="tests failed"),
            },
            "retry_count": 2,
            "max_retries": 3,
        }
        with patch("graph.nodes.implement_system", side_effect=RuntimeError("boom")):
            update = implement_node(state)
        assert update["retry_count"] == 3
        assert update["stage_results"]["implement"].status == StageStatus.FAILED

    def test_budget_actually_exhausts(self):
        """End-to-end: simulate implement → test-fail loop until route_after_test ends."""
        state: PipelineState = {
            "stage_results": {},
            "retry_count": 0,
            "max_retries": 2,
        }
        # Iteration 1 (first implement) — no prior implement, no increment.
        with patch("graph.nodes.implement_system"):
            state.update(implement_node(state))
        state["stage_results"]["test"] = StageResult(
            status=StageStatus.FAILED, error="fail"
        )
        assert route_after_test(state) == "implement"

        # Iteration 2 (retry 1) — prior implement present, retry_count → 1.
        with patch("graph.nodes.implement_system"):
            state.update(implement_node(state))
        assert state["retry_count"] == 1
        state["stage_results"]["test"] = StageResult(
            status=StageStatus.FAILED, error="fail"
        )
        assert route_after_test(state) == "implement"

        # Iteration 3 (retry 2) — retry_count → 2, budget hit on next test-fail.
        with patch("graph.nodes.implement_system"):
            state.update(implement_node(state))
        assert state["retry_count"] == 2
        state["stage_results"]["test"] = StageResult(
            status=StageStatus.FAILED, error="fail"
        )
        assert route_after_test(state) == "__end__"


class TestRouteAfterVerify:
    def test_completes_on_success(self):
        state: PipelineState = {
            "stage_results": {
                "verify": StageResult(status=StageStatus.PASSED),
            },
        }
        assert route_after_verify(state) == "complete"

    def test_aborts_on_failure(self):
        state: PipelineState = {
            "stage_results": {
                "verify": StageResult(
                    status=StageStatus.FAILED,
                    error="Rejected by reviewer",
                ),
            },
        }
        assert route_after_verify(state) == "__end__"


# ═══════════════════════════════════════════════════════════════════════════════
# Pending-gate file + status.json (dashboard polling contract)
# ═══════════════════════════════════════════════════════════════════════════════


class TestPendingGateFile:
    """_handle_decision_gate must drop pending_gate.json before calling interrupt()
    (so the dashboard can poll without reopening the checkpoint), and must remove
    it after resume. Without this, the dashboard has no way to render a gate form
    from a different process than the one that paused."""

    def test_write_then_clear_on_resume(self, tmp_path, monkeypatch):
        import json

        import engine.context
        from engine.decision_gates import DecisionRequired

        engine.context.init(tmp_path)
        (tmp_path / "state").mkdir(parents=True, exist_ok=True)

        state = {"run_id": "test-run-wc"}
        exc = DecisionRequired(gate="g1", stage="design", options=["a", "b"])
        pg_path = _pending_gate_path(state)

        from graph import nodes as nodes_mod

        class FakePolicy:
            policy = "pause"
            default_option = None

        # interrupt() mock: side-effect — check the file exists before "resuming".
        captured = {}

        def fake_interrupt(payload):
            captured["file_existed_at_interrupt"] = pg_path.exists()
            captured["payload"] = payload
            return {"choice": "a", "rationale": "why"}

        monkeypatch.setattr(nodes_mod, "get_gate_policy", lambda stage: FakePolicy())
        monkeypatch.setattr(nodes_mod, "interrupt", fake_interrupt)
        monkeypatch.setattr(nodes_mod, "save_decision", lambda **kw: None)

        _handle_decision_gate(exc, state)

        # Before interrupt() fired, the pending-gate file was on disk.
        assert captured["file_existed_at_interrupt"] is True
        # Payload has the shape the dashboard expects.
        assert captured["payload"]["gate"] == "g1"
        assert captured["payload"]["options"] == ["a", "b"]
        # After resume, the marker is cleaned up so stale gates don't confuse
        # the polling loop.
        assert not pg_path.exists()

    def test_skip_policy_writes_no_file(self, tmp_path, monkeypatch):
        import engine.context
        from engine.decision_gates import DecisionRequired

        engine.context.init(tmp_path)
        (tmp_path / "state").mkdir(parents=True, exist_ok=True)

        state = {"run_id": "test-run-skip"}
        exc = DecisionRequired(gate="g", stage="test", options=["x", "y"])

        from graph import nodes as nodes_mod

        class FakePolicy:
            policy = "skip"
            default_option = None

        monkeypatch.setattr(nodes_mod, "get_gate_policy", lambda stage: FakePolicy())
        result = _handle_decision_gate(exc, state)

        assert result is None
        assert not _pending_gate_path(state).exists()


class TestRunStatusFile:
    """run_pipeline() must leave a status.json the dashboard can poll to detect
    paused vs complete vs failed without parsing subprocess stdout."""

    def test_status_reflects_interrupt(self, tmp_path, monkeypatch):
        import json

        import engine.context
        from graph.pipeline import _write_run_status

        engine.context.init(tmp_path)
        (tmp_path / "state").mkdir(parents=True, exist_ok=True)

        result = {
            "run_id": "st-1",
            "__interrupt__": [{"value": {"gate": "g", "stage": "design", "options": ["a", "b"]}}],
            "current_stage": "design",
        }
        _write_run_status(result, thread_id="tid-1")

        status_path = tmp_path / "state" / "runs" / "st-1" / "status.json"
        status = json.loads(status_path.read_text())
        assert status["state"] == "paused"
        assert status["thread_id"] == "tid-1"
        assert status["current_stage"] == "design"

    def test_status_reflects_complete(self, tmp_path):
        import json

        import engine.context
        from graph.pipeline import _write_run_status

        engine.context.init(tmp_path)
        (tmp_path / "state").mkdir(parents=True, exist_ok=True)

        result = {"run_id": "st-2", "current_stage": "complete"}
        _write_run_status(result, thread_id="tid-2")

        status_path = tmp_path / "state" / "runs" / "st-2" / "status.json"
        assert json.loads(status_path.read_text())["state"] == "complete"

    def test_status_reflects_failed(self, tmp_path):
        import json

        import engine.context
        from graph.pipeline import _write_run_status

        engine.context.init(tmp_path)
        (tmp_path / "state").mkdir(parents=True, exist_ok=True)

        result = {"run_id": "st-3", "error": "design failed: bad contract"}
        _write_run_status(result, thread_id="tid-3")

        status = json.loads((tmp_path / "state" / "runs" / "st-3" / "status.json").read_text())
        assert status["state"] == "failed"
        assert "design failed" in status["error"]


# ═══════════════════════════════════════════════════════════════════════════════
# Graph structure tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestGraphStructure:
    """Verify the graph compiles and has the expected shape."""

    def test_graph_compiles(self):
        """Graph should compile without errors."""
        graph = build_graph()
        assert graph is not None

    def test_graph_has_all_nodes(self):
        """All pipeline stages should be present as nodes."""
        graph = build_graph()
        # The compiled graph's nodes are accessible via .nodes
        node_names = set(graph.nodes.keys())
        expected = {"init", "bootstrap", "design", "implement", "extract", "test", "verify", "complete"}
        # LangGraph adds __start__ and __end__ nodes
        assert expected.issubset(node_names), f"Missing nodes: {expected - node_names}"


# ═══════════════════════════════════════════════════════════════════════════════
# State schema tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestStageResult:
    def test_immutable(self):
        """StageResult should be frozen (immutable after creation)."""
        result = StageResult(status=StageStatus.PASSED)
        with pytest.raises(AttributeError):
            result.status = StageStatus.FAILED

    def test_defaults(self):
        result = StageResult(status=StageStatus.PENDING)
        assert result.artifacts == []
        assert result.error is None
        assert result.metadata == {}

    def test_with_metadata(self):
        result = StageResult(
            status=StageStatus.PASSED,
            artifacts=["designs/ARCHITECTURE.md"],
            metadata={"cache_hit": True},
        )
        assert result.artifacts == ["designs/ARCHITECTURE.md"]
        assert result.metadata["cache_hit"] is True


class TestStageStatus:
    def test_all_statuses_exist(self):
        """Verify all expected statuses are defined."""
        assert StageStatus.PENDING == "pending"
        assert StageStatus.RUNNING == "running"
        assert StageStatus.PASSED == "passed"
        assert StageStatus.FAILED == "failed"
        assert StageStatus.SKIPPED == "skipped"
        assert StageStatus.AWAITING_DECISION == "awaiting_decision"


class TestDecision:
    def test_immutable(self):
        decision = Decision(
            gate="test_gate",
            stage="test",
            selected="continue",
            actor="human:kaden",
        )
        with pytest.raises(AttributeError):
            decision.selected = "abort"

    def test_default_rationale(self):
        decision = Decision(
            gate="test_gate",
            stage="test",
            selected="continue",
            actor="auto-policy",
        )
        assert decision.rationale == ""
