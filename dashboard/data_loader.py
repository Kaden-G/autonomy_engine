"""Data loader for the Autonomy Engine dashboard.

Reads state files, trace entries, evidence records, and decisions
directly from the filesystem. Does NOT import engine modules to avoid
Prefect initialization side effects.
"""

import hashlib
import json
from pathlib import Path

import yaml


def find_project_dir() -> Path | None:
    """Auto-detect the project directory.

    Checks (in order):
    1. AUTONOMY_ENGINE_PROJECT_DIR environment variable
    2. Current working directory (if it has state/)
    3. Parent of this file's directory (engine root, if it has state/)
    """
    import os

    env_dir = os.environ.get("AUTONOMY_ENGINE_PROJECT_DIR")
    if env_dir:
        p = Path(env_dir)
        if (p / "state").is_dir():
            return p

    cwd = Path.cwd()
    if (cwd / "state").is_dir():
        return cwd

    engine_root = Path(__file__).resolve().parent.parent
    if (engine_root / "state").is_dir():
        return engine_root

    return None


def get_state_dir(project_dir: Path) -> Path:
    return project_dir / "state"


# -- Run Discovery -------------------------------------------------------


def is_intake_complete(project_dir: Path) -> bool:
    """Return True if all 5 required intake artifacts exist."""
    return all(get_intake_status(project_dir).values())


def get_latest_run_id(project_dir: Path) -> str | None:
    """Return the most recently created run ID (by directory mtime)."""
    runs_dir = get_state_dir(project_dir) / "runs"
    if not runs_dir.is_dir():
        return None
    run_dirs = [d for d in runs_dir.iterdir() if d.is_dir()]
    if not run_dirs:
        return None
    latest = max(run_dirs, key=lambda d: d.stat().st_mtime)
    return latest.name


def list_runs(project_dir: Path) -> list[dict]:
    """List all runs with basic metadata, sorted newest first."""
    runs_dir = get_state_dir(project_dir) / "runs"
    if not runs_dir.is_dir():
        return []

    runs = []
    for run_dir in sorted(runs_dir.iterdir(), reverse=True):
        if not run_dir.is_dir():
            continue
        run_id = run_dir.name
        trace_path = run_dir / "trace.jsonl"

        meta = {
            "run_id": run_id,
            "has_trace": trace_path.exists(),
            "trace_entries": 0,
            "stages": [],
            "started_at": None,
            "finished_at": None,
            "has_config_snapshot": (run_dir / "config_snapshot.yml").exists(),
            "evidence_count": 0,
            "decision_count": 0,
        }

        if trace_path.exists():
            entries = _load_trace_entries(trace_path)
            meta["trace_entries"] = len(entries)
            meta["stages"] = list(dict.fromkeys(e["task"] for e in entries))
            if entries:
                meta["started_at"] = entries[0].get("timestamp")
                meta["finished_at"] = entries[-1].get("timestamp")

        evidence_dir = run_dir / "evidence"
        if evidence_dir.is_dir():
            meta["evidence_count"] = len(list(evidence_dir.glob("*.json")))

        decisions_dir = run_dir / "decisions"
        if decisions_dir.is_dir():
            meta["decision_count"] = len(list(decisions_dir.glob("*.json")))

        runs.append(meta)

    return runs


# -- Trace ----------------------------------------------------------------


def _load_trace_entries(trace_path: Path) -> list[dict]:
    """Parse a trace.jsonl file into a list of dicts."""
    entries = []
    for line in trace_path.read_text().strip().splitlines():
        if line.strip():
            entries.append(json.loads(line))
    return entries


def load_trace(project_dir: Path, run_id: str) -> list[dict]:
    """Load all trace entries for a run."""
    trace_path = get_state_dir(project_dir) / "runs" / run_id / "trace.jsonl"
    if not trace_path.exists():
        return []
    return _load_trace_entries(trace_path)


def verify_trace_integrity(project_dir: Path, run_id: str) -> tuple[bool, list[str]]:
    """Replay the hash chain and report breaks. Returns (is_valid, errors)."""
    trace_path = get_state_dir(project_dir) / "runs" / run_id / "trace.jsonl"
    if not trace_path.exists():
        return False, [f"trace.jsonl not found for run {run_id}"]

    text = trace_path.read_text().strip()
    if not text:
        return False, ["trace.jsonl is empty"]

    errors = []
    expected_prev = "0" * 64  # GENESIS_HASH

    for i, line in enumerate(text.splitlines()):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"Line {i}: invalid JSON - {exc}")
            break

        stored_hash = entry.pop("entry_hash", None)
        if stored_hash is None:
            errors.append(f"Line {i}: missing entry_hash")
            break

        if entry.get("prev_hash") != expected_prev:
            errors.append(
                f"Line {i}: prev_hash mismatch "
                f"(expected {expected_prev[:16]}..., "
                f"got {entry.get('prev_hash', '<missing>')[:16]}...)"
            )

        canonical = json.dumps(entry, sort_keys=True, separators=(",", ":"))
        computed = hashlib.sha256(canonical.encode()).hexdigest()
        if computed != stored_hash:
            errors.append(
                f"Line {i}: entry_hash mismatch "
                f"(expected {computed[:16]}..., got {stored_hash[:16]}...)"
            )

        expected_prev = stored_hash

    return len(errors) == 0, errors


# -- Evidence -------------------------------------------------------------


def load_evidence(project_dir: Path, run_id: str) -> list[dict]:
    """Load all evidence records for a run, sorted by name."""
    evidence_dir = get_state_dir(project_dir) / "runs" / run_id / "evidence"
    if not evidence_dir.is_dir():
        return []
    records = []
    for path in sorted(evidence_dir.glob("*.json")):
        records.append(json.loads(path.read_text()))
    return records


# -- Decisions ------------------------------------------------------------


def load_decisions(project_dir: Path, run_id: str) -> list[dict]:
    """Load all decision records for a run."""
    decisions_dir = get_state_dir(project_dir) / "runs" / run_id / "decisions"
    if not decisions_dir.is_dir():
        return []
    records = []
    for path in sorted(decisions_dir.glob("*.json")):
        records.append(json.loads(path.read_text()))
    return records


# -- Artifacts ------------------------------------------------------------


def load_artifact(project_dir: Path, state_rel_path: str) -> str | None:
    """Load an artifact file by its state-relative path."""
    path = get_state_dir(project_dir) / state_rel_path
    if path.exists():
        return path.read_text()
    return None


# -- Config ---------------------------------------------------------------


def load_config(project_dir: Path) -> dict:
    """Load the active config.yml."""
    config_path = project_dir / "config.yml"
    if not config_path.exists():
        return {}
    with open(config_path) as f:
        return yaml.safe_load(f) or {}


def load_config_snapshot(project_dir: Path, run_id: str) -> dict | None:
    """Load the config snapshot for a specific run."""
    snap = get_state_dir(project_dir) / "runs" / run_id / "config_snapshot.yml"
    if not snap.exists():
        return None
    with open(snap) as f:
        return yaml.safe_load(f)


def load_gate_policies(project_dir: Path) -> dict:
    """Load decision gate policies from DECISION_GATES.yml."""
    for templates_dir in [
        project_dir / "templates",
        Path(__file__).resolve().parent.parent / "templates",
    ]:
        gates_path = templates_dir / "DECISION_GATES.yml"
        if gates_path.exists():
            data = yaml.safe_load(gates_path.read_text())
            return data.get("gates", {})
    return {}


# -- Cache ----------------------------------------------------------------


def get_cache_stats(project_dir: Path) -> dict:
    """Compute LLM cache statistics."""
    cache_dir = get_state_dir(project_dir) / "cache" / "llm"
    if not cache_dir.is_dir():
        return {"total_entries": 0, "entries": []}

    entries = []
    for path in sorted(cache_dir.glob("*.json")):
        data = json.loads(path.read_text())
        entries.append(
            {
                "cache_key": path.stem,
                "stage": data.get("stage", "unknown"),
                "model": data.get("model", "unknown"),
                "created_at": data.get("created_at"),
                "response_size": len(data.get("response", "")),
            }
        )

    return {
        "total_entries": len(entries),
        "entries": entries,
        "by_stage": _count_by(entries, "stage"),
    }


def _count_by(items: list[dict], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        val = item.get(key, "unknown")
        counts[val] = counts.get(val, 0) + 1
    return counts


# -- Benchmarks -----------------------------------------------------------


def list_benchmark_results(project_dir: Path) -> list[dict]:
    """List all benchmark result files in bench/."""
    bench_dir = project_dir / "bench"
    if not bench_dir.is_dir():
        return []
    results = []
    for path in sorted(bench_dir.glob("results_*.json"), reverse=True):
        data = json.loads(path.read_text())
        data["_file"] = str(path)
        results.append(data)
    return results


# -- Input Artifacts ------------------------------------------------------


def load_project_spec(project_dir: Path) -> dict | None:
    """Load the project spec YAML."""
    spec_path = get_state_dir(project_dir) / "inputs" / "project_spec.yml"
    if not spec_path.exists():
        return None
    with open(spec_path) as f:
        return yaml.safe_load(f)


def get_intake_status(project_dir: Path) -> dict:
    """Check which intake artifacts exist."""
    inputs_dir = get_state_dir(project_dir) / "inputs"
    required = [
        "project_spec.yml",
        "REQUIREMENTS.md",
        "CONSTRAINTS.md",
        "NON_GOALS.md",
        "ACCEPTANCE_CRITERIA.md",
    ]
    return {name: (inputs_dir / name).exists() for name in required}


# -- Pipeline Stage Status ------------------------------------------------


def get_pipeline_status(project_dir: Path) -> dict:
    """Determine what pipeline artifacts exist (latest state)."""
    state = get_state_dir(project_dir)
    return {
        "intake_complete": all(get_intake_status(project_dir).values()),
        "has_architecture": (state / "designs" / "ARCHITECTURE.md").exists(),
        "has_implementation": (state / "implementations" / "IMPLEMENTATION.md").exists(),
        "has_manifest": (state / "implementations" / "FILE_MANIFEST.json").exists(),
        "has_test_results": (state / "tests" / "TEST_RESULTS.md").exists(),
        "has_verification": (state / "tests" / "VERIFICATION.md").exists(),
        "has_build_manifest": (state / "build" / "MANIFEST.md").exists(),
    }
