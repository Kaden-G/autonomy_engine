"""Tests for the manifest-conflict DecisionRequired gate (P1-3).

These tests exercise :func:`tasks.implement._apply_manifest_conflict_gate`
directly — the helper extracted from ``_implement_chunked`` so the gate
logic can be tested without mocking the LLM.

Resume-path state (decision records) is stored under
``state/runs/<run_id>/decisions/<gate>.json``; the
``_isolated_state`` fixture points engine context at a tmp dir and
initialises a run so ``save_decision`` / ``decision_exists`` /
``load_decision`` have somewhere to write.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import engine.context
import engine.tracer as tracer
from engine.decision_gates import DecisionRequired, save_decision
from engine.tracer import GENESIS_HASH, init_run
from tasks.implement import _apply_manifest_conflict_gate, _merge_manifests


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path: Path):
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


def _manifest(*files: tuple[str, str]) -> str:
    return json.dumps({"files": [{"path": p, "content": c} for p, c in files]})


# ── Baseline: no conflicts, no raise ────────────────────────────────────────


class TestNoConflicts:
    def test_clean_merge_passes_through(self):
        init_run()
        m1 = _manifest(("a.ts", "a"))
        m2 = _manifest(("b.ts", "b"))
        merged_json, conflicts = _merge_manifests([m1, m2])
        out_json, out_conflicts = _apply_manifest_conflict_gate(
            merged_json, conflicts, [m1, m2], ["A", "B"]
        )
        assert out_json == merged_json
        assert out_conflicts == []


# ── First pass: conflict detected → DecisionRequired ────────────────────────


class TestConflictRaisesGate:
    def test_conflict_raises_decision_required(self):
        init_run()
        m1 = _manifest(("shared.ts", "v1"))
        m2 = _manifest(("shared.ts", "v2"))
        merged_json, conflicts = _merge_manifests([m1, m2], component_names=["A", "B"])
        with pytest.raises(DecisionRequired) as exc:
            _apply_manifest_conflict_gate(merged_json, conflicts, [m1, m2], ["A", "B"])
        assert exc.value.gate == "manifest_conflict"
        assert exc.value.stage == "implement"
        assert exc.value.options == ["use_last_writer_wins", "use_first_writer", "abort"]

    def test_conflicts_json_written_before_raise(self, tmp_path):
        init_run()
        m1 = _manifest(("shared.ts", "v1"))
        m2 = _manifest(("shared.ts", "v2"))
        merged_json, conflicts = _merge_manifests([m1, m2], component_names=["A", "B"])

        with pytest.raises(DecisionRequired):
            _apply_manifest_conflict_gate(merged_json, conflicts, [m1, m2], ["A", "B"])

        # save_state_file writes to state/<name> (project-scoped, not run-scoped) —
        # matches the IMPLEMENTATION.md / FILE_MANIFEST.json sibling paths in
        # tasks/implement.py.
        conflicts_path = tmp_path / "state" / "implementations" / "MANIFEST_CONFLICTS.json"
        assert conflicts_path.exists(), "MANIFEST_CONFLICTS.json must be written before raise"

        saved = json.loads(conflicts_path.read_text())
        assert saved[0]["path"] == "shared.ts"
        assert saved[0]["chunks"] == ["A", "B"]
        assert len(saved[0]["versions"]) == 2


# ── Resume paths: decision already recorded ─────────────────────────────────


class TestResumeUseLastWriterWins:
    def test_resume_passes_through_with_last_writer(self):
        init_run()
        save_decision(
            "manifest_conflict",
            "implement",
            ["use_last_writer_wins", "use_first_writer", "abort"],
            "use_last_writer_wins",
        )
        m1 = _manifest(("shared.ts", "first"))
        m2 = _manifest(("shared.ts", "second"))
        merged_json, conflicts = _merge_manifests([m1, m2], component_names=["A", "B"])

        out_json, out_conflicts = _apply_manifest_conflict_gate(
            merged_json, conflicts, [m1, m2], ["A", "B"]
        )
        merged = json.loads(out_json)
        # Default merge already applied last-writer-wins; nothing changes.
        assert merged["files"][0]["content"] == "second"


class TestResumeUseFirstWriter:
    def test_resume_re_merges_with_first_writer_policy(self):
        init_run()
        save_decision(
            "manifest_conflict",
            "implement",
            ["use_last_writer_wins", "use_first_writer", "abort"],
            "use_first_writer",
        )
        m1 = _manifest(("shared.ts", "first"))
        m2 = _manifest(("shared.ts", "second"))
        merged_json, conflicts = _merge_manifests([m1, m2], component_names=["A", "B"])

        out_json, out_conflicts = _apply_manifest_conflict_gate(
            merged_json, conflicts, [m1, m2], ["A", "B"]
        )
        merged = json.loads(out_json)
        assert merged["files"][0]["content"] == "first"
        assert out_conflicts[0]["winner"] == "A"


class TestResumeAbort:
    def test_resume_abort_raises_runtime_error(self):
        init_run()
        save_decision(
            "manifest_conflict",
            "implement",
            ["use_last_writer_wins", "use_first_writer", "abort"],
            "abort",
        )
        m1 = _manifest(("shared.ts", "v1"))
        m2 = _manifest(("shared.ts", "v2"))
        merged_json, conflicts = _merge_manifests([m1, m2], component_names=["A", "B"])

        with pytest.raises(RuntimeError, match="abort"):
            _apply_manifest_conflict_gate(merged_json, conflicts, [m1, m2], ["A", "B"])


# ── Gate is idempotent on resume without conflicts ──────────────────────────


class TestResumeIdempotent:
    def test_recorded_decision_but_no_conflicts_is_noop(self):
        """If a decision file exists from a prior resume but a later merge has
        no conflicts, the gate must not raise anything."""
        init_run()
        save_decision(
            "manifest_conflict",
            "implement",
            ["use_last_writer_wins", "use_first_writer", "abort"],
            "use_last_writer_wins",
        )
        m1 = _manifest(("a.ts", "a"))
        m2 = _manifest(("b.ts", "b"))
        merged_json, conflicts = _merge_manifests([m1, m2])

        out_json, out_conflicts = _apply_manifest_conflict_gate(
            merged_json, conflicts, [m1, m2], ["A", "B"]
        )
        assert out_conflicts == []
