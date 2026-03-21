"""Tamper-evident audit log — the engine's "black box flight recorder."

Every action the pipeline takes (design, implement, test, verify) is recorded
as a log entry in ``trace.jsonl``.  These entries are chained together and
cryptographically signed so that any after-the-fact modification — changing a
result, deleting a step, reordering entries — is detectable.

How it works (in plain terms):
    Each pipeline run generates a unique secret key.  Every log entry is signed
    with that key using HMAC-SHA256 (a standard tamper-detection algorithm).
    Each entry also includes the signature of the *previous* entry, forming a
    chain.  To verify the log, you replay the chain with the key and check that
    every signature matches.

What tampering looks like to the verifier:
    1. Edited entry content → signature mismatch
    2. Reordered entries → chain link broken
    3. Deleted or inserted entries → sequence gap detected
    4. Missing key file → verification impossible (flagged clearly)

Technical details:
    - Entries are stored as JSONL in ``state/runs/<run_id>/trace.jsonl``
    - The HMAC key lives in ``state/runs/<run_id>/.trace_key`` (owner-read-only)
    - HMAC-SHA256 with 256-bit per-run keys prevents chain recomputation attacks
"""

import hashlib
import hmac
import json
import os
import secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from engine.context import get_config_path, get_state_dir

# ── Constants ────────────────────────────────────────────────────────────────

GENESIS_HASH = "0" * 64  # Starting value for the chain (first entry has no predecessor)
_KEY_FILENAME = ".trace_key"  # Hidden file storing the cryptographic signing key for this run

# ── Module state (set once per run via init_run) ─────────────────────────────

_run_id: str | None = None
_prev_hash: str = GENESIS_HASH
_seq: int = 0
_hmac_key: bytes = b""


def init_run() -> str:
    """Start a new run — create ``state/runs/<run_id>/`` and reset chain state.

    Generates a cryptographic HMAC key for this run's trace integrity.
    Also snapshots ``config.yml`` into the run directory for reproducibility.
    """
    global _run_id, _prev_hash, _seq, _hmac_key
    _run_id = uuid4().hex[:12]
    _prev_hash = GENESIS_HASH
    _seq = 0

    run_dir = get_state_dir() / "runs" / _run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # Generate a unique signing key for this run's audit log (256-bit, cryptographically random)
    _hmac_key = secrets.token_bytes(32)
    key_path = run_dir / _KEY_FILENAME
    key_path.write_bytes(_hmac_key)
    try:
        os.chmod(key_path, 0o600)  # owner-read-write only
    except OSError:
        pass  # Windows or restrictive filesystem — best effort

    # Snapshot runtime config for reproducibility
    config_path = get_config_path()
    if config_path.exists():
        shutil.copy2(config_path, run_dir / "config_snapshot.yml")

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


def _load_hmac_key(run_id: str) -> bytes | None:
    """Load the HMAC key for a run, or None if missing."""
    key_path = get_state_dir() / "runs" / run_id / _KEY_FILENAME
    if key_path.exists():
        return key_path.read_bytes()
    return None


# ── Hashing helpers ──────────────────────────────────────────────────────────


def hash_prompt(prompt_text: str) -> str:
    """Return the full SHA-256 hash of a prompt string for traceability."""
    return hashlib.sha256(prompt_text.encode()).hexdigest()


def _hash_artifact(rel_path: str, external_base: Path | None = None) -> str | None:
    """Compute SHA-256 of an artifact file.

    State-relative paths are resolved under ``get_state_dir()``.
    ``<external>:`` paths are resolved under *external_base* if provided.
    Returns ``None`` for missing files or unresolvable external paths.
    """
    if rel_path.startswith("<external>:"):
        if external_base is None:
            return None
        suffix = rel_path[len("<external>:"):]
        full = external_base / suffix
    else:
        full = get_state_dir() / rel_path
    try:
        return hashlib.sha256(full.read_bytes()).hexdigest()
    except (OSError, FileNotFoundError):
        return None


def _compute_entry_hmac(entry: dict, key: bytes) -> str:
    """HMAC-SHA256 of a trace entry (must NOT contain ``entry_hash``).

    Uses a keyed HMAC rather than plain SHA-256, so an attacker who
    modifies entries cannot recompute valid hashes without the key.
    """
    canonical = json.dumps(entry, sort_keys=True, separators=(",", ":"))
    return hmac.new(key, canonical.encode(), hashlib.sha256).hexdigest()


# ── Core trace function ─────────────────────────────────────────────────────


def trace(
    task: str,
    inputs: list[str],
    outputs: list[str],
    model: str | None = None,
    prompt_hash: str | None = None,
    provider: str | None = None,
    max_tokens: int | None = None,
    external_base: Path | None = None,
    extra: dict | None = None,
) -> None:
    """Append an HMAC-authenticated trace entry to the active run's ``trace.jsonl``.

    *external_base* is passed to ``_hash_artifact`` so ``<external>:`` paths
    can be resolved and hashed.  *extra* is an optional dict merged into the
    entry for structured metadata (e.g. decision details, token usage).
    """
    global _prev_hash, _seq

    input_hashes = {p: _hash_artifact(p, external_base) for p in inputs}
    output_hashes = {p: _hash_artifact(p, external_base) for p in outputs}

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
    if extra:
        entry["extra"] = extra

    entry_hash = _compute_entry_hmac(entry, _hmac_key)
    entry["entry_hash"] = entry_hash

    with open(_trace_path(), "a") as f:
        f.write(json.dumps(entry, separators=(",", ":")) + "\n")

    _prev_hash = entry_hash
    _seq += 1


# ── Integrity verification ──────────────────────────────────────────────────


def verify_trace_integrity(run_id: str | None = None) -> tuple[bool, list[str]]:
    """Replay the HMAC chain and report any breaks.

    Returns ``(is_valid, errors)``.  An empty *errors* list means the
    chain is intact and no entries have been tampered with.

    Requires the ``.trace_key`` file to be present — if it's missing,
    verification fails with a clear error (the key may have been deleted
    or the run was created before HMAC was added).
    """
    rid = run_id if run_id is not None else get_run_id()
    path = get_state_dir() / "runs" / rid / "trace.jsonl"

    if not path.exists():
        return False, [f"trace.jsonl not found for run {rid}"]

    # Load the signing key for this run's audit log
    key = _load_hmac_key(rid)
    if key is None:
        return False, [
            f"HMAC key (.trace_key) not found for run {rid}. "
            "Cannot verify integrity — trace may predate HMAC support."
        ]

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

        # Check sequence continuity
        if entry.get("seq") != i:
            errors.append(
                f"Line {i}: seq mismatch (expected {i}, got {entry.get('seq')})"
            )

        # Check chain link
        if entry.get("prev_hash") != expected_prev:
            errors.append(
                f"Line {i}: prev_hash mismatch "
                f"(expected {expected_prev[:16]}…, "
                f"got {entry.get('prev_hash', '<missing>')[:16]}…)"
            )

        # Recompute the expected signature and compare (timing-safe to prevent side-channel attacks)
        computed = _compute_entry_hmac(entry, key)
        if not hmac.compare_digest(computed, stored_hash):
            errors.append(
                f"Line {i}: HMAC mismatch — entry has been modified "
                f"(expected {computed[:16]}…, got {stored_hash[:16]}…)"
            )

        expected_prev = stored_hash

    return len(errors) == 0, errors
