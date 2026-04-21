"""End-to-end tests for HMAC audit-trail tamper detection.

# Maps to: OWASP ASVS V7.1 (Log Tamper Protection), NIST AI RMF MEASURE 2.7 (Traceability).

Interview demo: run any test here to show tamper detection live.

Each test synthesizes a small trace in a tmp dir, invokes
``python -m engine.verify_trace --run-id <id> --state-dir <path>`` via
subprocess, and asserts the exit code / stdout / stderr match the
specified behavior for that tamper class.

Exit-code contract (verified):
    0 — chain valid.
    1 — chain invalid (HMAC mismatch, sequence break, reordering).
    2 — verification impossible (missing key file, missing trace file).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import engine.context
import engine.tracer as tracer
from engine.tracer import GENESIS_HASH, init_run, trace


@pytest.fixture
def synthetic_run(tmp_path: Path, monkeypatch):
    """Build a small run with a valid HMAC chain and yield (state_dir, run_id).

    Redirects AE_TRACE_KEY_DIR to tmp_path/keys so the test never touches
    the real ~/.autonomy_engine/keys/ directory. The CLI invoked by the
    subprocess inherits this env var via the test process's environment.
    """
    engine.context.init(tmp_path)
    (tmp_path / "state").mkdir()
    monkeypatch.setenv("AE_TRACE_KEY_DIR", str(tmp_path / "keys"))

    # Reset tracer module state between tests — the tracer caches the
    # active run_id and the previous-hash pointer at module scope.
    tracer._run_id = None
    tracer._prev_hash = GENESIS_HASH
    tracer._seq = 0

    run_id = init_run()
    # Emit a few entries so truncation / re-order tests have something to
    # reorder. The exact content doesn't matter for chain integrity.
    for i in range(5):
        trace(task=f"stage-{i}", inputs=[], outputs=[], extra={"i": i})

    yield tmp_path / "state", run_id

    tracer._run_id = None
    tracer._prev_hash = GENESIS_HASH
    tracer._seq = 0


def _run_cli(state_dir: Path, run_id: str, *extra_args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "engine.verify_trace",
            "--run-id",
            run_id,
            "--state-dir",
            str(state_dir),
            *extra_args,
        ],
        capture_output=True,
        text=True,
    )


def _trace_path(state_dir: Path, run_id: str) -> Path:
    return state_dir / "runs" / run_id / "trace.jsonl"


def _key_path(tmp_path: Path, run_id: str) -> Path:
    """Return the new-location HMAC key path (matches the AE_TRACE_KEY_DIR
    override set in the fixture)."""
    return tmp_path / "keys" / f"{run_id}.key"


# ── Tests ────────────────────────────────────────────────────────────────────


def test_valid_trace_passes_verification(synthetic_run):
    """Clean run — chain intact, exit 0, stdout says VALID."""
    state_dir, run_id = synthetic_run
    result = _run_cli(state_dir, run_id)
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "VALID" in result.stdout
    assert run_id in result.stdout


def test_hmac_tampering_detected_end_to_end(synthetic_run):
    """Flip one byte in a payload — exit 1, stderr names the failing seq."""
    state_dir, run_id = synthetic_run
    trace_path = _trace_path(state_dir, run_id)

    lines = trace_path.read_text().splitlines()
    # Tamper with a middle entry: rewrite the 'task' field in line index 3
    # (after init_run's entry and a couple of stage entries).
    victim = json.loads(lines[3])
    victim["task"] = "TAMPERED"
    lines[3] = json.dumps(victim)
    trace_path.write_text("\n".join(lines) + "\n")

    result = _run_cli(state_dir, run_id)
    assert result.returncode == 1, (
        f"Expected exit 1, got {result.returncode}.\n"
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "INVALID" in result.stdout
    # The failing seq should be named in stderr (seq 3 or a later one
    # depending on where the chain break first registers).
    assert "seq" in result.stderr.lower() or "hmac" in result.stderr.lower()


def test_missing_key_exits_with_code_2(synthetic_run, tmp_path):
    """Delete the HMAC key file — exit 2, distinguishable from 1."""
    state_dir, run_id = synthetic_run
    _key_path(tmp_path, run_id).unlink()

    result = _run_cli(state_dir, run_id)
    assert result.returncode == 2, (
        f"Expected exit 2 (missing key), got {result.returncode}.\nstderr={result.stderr!r}"
    )
    assert "INVALID" in result.stdout
    # The specific error message mentions the missing key.
    assert "HMAC key" in result.stderr or "HMAC key" in result.stdout


def test_reordered_entries_detected(synthetic_run):
    """Swap two lines — chain link breaks; exit 1 with chain/seq diagnostic."""
    state_dir, run_id = synthetic_run
    trace_path = _trace_path(state_dir, run_id)

    lines = trace_path.read_text().splitlines()
    # Swap lines 2 and 3 — both have valid-looking JSON but the prev_hash
    # chain and the seq numbers no longer line up.
    lines[2], lines[3] = lines[3], lines[2]
    trace_path.write_text("\n".join(lines) + "\n")

    result = _run_cli(state_dir, run_id)
    assert result.returncode == 1
    assert "INVALID" in result.stdout


def test_deleted_middle_entry_detected(synthetic_run):
    """Remove a middle entry — seq continuity fails; exit 1."""
    state_dir, run_id = synthetic_run
    trace_path = _trace_path(state_dir, run_id)

    lines = trace_path.read_text().splitlines()
    del lines[3]  # remove a middle entry
    trace_path.write_text("\n".join(lines) + "\n")

    result = _run_cli(state_dir, run_id)
    assert result.returncode == 1
    assert "INVALID" in result.stdout


def test_truncated_trailing_entries_still_verifiable_up_to_intact(synthetic_run):
    """Truncation from the tail is NOT tamper — chain up to truncation point
    is still valid, so exit 0. The ``entries`` count reports what's present."""
    state_dir, run_id = synthetic_run
    trace_path = _trace_path(state_dir, run_id)

    lines = trace_path.read_text().splitlines()
    full_count = len(lines)
    # Truncate to first N entries — pretend the process crashed mid-run.
    truncated = lines[: full_count - 2]
    trace_path.write_text("\n".join(truncated) + "\n")

    result = _run_cli(state_dir, run_id, "--json")
    assert result.returncode == 0, (
        f"Expected valid-up-to-truncation, got exit {result.returncode}.\n"
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["valid"] is True
    assert payload["entries"] == len(truncated)
    assert payload["failure"] is None


def test_cli_json_output_parseable(synthetic_run):
    """--json emits a single-line JSON object parseable from stdout."""
    state_dir, run_id = synthetic_run
    result = _run_cli(state_dir, run_id, "--json")
    assert result.returncode == 0
    # Last non-empty stdout line should be valid JSON with the documented shape.
    stdout_lines = [line for line in result.stdout.strip().splitlines() if line]
    assert stdout_lines, f"No stdout; stderr={result.stderr!r}"
    payload = json.loads(stdout_lines[-1])
    assert set(payload.keys()) == {"valid", "entries", "failure", "failure_seq"}
    assert payload["valid"] is True
    assert isinstance(payload["entries"], int)
    assert payload["failure"] is None
    assert payload["failure_seq"] is None
