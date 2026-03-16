"""Tests for engine.decision_gates — run-scoped, validated decisions."""

import json

import pytest
import yaml

import engine.context
import engine.tracer as tracer
from engine.tracer import GENESIS_HASH, init_run
from engine.decision_gates import (
    DecisionRequired,
    GatePolicy,
    _VALID_POLICIES,
    _resolve_actor,
    decision_exists,
    get_gate_policy,
    handle_gate,
    load_decision,
    save_decision,
    _gate_slug,
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


# ── DecisionRequired exception ──────────────────────────────────────────────


class TestDecisionRequired:
    def test_carries_gate_stage_options(self):
        exc = DecisionRequired("arch_choice", "design", ["A", "B"])
        assert exc.gate == "arch_choice"
        assert exc.stage == "design"
        assert exc.options == ["A", "B"]

    def test_message_includes_gate_and_stage(self):
        exc = DecisionRequired("arch_choice", "design", ["A", "B"])
        assert "design" in str(exc)
        assert "arch_choice" in str(exc)


# ── save_decision + load_decision ────────────────────────────────────────────


class TestSaveAndLoadDecision:
    def test_save_creates_json_file(self, tmp_path):
        run_id = init_run()
        save_decision("my_gate", "design", ["A", "B"], "A")
        path = tmp_path / "state" / "runs" / run_id / "decisions" / "my_gate.json"
        assert path.exists()

    def test_load_returns_structured_record(self, tmp_path):
        init_run()
        save_decision("my_gate", "design", ["A", "B"], "B", rationale="better fit")
        record = load_decision("my_gate")
        assert record["gate"] == "my_gate"
        assert record["stage"] == "design"
        assert record["allowed_options"] == ["A", "B"]
        assert record["selected"] == "B"
        # actor is resolved — with USER env set it's "human:<username>"
        assert record["actor"].startswith("human")
        assert record["rationale"] == "better fit"
        assert "timestamp" in record
        assert "run_id" in record

    def test_record_includes_run_id(self, tmp_path):
        run_id = init_run()
        save_decision("my_gate", "design", ["X"], "X")
        record = load_decision("my_gate")
        assert record["run_id"] == run_id

    def test_custom_actor(self, tmp_path):
        init_run()
        save_decision("g", "s", ["opt"], "opt", actor="ci-bot")
        record = load_decision("g")
        assert record["actor"] == "ci-bot"

    def test_record_is_valid_json(self, tmp_path):
        run_id = init_run()
        save_decision("my_gate", "design", ["A", "B"], "A")
        path = tmp_path / "state" / "runs" / run_id / "decisions" / "my_gate.json"
        data = json.loads(path.read_text())
        assert isinstance(data, dict)


# ── Choice validation ────────────────────────────────────────────────────────


class TestChoiceValidation:
    def test_rejects_invalid_choice(self, tmp_path):
        init_run()
        with pytest.raises(ValueError, match="Invalid choice"):
            save_decision("gate", "design", ["A", "B"], "C")

    def test_rejects_empty_choice(self, tmp_path):
        init_run()
        with pytest.raises(ValueError, match="Invalid choice"):
            save_decision("gate", "design", ["A", "B"], "")

    def test_rejects_case_mismatch(self, tmp_path):
        init_run()
        with pytest.raises(ValueError, match="Invalid choice"):
            save_decision("gate", "design", ["Option A"], "option a")

    def test_accepts_valid_choice(self, tmp_path):
        init_run()
        save_decision("gate", "design", ["A", "B", "C"], "B")
        record = load_decision("gate")
        assert record["selected"] == "B"

    def test_error_message_shows_allowed(self, tmp_path):
        init_run()
        with pytest.raises(ValueError, match=r"\['X', 'Y'\]"):
            save_decision("gate", "stage", ["X", "Y"], "Z")


# ── decision_exists ─────────────────────────────────────────────────────────


class TestDecisionExists:
    def test_false_when_no_decision(self, tmp_path):
        init_run()
        assert decision_exists("no_such_gate") is False

    def test_true_after_save(self, tmp_path):
        init_run()
        save_decision("my_gate", "design", ["A"], "A")
        assert decision_exists("my_gate") is True


# ── Run isolation ────────────────────────────────────────────────────────────


class TestRunIsolation:
    def test_decision_from_run1_not_visible_in_run2(self, tmp_path):
        init_run()
        save_decision("shared_gate", "design", ["A", "B"], "A")
        assert decision_exists("shared_gate") is True

        # Start a new run — the old decision must NOT carry over
        init_run()
        assert decision_exists("shared_gate") is False

    def test_same_gate_different_choices_per_run(self, tmp_path):
        r1 = init_run()
        save_decision("gate", "design", ["A", "B"], "A")
        record_r1 = load_decision("gate")

        r2 = init_run()
        save_decision("gate", "design", ["A", "B"], "B")
        record_r2 = load_decision("gate")

        assert record_r1["selected"] == "A"
        assert record_r1["run_id"] == r1
        assert record_r2["selected"] == "B"
        assert record_r2["run_id"] == r2

    def test_load_fails_in_new_run(self, tmp_path):
        init_run()
        save_decision("gate", "design", ["A"], "A")

        init_run()
        with pytest.raises(FileNotFoundError, match="No decision record"):
            load_decision("gate")


# ── load_decision error cases ────────────────────────────────────────────────


class TestLoadDecisionErrors:
    def test_raises_when_gate_not_found(self, tmp_path):
        init_run()
        with pytest.raises(FileNotFoundError, match="No decision record"):
            load_decision("nonexistent_gate")

    def test_raises_without_active_run(self, tmp_path):
        with pytest.raises(RuntimeError, match="No active run"):
            load_decision("any_gate")


# ── _gate_slug ───────────────────────────────────────────────────────────────


class TestGateSlug:
    def test_lowercases(self):
        assert _gate_slug("My Gate") == "my_gate"

    def test_spaces_to_underscores(self):
        assert _gate_slug("architecture choice needed") == "architecture_choice_needed"

    def test_truncates_at_60(self):
        long_name = "a" * 100
        assert len(_gate_slug(long_name)) == 60

    def test_already_slug(self):
        assert _gate_slug("my_gate") == "my_gate"


# ── GatePolicy ─────────────────────────────────────────────────────────────


class TestGatePolicy:
    def test_valid_policies_accepted(self):
        for p in _VALID_POLICIES:
            gp = GatePolicy(stage="test", policy=p)
            assert gp.policy == p

    def test_invalid_policy_rejected(self):
        with pytest.raises(ValueError, match="Invalid gate policy"):
            GatePolicy(stage="test", policy="bogus")

    def test_frozen_immutability(self):
        gp = GatePolicy(stage="test", policy="skip")
        with pytest.raises(AttributeError):
            gp.policy = "auto"

    def test_default_option_none(self):
        gp = GatePolicy(stage="test", policy="skip")
        assert gp.default_option is None

    def test_default_option_set(self):
        gp = GatePolicy(stage="test", policy="auto", default_option="continue")
        assert gp.default_option == "continue"


# ── get_gate_policy ────────────────────────────────────────────────────────


class TestGetGatePolicy:
    def test_design_defaults_to_pause_without_file(self, tmp_path):
        """With no DECISION_GATES.yml, design stage defaults to pause."""
        # tmp_path has no templates/ dir, so the file won't be found —
        # but context points at tmp_path, so get_templates_dir falls back
        # to engine default.  We create a templates dir with no YAML file.
        templates = tmp_path / "templates"
        templates.mkdir()
        engine.context.init(tmp_path)
        policy = get_gate_policy("design")
        assert policy.policy == "pause"

    def test_unknown_stage_defaults_to_skip(self, tmp_path):
        templates = tmp_path / "templates"
        templates.mkdir()
        engine.context.init(tmp_path)
        policy = get_gate_policy("unknown_stage")
        assert policy.policy == "skip"

    def test_loads_from_yaml(self, tmp_path):
        templates = tmp_path / "templates"
        templates.mkdir()
        gates_file = templates / "DECISION_GATES.yml"
        gates_file.write_text(
            yaml.dump(
                {
                    "gates": {
                        "test": {
                            "policy": "auto",
                            "default_option": "continue",
                            "description": "test gate",
                        }
                    }
                }
            )
        )
        engine.context.init(tmp_path)
        policy = get_gate_policy("test")
        assert policy.policy == "auto"
        assert policy.default_option == "continue"
        assert policy.description == "test gate"

    def test_missing_stage_falls_back(self, tmp_path):
        templates = tmp_path / "templates"
        templates.mkdir()
        gates_file = templates / "DECISION_GATES.yml"
        gates_file.write_text(yaml.dump({"gates": {"design": {"policy": "pause"}}}))
        engine.context.init(tmp_path)
        # verify defaults to "pause" (not in YAML, falls back to _DEFAULT_POLICIES)
        policy = get_gate_policy("verify")
        assert policy.policy == "pause"
        # unknown stages still default to "skip"
        policy = get_gate_policy("unknown_stage")
        assert policy.policy == "skip"

    def test_malformed_yaml_falls_back(self, tmp_path):
        templates = tmp_path / "templates"
        templates.mkdir()
        gates_file = templates / "DECISION_GATES.yml"
        gates_file.write_text("not: [valid: yaml: {{")
        engine.context.init(tmp_path)
        policy = get_gate_policy("design")
        assert policy.policy == "pause"

    def test_backward_compatible_design_default(self, tmp_path):
        templates = tmp_path / "templates"
        templates.mkdir()
        engine.context.init(tmp_path)
        assert get_gate_policy("design").policy == "pause"

    def test_backward_compatible_implement_default(self, tmp_path):
        templates = tmp_path / "templates"
        templates.mkdir()
        engine.context.init(tmp_path)
        assert get_gate_policy("implement").policy == "skip"


# ── handle_gate ────────────────────────────────────────────────────────────


class TestHandleGate:
    def test_no_exception_passthrough(self, tmp_path):
        """When the task doesn't raise, handle_gate just returns."""
        engine.context.init(tmp_path)
        (tmp_path / "templates").mkdir()
        called = []

        def task_fn():
            called.append(1)

        handle_gate(task_fn, "design", lambda e: None)
        assert called == [1]

    def test_skip_swallows_exception(self, tmp_path):
        """With skip policy, DecisionRequired is swallowed."""
        templates = tmp_path / "templates"
        templates.mkdir()
        (templates / "DECISION_GATES.yml").write_text(
            yaml.dump({"gates": {"test": {"policy": "skip"}}})
        )
        engine.context.init(tmp_path)
        init_run()

        call_count = []

        def task_fn():
            call_count.append(1)
            if len(call_count) == 1:
                raise DecisionRequired("triage", "test", ["continue", "abort"])

        handle_gate(task_fn, "test", lambda e: None)
        # task was called once, exception swallowed, no re-run
        assert len(call_count) == 1

    def test_auto_saves_decision_and_reruns(self, tmp_path):
        """With auto policy, a decision is saved and the task re-runs."""
        templates = tmp_path / "templates"
        templates.mkdir()
        (templates / "DECISION_GATES.yml").write_text(
            yaml.dump({"gates": {"test": {"policy": "auto", "default_option": "continue"}}})
        )
        engine.context.init(tmp_path)
        (tmp_path / "state").mkdir(exist_ok=True)
        init_run()

        call_count = []

        def task_fn():
            call_count.append(1)
            if len(call_count) == 1:
                raise DecisionRequired("triage", "test", ["continue", "abort"])

        handle_gate(task_fn, "test", lambda e: None)
        assert len(call_count) == 2
        record = load_decision("triage")
        assert record["selected"] == "continue"
        assert record["actor"] == "auto-policy"

    def test_auto_uses_first_option_when_no_default(self, tmp_path):
        """When default_option is null, auto picks the first option."""
        templates = tmp_path / "templates"
        templates.mkdir()
        (templates / "DECISION_GATES.yml").write_text(
            yaml.dump({"gates": {"test": {"policy": "auto", "default_option": None}}})
        )
        engine.context.init(tmp_path)
        (tmp_path / "state").mkdir(exist_ok=True)
        init_run()

        call_count = []

        def task_fn():
            call_count.append(1)
            if len(call_count) == 1:
                raise DecisionRequired("triage", "test", ["first", "second"])

        handle_gate(task_fn, "test", lambda e: None)
        record = load_decision("triage")
        assert record["selected"] == "first"

    def test_pause_calls_on_pause_and_reruns(self, tmp_path):
        """With pause policy, on_pause is called, then task re-runs."""
        templates = tmp_path / "templates"
        templates.mkdir()
        (templates / "DECISION_GATES.yml").write_text(
            yaml.dump({"gates": {"design": {"policy": "pause"}}})
        )
        engine.context.init(tmp_path)
        (tmp_path / "state").mkdir(exist_ok=True)
        init_run()

        pause_calls = []
        call_count = []

        def on_pause(exc):
            pause_calls.append(exc)
            # Simulate human saving a decision
            save_decision(
                gate=exc.gate,
                stage=exc.stage,
                allowed_options=exc.options,
                selected=exc.options[0],
            )

        def task_fn():
            call_count.append(1)
            if len(call_count) == 1:
                raise DecisionRequired("arch", "design", ["A", "B"])

        handle_gate(task_fn, "design", on_pause)
        assert len(pause_calls) == 1
        assert len(call_count) == 2


# ── _resolve_actor ─────────────────────────────────────────────────────────


class TestResolveActor:
    def test_explicit_actor_wins(self, monkeypatch):
        monkeypatch.setenv("AE_ACTOR", "env-actor")
        assert _resolve_actor("explicit") == "explicit"

    def test_ae_actor_env_var(self, monkeypatch):
        monkeypatch.setenv("AE_ACTOR", "ci-pipeline")
        monkeypatch.delenv("USER", raising=False)
        assert _resolve_actor(None) == "ci-pipeline"

    def test_system_user_fallback(self, monkeypatch):
        monkeypatch.delenv("AE_ACTOR", raising=False)
        monkeypatch.setenv("USER", "testuser")
        assert _resolve_actor(None) == "human:testuser"

    def test_final_fallback_is_human(self, monkeypatch):
        monkeypatch.delenv("AE_ACTOR", raising=False)
        monkeypatch.delenv("USER", raising=False)
        monkeypatch.delenv("LOGNAME", raising=False)
        monkeypatch.delenv("USERNAME", raising=False)
        assert _resolve_actor(None) == "human"


# ── Decision trace entries ──────────────────────────────────────────────────


class TestDecisionTraceEntry:
    def test_save_decision_emits_trace_entry(self, tmp_path):
        run_id = init_run()
        save_decision("arch", "design", ["A", "B"], "A", actor="tester")
        trace_file = tmp_path / "state" / "runs" / run_id / "trace.jsonl"
        assert trace_file.exists()
        entries = [json.loads(line) for line in trace_file.read_text().strip().splitlines()]
        decision_entries = [e for e in entries if e["task"] == "decision"]
        assert len(decision_entries) == 1
        entry = decision_entries[0]
        assert entry["extra"]["gate"] == "arch"
        assert entry["extra"]["stage"] == "design"
        assert entry["extra"]["selected"] == "A"
        assert entry["extra"]["actor"] == "tester"

    def test_trace_entry_has_decision_artifact_output(self, tmp_path):
        run_id = init_run()
        save_decision("gate1", "test", ["X"], "X")
        trace_file = tmp_path / "state" / "runs" / run_id / "trace.jsonl"
        entries = [json.loads(line) for line in trace_file.read_text().strip().splitlines()]
        decision_entries = [e for e in entries if e["task"] == "decision"]
        entry = decision_entries[0]
        # Output should reference the decision file path
        output_keys = list(entry["outputs"].keys())
        assert len(output_keys) == 1
        assert "decisions/gate1.json" in output_keys[0]

    def test_trace_entry_has_rationale_flag(self, tmp_path):
        run_id = init_run()
        save_decision("g", "s", ["A"], "A", rationale="because")
        trace_file = tmp_path / "state" / "runs" / run_id / "trace.jsonl"
        entries = [json.loads(line) for line in trace_file.read_text().strip().splitlines()]
        decision_entries = [e for e in entries if e["task"] == "decision"]
        assert decision_entries[0]["extra"]["has_rationale"] is True

    def test_trace_entry_no_rationale_flag(self, tmp_path):
        run_id = init_run()
        save_decision("g", "s", ["A"], "A")
        trace_file = tmp_path / "state" / "runs" / run_id / "trace.jsonl"
        entries = [json.loads(line) for line in trace_file.read_text().strip().splitlines()]
        decision_entries = [e for e in entries if e["task"] == "decision"]
        assert decision_entries[0]["extra"]["has_rationale"] is False
