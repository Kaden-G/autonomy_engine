"""Tests for engine.tracer — append-only hash-chained tracing."""

import json

import pytest

import engine.context
import engine.tracer as tracer
from engine.tracer import (
    GENESIS_HASH,
    _compute_entry_hash,
    init_run,
    trace,
    verify_trace_integrity,
)


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path):
    """Point engine context at a temp dir and reset tracer module state."""
    engine.context.init(tmp_path)
    (tmp_path / "state").mkdir()
    # Reset tracer globals
    tracer._run_id = None
    tracer._prev_hash = GENESIS_HASH
    tracer._seq = 0
    yield
    tracer._run_id = None
    tracer._prev_hash = GENESIS_HASH
    tracer._seq = 0


def _read_entries(tmp_path) -> list[dict]:
    """Read all entries from the single run's trace.jsonl."""
    runs_dir = tmp_path / "state" / "runs"
    run_dirs = sorted(runs_dir.iterdir())
    trace_file = run_dirs[-1] / "trace.jsonl"
    return [json.loads(line) for line in trace_file.read_text().strip().splitlines()]


# ── init_run ─────────────────────────────────────────────────────────────────


class TestInitRun:
    def test_creates_run_directory(self, tmp_path):
        run_id = init_run()
        assert (tmp_path / "state" / "runs" / run_id).is_dir()

    def test_returns_12_char_hex_string(self, tmp_path):
        run_id = init_run()
        assert isinstance(run_id, str)
        assert len(run_id) == 12
        int(run_id, 16)  # must be valid hex

    def test_successive_runs_get_different_ids(self, tmp_path):
        r1 = init_run()
        r2 = init_run()
        assert r1 != r2


# ── Append behavior ─────────────────────────────────────────────────────────


class TestTraceAppend:
    def test_first_entry_has_genesis_prev_hash(self, tmp_path):
        init_run()
        trace(task="bootstrap", inputs=[], outputs=[])
        entries = _read_entries(tmp_path)
        assert entries[0]["prev_hash"] == GENESIS_HASH

    def test_second_entry_chains_to_first(self, tmp_path):
        init_run()
        trace(task="step1", inputs=[], outputs=[])
        trace(task="step2", inputs=[], outputs=[])
        entries = _read_entries(tmp_path)
        assert entries[1]["prev_hash"] == entries[0]["entry_hash"]

    def test_three_entry_chain(self, tmp_path):
        init_run()
        for i in range(3):
            trace(task=f"step{i}", inputs=[], outputs=[])
        entries = _read_entries(tmp_path)
        assert entries[0]["prev_hash"] == GENESIS_HASH
        assert entries[1]["prev_hash"] == entries[0]["entry_hash"]
        assert entries[2]["prev_hash"] == entries[1]["entry_hash"]

    def test_file_is_jsonl_not_json_array(self, tmp_path):
        init_run()
        trace(task="a", inputs=[], outputs=[])
        trace(task="b", inputs=[], outputs=[])
        runs_dir = tmp_path / "state" / "runs"
        trace_file = sorted(runs_dir.iterdir())[-1] / "trace.jsonl"
        raw = trace_file.read_text()
        # JSONL — NOT a JSON array
        assert not raw.strip().startswith("[")
        lines = raw.strip().splitlines()
        assert len(lines) == 2
        for line in lines:
            json.loads(line)  # each line is valid JSON

    def test_seq_increments(self, tmp_path):
        init_run()
        for _ in range(3):
            trace(task="x", inputs=[], outputs=[])
        entries = _read_entries(tmp_path)
        assert [e["seq"] for e in entries] == [0, 1, 2]

    def test_append_does_not_rewrite_earlier_lines(self, tmp_path):
        init_run()
        trace(task="a", inputs=[], outputs=[])
        runs_dir = tmp_path / "state" / "runs"
        trace_file = sorted(runs_dir.iterdir())[-1] / "trace.jsonl"
        content_after_one = trace_file.read_text()
        trace(task="b", inputs=[], outputs=[])
        content_after_two = trace_file.read_text()
        assert content_after_two.startswith(content_after_one)

    def test_input_hashes_computed(self, tmp_path):
        init_run()
        state_dir = tmp_path / "state"
        (state_dir / "inputs").mkdir(parents=True, exist_ok=True)
        (state_dir / "inputs" / "req.md").write_text("hello")
        trace(task="t", inputs=["inputs/req.md"], outputs=[])
        entries = _read_entries(tmp_path)
        h = entries[0]["inputs"]["inputs/req.md"]
        assert h is not None
        assert len(h) == 64  # full SHA-256 hex

    def test_output_hashes_computed(self, tmp_path):
        init_run()
        state_dir = tmp_path / "state"
        (state_dir / "build").mkdir(parents=True, exist_ok=True)
        (state_dir / "build" / "out.md").write_text("result")
        trace(task="t", inputs=[], outputs=["build/out.md"])
        entries = _read_entries(tmp_path)
        h = entries[0]["outputs"]["build/out.md"]
        assert h is not None
        assert len(h) == 64

    def test_external_paths_hash_to_none(self, tmp_path):
        init_run()
        trace(task="t", inputs=[], outputs=["<external>:app.py"])
        entries = _read_entries(tmp_path)
        assert entries[0]["outputs"]["<external>:app.py"] is None

    def test_missing_file_hashes_to_none(self, tmp_path):
        init_run()
        trace(task="t", inputs=["does/not/exist.md"], outputs=[])
        entries = _read_entries(tmp_path)
        assert entries[0]["inputs"]["does/not/exist.md"] is None

    def test_model_and_prompt_hash_included(self, tmp_path):
        init_run()
        trace(task="t", inputs=[], outputs=[], model="gpt-4", prompt_hash="abc123")
        entries = _read_entries(tmp_path)
        assert entries[0]["model"] == "gpt-4"
        assert entries[0]["prompt_hash"] == "abc123"

    def test_model_defaults_to_none(self, tmp_path):
        init_run()
        trace(task="t", inputs=[], outputs=[])
        entries = _read_entries(tmp_path)
        assert entries[0]["model"] is None
        assert entries[0]["prompt_hash"] is None


# ── Integrity verification ───────────────────────────────────────────────────


class TestVerifyIntegrity:
    def test_valid_chain_passes(self, tmp_path):
        run_id = init_run()
        trace(task="a", inputs=[], outputs=[])
        trace(task="b", inputs=[], outputs=[])
        trace(task="c", inputs=[], outputs=[])
        ok, errors = verify_trace_integrity(run_id)
        assert ok is True
        assert errors == []

    def test_single_entry_verifies(self, tmp_path):
        run_id = init_run()
        trace(task="solo", inputs=[], outputs=[])
        ok, errors = verify_trace_integrity(run_id)
        assert ok is True
        assert errors == []

    def test_tampered_task_detected(self, tmp_path):
        run_id = init_run()
        trace(task="a", inputs=[], outputs=[])
        trace(task="b", inputs=[], outputs=[])
        # Tamper: change the task name without recomputing entry_hash
        trace_file = tmp_path / "state" / "runs" / run_id / "trace.jsonl"
        lines = trace_file.read_text().strip().splitlines()
        entry = json.loads(lines[0])
        entry["task"] = "TAMPERED"
        lines[0] = json.dumps(entry, separators=(",", ":"))
        trace_file.write_text("\n".join(lines) + "\n")
        ok, errors = verify_trace_integrity(run_id)
        assert ok is False
        assert any("entry_hash mismatch" in e for e in errors)

    def test_broken_prev_hash_chain_detected(self, tmp_path):
        run_id = init_run()
        trace(task="a", inputs=[], outputs=[])
        trace(task="b", inputs=[], outputs=[])
        # Break chain: change prev_hash on second entry (recompute its entry_hash)
        trace_file = tmp_path / "state" / "runs" / run_id / "trace.jsonl"
        lines = trace_file.read_text().strip().splitlines()
        entry = json.loads(lines[1])
        entry.pop("entry_hash")
        entry["prev_hash"] = "f" * 64
        entry["entry_hash"] = _compute_entry_hash(entry)
        lines[1] = json.dumps(entry, separators=(",", ":"))
        trace_file.write_text("\n".join(lines) + "\n")
        ok, errors = verify_trace_integrity(run_id)
        assert ok is False
        assert any("prev_hash mismatch" in e for e in errors)

    def test_missing_trace_file(self, tmp_path):
        run_id = init_run()
        # No trace entries written — file does not exist
        ok, errors = verify_trace_integrity(run_id)
        assert ok is False
        assert any("not found" in e for e in errors)

    def test_empty_trace_file(self, tmp_path):
        run_id = init_run()
        trace_file = tmp_path / "state" / "runs" / run_id / "trace.jsonl"
        trace_file.write_text("")
        ok, errors = verify_trace_integrity(run_id)
        assert ok is False
        assert any("empty" in e for e in errors)

    def test_deleted_entry_breaks_chain(self, tmp_path):
        run_id = init_run()
        trace(task="a", inputs=[], outputs=[])
        trace(task="b", inputs=[], outputs=[])
        trace(task="c", inputs=[], outputs=[])
        # Delete middle entry
        trace_file = tmp_path / "state" / "runs" / run_id / "trace.jsonl"
        lines = trace_file.read_text().strip().splitlines()
        del lines[1]
        trace_file.write_text("\n".join(lines) + "\n")
        ok, errors = verify_trace_integrity(run_id)
        assert ok is False
        assert any("prev_hash mismatch" in e for e in errors)


# ── _compute_entry_hash ─────────────────────────────────────────────────────


class TestComputeEntryHash:
    def test_deterministic(self):
        entry = {"task": "x", "seq": 0, "prev_hash": GENESIS_HASH}
        assert _compute_entry_hash(entry) == _compute_entry_hash(entry)

    def test_different_entries_different_hashes(self):
        a = {"task": "x", "seq": 0}
        b = {"task": "y", "seq": 0}
        assert _compute_entry_hash(a) != _compute_entry_hash(b)

    def test_returns_64_char_hex(self):
        h = _compute_entry_hash({"a": 1})
        assert len(h) == 64
        int(h, 16)
