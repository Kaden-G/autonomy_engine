# Maps to: NIST AI RMF MEASURE 2.7 (Traceability), OWASP ASVS V7.1 (Log Tamper Protection).
"""CLI for verifying the HMAC chain of a run's audit trail.

Usage::

    python -m engine.verify_trace --run-id <id> [--state-dir PATH] [--json]

Exit codes:
    0 — chain valid, every entry's HMAC matches and sequence is intact.
    1 — chain invalid (tamper detected, missing entry, or reordering).
    2 — verification impossible (missing HMAC key, missing trace.jsonl).

The exit-code distinction matters: CI uses ``1`` as "reject merge" but
``2`` as "something structural is off, flag for human review." Conflating
them would hide the difference between "an attacker edited a byte" and
"someone forgot to commit the .trace_key file."
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from engine.tracer import verify_trace_integrity


# Match the "Line N: ..." prefix that verify_trace_integrity uses when it
# reports a chain break. Extracting N lets the CLI report a concrete
# failure sequence without changing the function's existing return shape.
_LINE_PREFIX_RE = re.compile(r"^Line (\d+):\s*(.*)$")


def _classify_errors(errors: list[str]) -> tuple[int | None, str | None]:
    """Return (first_failure_seq, first_failure_message) from an errors list.

    None/None if the list is empty.
    """
    if not errors:
        return None, None
    first = errors[0]
    m = _LINE_PREFIX_RE.match(first)
    if m:
        return int(m.group(1)), m.group(2).strip()
    return None, first


def _entry_count(state_dir: Path, run_id: str) -> int:
    """Count non-empty lines in trace.jsonl. 0 if the file is missing."""
    path = state_dir / "runs" / run_id / "trace.jsonl"
    if not path.exists():
        return 0
    text = path.read_text().strip()
    if not text:
        return 0
    return len(text.splitlines())


def _build_result(run_id: str, state_dir: Path) -> dict:
    """Return the stable-shape result dict the CLI exposes."""
    valid, errors = verify_trace_integrity(run_id=run_id, state_dir=state_dir)
    failure_seq, failure_msg = _classify_errors(errors)
    entries = _entry_count(state_dir, run_id)
    # Convention: "entries counted" is the number of lines present, not the
    # number of lines that verified cleanly. A truncation past the tail is
    # not a tamper — the chain up to the truncation point is still valid,
    # and verify_trace_integrity returns valid=True. Callers who need the
    # last_valid_seq can subtract 1 from entries when valid=True.
    return {
        "valid": valid,
        "entries": entries,
        "failure": failure_msg,
        "failure_seq": failure_seq,
    }


def _is_missing_key_error(errors: list[str]) -> bool:
    """Detect the 'missing HMAC key' case so we can exit 2 instead of 1."""
    return any("HMAC key" in e for e in errors)


def _is_missing_trace_error(errors: list[str]) -> bool:
    """Detect the 'missing trace.jsonl' case so we can exit 2 instead of 1."""
    return any("trace.jsonl not found" in e for e in errors)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m engine.verify_trace",
        description="Verify run audit-trail integrity (HMAC chain).",
    )
    parser.add_argument("--run-id", required=True, help="Run ID under state/runs/")
    parser.add_argument(
        "--state-dir",
        default="state",
        help="Base state directory (default: ./state)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a single-line JSON object on stdout (machine-readable).",
    )
    args = parser.parse_args(argv)

    state_dir = Path(args.state_dir)
    result = _build_result(args.run_id, state_dir)

    # Re-run the raw call to surface the missing-key / missing-trace signals
    # for exit-code classification.
    _, errors = verify_trace_integrity(run_id=args.run_id, state_dir=state_dir)

    if args.json:
        print(json.dumps(result))
    else:
        status = "VALID" if result["valid"] else "INVALID"
        print(f"[{status}] run={args.run_id} entries={result['entries']}")
        if not result["valid"]:
            if result["failure_seq"] is not None:
                print(
                    f"  failure at seq {result['failure_seq']}: {result['failure']}",
                    file=sys.stderr,
                )
            else:
                print(f"  failure: {result['failure']}", file=sys.stderr)

    if result["valid"]:
        return 0
    if _is_missing_key_error(errors) or _is_missing_trace_error(errors):
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
