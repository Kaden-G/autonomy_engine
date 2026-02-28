"""Traceability spine — append-only, hash-chained trace entries per run.

Each run gets a unique run_id.  Trace entries are appended as JSONL to
``state/runs/<run_id>/trace.jsonl``.  Every entry carries ``prev_hash``
(the hash of the preceding entry) and ``entry_hash`` (its own hash),
forming a tamper-evident chain.
"""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from engine.context import get_state_dir

# ── Constants ────────────────────────────────────────────────────────────────

GENESIS_HASH = "0" * 64  # prev_hash for the very first entry in a run

# ── Module state (set once per run via init_run) ─────────────────────────────

_run_id: str | None = None
_prev_hash: str = GENESIS_HASH
_seq: int = 0


def init_run() -> str:
    """Start a new run — create ``state/runs/<run_id>/`` and reset chain state."""
    global _run_id, _prev_hash, _seq
    _run_id = uuid4().hex[:12]
    _prev_hash = GENESIS_HASH
    _seq = 0
    run_dir = get_state_dir() / "runs" / _run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return _run_id


def get_run_id() -> str:
    """Return the active run ID, or raise if no run has been initialized."""
    if _run_id is None:
        raise RuntimeError("No active run. Call init_run() first.")
    return _run_id


# ── Trace path ───────────────────────────────────────────────────────────────

def _trace_path() -> Path:
    """Return the path to ``trace.jsonl`` for the active run."""
    return get_state_dir() / "runs" / get_run_id() / "trace.jsonl"


# ── Hashing helpers ──────────────────────────────────────────────────────────

def hash_prompt(prompt_text: str) -> str:
    """Return a truncated SHA-256 hash of a prompt string for traceability."""
    return hashlib.sha256(prompt_text.encode()).hexdigest()[:16]


def _hash_artifact(rel_path: str) -> str | None:
    """Compute SHA-256 of a state file.  Returns ``None`` for external or missing files."""
    if rel_path.startswith("<external>:"):
        return None
    try:
        full = get_state_dir() / rel_path
        return hashlib.sha256(full.read_bytes()).hexdigest()
    except (OSError, FileNotFoundError):
        return None


def _compute_entry_hash(entry: dict) -> str:
    """Deterministic SHA-256 of a trace entry (must NOT contain ``entry_hash``)."""
    canonical = json.dumps(entry, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


# ── Core trace function ─────────────────────────────────────────────────────

def trace(
    task: str,
    inputs: list[str],
    outputs: list[str],
    model: str | None = None,
    prompt_hash: str | None = None,
    provider: str | None = None,
    max_tokens: int | None = None,
) -> None:
    """Append a hash-chained trace entry to the active run's ``trace.jsonl``."""
    global _prev_hash, _seq

    input_hashes = {p: _hash_artifact(p) for p in inputs}
    output_hashes = {p: _hash_artifact(p) for p in outputs}

    entry = {
        "seq": _seq,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "task": task,
        "inputs": input_hashes,
        "outputs": output_hashes,
        "model": model,
        "prompt_hash": prompt_hash,
        "provider": provider,
        "max_tokens": max_tokens,
        "prev_hash": _prev_hash,
    }

    entry_hash = _compute_entry_hash(entry)
    entry["entry_hash"] = entry_hash

    with open(_trace_path(), "a") as f:
        f.write(json.dumps(entry, separators=(",", ":")) + "\n")

    _prev_hash = entry_hash
    _seq += 1


# ── Integrity verification ──────────────────────────────────────────────────

def verify_trace_integrity(run_id: str | None = None) -> tuple[bool, list[str]]:
    """Replay the hash chain and report any breaks.

    Returns ``(is_valid, errors)``.  An empty *errors* list means the
    chain is intact.
    """
    rid = run_id if run_id is not None else get_run_id()
    path = get_state_dir() / "runs" / rid / "trace.jsonl"

    if not path.exists():
        return False, [f"trace.jsonl not found for run {rid}"]

    text = path.read_text().strip()
    if not text:
        return False, ["trace.jsonl is empty"]

    errors: list[str] = []
    expected_prev = GENESIS_HASH

    for i, line in enumerate(text.splitlines()):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"Line {i}: invalid JSON — {exc}")
            break

        stored_hash = entry.pop("entry_hash", None)
        if stored_hash is None:
            errors.append(f"Line {i}: missing entry_hash")
            break

        # Check chain link
        if entry.get("prev_hash") != expected_prev:
            errors.append(
                f"Line {i}: prev_hash mismatch "
                f"(expected {expected_prev[:16]}…, "
                f"got {entry.get('prev_hash', '<missing>')[:16]}…)"
            )

        # Recompute and compare
        computed = _compute_entry_hash(entry)
        if computed != stored_hash:
            errors.append(
                f"Line {i}: entry_hash mismatch "
                f"(expected {computed[:16]}…, got {stored_hash[:16]}…)"
            )

        expected_prev = stored_hash

    return len(errors) == 0, errors
