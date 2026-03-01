"""Benchmark runner — measure pipeline efficiency across N runs.

Exercises the full pipeline against a completed intake, captures stage
timings and LLM metrics, and writes structured JSON results keyed by
git SHA for before/after comparison.

Usage:
    python bench/benchmark_runs.py --runs N --project-dir DIR [--out PATH]
"""

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median

# Ensure engine root is importable
_BENCH_DIR = Path(__file__).resolve().parent
_ENGINE_ROOT = _BENCH_DIR.parent
if str(_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_ENGINE_ROOT))

from engine.context import get_prompts_dir, get_state_dir
from engine.context import init as init_context
from engine.decision_gates import DecisionRequired, handle_gate
from engine.evidence import format_evidence_for_llm, load_all_evidence
from engine.tracer import init_run
from tasks.bootstrap import bootstrap_project
from tasks.design import design_system
from tasks.extract import extract_project
from tasks.implement import implement_system
from tasks.test import test_system
from tasks.verify import verify_system


# ── Helpers ──────────────────────────────────────────────────────────────────


def _git_sha() -> str:
    """Return the short git SHA of HEAD."""
    result = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
        cwd=_ENGINE_ROOT,
    )
    return result.stdout.strip() or "unknown"


def _abort_on_pause(exc: DecisionRequired) -> None:
    """Callback for handle_gate — gates must be skip/auto for benchmarks."""
    raise RuntimeError(
        f"Gate '{exc.gate}' at stage '{exc.stage}' requires human input. "
        "Set all DECISION_GATES.yml policies to 'skip' or 'auto' for benchmarking."
    )


def _parse_trace(run_id: str, state_dir: Path) -> dict:
    """Parse trace.jsonl for a run, counting model calls, cache hits, and sandbox info."""
    trace_path = state_dir / "runs" / run_id / "trace.jsonl"
    model_calls = 0
    cache_hits = 0
    calls_by_stage: dict[str, int] = {}
    sandbox_venv_cache_hit: bool | None = None
    sandbox_venv_setup_time_s: float | None = None

    if trace_path.exists():
        for line in trace_path.read_text().splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            if entry.get("model") is not None:
                model_calls += 1
                stage = entry.get("task", "unknown")
                calls_by_stage[stage] = calls_by_stage.get(stage, 0) + 1
            extra = entry.get("extra") or {}
            if extra.get("cache_hit"):
                cache_hits += 1
            if extra.get("sandbox_venv_cache_hit") is not None:
                sandbox_venv_cache_hit = extra["sandbox_venv_cache_hit"]
            if extra.get("sandbox_venv_create_time_s") is not None:
                sandbox_venv_setup_time_s = extra["sandbox_venv_create_time_s"]

    cache_hit_rate = round(cache_hits / model_calls, 2) if model_calls > 0 else 0.0

    return {
        "model_calls": model_calls,
        "cache_hits": cache_hits,
        "cache_hit_rate": cache_hit_rate,
        "model_calls_by_stage": calls_by_stage,
        "sandbox_venv_cache_hit": sandbox_venv_cache_hit,
        "sandbox_venv_setup_time_s": sandbox_venv_setup_time_s,
    }


def _measure_prompts(state_dir: Path, prompts_dir: Path) -> dict:
    """Reconstruct exact prompts from templates + state files, measure sizes."""
    result = {"design": {}, "implement": {}, "verify": {}}

    # Design prompt
    try:
        template = (prompts_dir / "design.txt").read_text()
        prompt = template.format(
            requirements=_read_state(state_dir, "inputs/REQUIREMENTS.md"),
            constraints=_read_state(state_dir, "inputs/CONSTRAINTS.md"),
            non_goals=_read_state(state_dir, "inputs/NON_GOALS.md"),
            extra_context="",
        )
        result["design"] = {"bytes": len(prompt.encode()), "chars": len(prompt)}
    except FileNotFoundError:
        result["design"] = {"bytes": 0, "chars": 0}

    # Implement prompt
    try:
        template = (prompts_dir / "implement.txt").read_text()
        prompt = template.format(
            architecture=_read_state(state_dir, "designs/ARCHITECTURE.md"),
            requirements=_read_state(state_dir, "inputs/REQUIREMENTS.md"),
            constraints=_read_state(state_dir, "inputs/CONSTRAINTS.md"),
        )
        result["implement"] = {"bytes": len(prompt.encode()), "chars": len(prompt)}
    except FileNotFoundError:
        result["implement"] = {"bytes": 0, "chars": 0}

    # Verify prompt
    try:
        template = (prompts_dir / "verify.txt").read_text()
        evidence = load_all_evidence()
        evidence_text = format_evidence_for_llm(evidence)
        prompt = template.format(
            evidence=evidence_text,
            acceptance_criteria=_read_state(state_dir, "inputs/ACCEPTANCE_CRITERIA.md"),
            requirements=_read_state(state_dir, "inputs/REQUIREMENTS.md"),
        )
        result["verify"] = {"bytes": len(prompt.encode()), "chars": len(prompt)}
    except FileNotFoundError:
        result["verify"] = {"bytes": 0, "chars": 0}

    total_bytes = sum(v["bytes"] for v in result.values())
    total_chars = sum(v["chars"] for v in result.values())
    result["total"] = {"bytes": total_bytes, "chars": total_chars}

    return result


def _read_state(state_dir: Path, rel_path: str) -> str:
    """Read a file from the state directory."""
    return (state_dir / rel_path).read_text()


def _measure_responses(state_dir: Path) -> dict:
    """Read output state files after a run and measure sizes."""
    files = {
        "design": "designs/ARCHITECTURE.md",
        "implement": "implementations/IMPLEMENTATION.md",
        "verify": "tests/VERIFICATION.md",
    }
    result = {}
    for stage, rel_path in files.items():
        path = state_dir / rel_path
        if path.exists():
            content = path.read_text()
            result[stage] = {"bytes": len(content.encode()), "chars": len(content)}
        else:
            result[stage] = {"bytes": 0, "chars": 0}

    total_bytes = sum(v["bytes"] for v in result.values())
    total_chars = sum(v["chars"] for v in result.values())
    result["total"] = {"bytes": total_bytes, "chars": total_chars}

    return result


# ── Pipeline runner ──────────────────────────────────────────────────────────


def _time_stage(fn, *args) -> float:
    """Call fn(*args) and return wall-clock seconds."""
    t0 = time.monotonic()
    fn(*args)
    return time.monotonic() - t0


def run_pipeline(project_dir: str) -> dict:
    """Execute one full pipeline run and collect metrics."""
    init_context(project_dir)
    run_id = init_run()

    stage_wall_s = {}
    t_total_start = time.monotonic()

    # Bootstrap (direct call — no gate)
    stage_wall_s["bootstrap"] = _time_stage(bootstrap_project.fn)

    # Design (gated)
    stage_wall_s["design"] = _time_stage(handle_gate, design_system.fn, "design", _abort_on_pause)

    # Implement (gated)
    stage_wall_s["implement"] = _time_stage(
        handle_gate, implement_system.fn, "implement", _abort_on_pause
    )

    # Extract (direct call — no gate)
    stage_wall_s["extract"] = _time_stage(extract_project.fn)

    # Test (gated)
    stage_wall_s["test"] = _time_stage(handle_gate, test_system.fn, "test", _abort_on_pause)

    # Verify (gated)
    stage_wall_s["verify"] = _time_stage(handle_gate, verify_system.fn, "verify", _abort_on_pause)

    total_wall_s = time.monotonic() - t_total_start

    # Post-run metrics
    state_dir = get_state_dir()
    prompts_dir = get_prompts_dir()

    trace_metrics = _parse_trace(run_id, state_dir)
    prompt_sizes = _measure_prompts(state_dir, prompts_dir)
    response_sizes = _measure_responses(state_dir)

    return {
        "run_id": run_id,
        "total_wall_s": round(total_wall_s, 3),
        "stage_wall_s": {k: round(v, 3) for k, v in stage_wall_s.items()},
        "model_calls": trace_metrics["model_calls"],
        "model_calls_by_stage": trace_metrics["model_calls_by_stage"],
        "cache_hits": trace_metrics["cache_hits"],
        "cache_hit_rate": trace_metrics["cache_hit_rate"],
        "sandbox_venv_cache_hit": trace_metrics["sandbox_venv_cache_hit"],
        "sandbox_venv_setup_time_s": trace_metrics["sandbox_venv_setup_time_s"],
        "prompt_payload_bytes": {
            stage: prompt_sizes[stage]["bytes"]
            for stage in ("design", "implement", "verify", "total")
        },
        "response_payload_bytes": {
            stage: response_sizes[stage]["bytes"]
            for stage in ("design", "implement", "verify", "total")
        },
        "prompt_chars": {
            stage: prompt_sizes[stage]["chars"]
            for stage in ("design", "implement", "verify", "total")
        },
        "response_chars": {
            stage: response_sizes[stage]["chars"]
            for stage in ("design", "implement", "verify", "total")
        },
    }


# ── Aggregation ──────────────────────────────────────────────────────────────


def _aggregate(runs: list[dict]) -> dict:
    """Compute mean/median stats across runs, structured by category."""
    if not runs:
        return {}

    totals = [r["total_wall_s"] for r in runs]

    # Total time stats
    total_time_stats = {
        "mean": round(mean(totals), 3),
        "median": round(median(totals), 3),
    }

    # Per-stage time stats
    all_stages: set[str] = set()
    for r in runs:
        all_stages.update(r.get("stage_wall_s", {}).keys())
    per_stage_time_stats = {}
    for stage in sorted(all_stages):
        stage_times = [r.get("stage_wall_s", {}).get(stage, 0) for r in runs]
        per_stage_time_stats[stage] = {
            "mean": round(mean(stage_times), 3),
            "median": round(median(stage_times), 3),
        }

    # LLM section
    all_llm_stages: set[str] = set()
    for r in runs:
        all_llm_stages.update(r.get("model_calls_by_stage", {}).keys())
    calls_by_stage = {
        stage: round(mean(r.get("model_calls_by_stage", {}).get(stage, 0) for r in runs), 1)
        for stage in sorted(all_llm_stages)
    }
    prompt_chars_by_stage = {}
    response_chars_by_stage = {}
    for stage in ("design", "implement", "verify"):
        prompt_chars_by_stage[stage] = round(
            mean(r.get("prompt_chars", {}).get(stage, 0) for r in runs)
        )
        response_chars_by_stage[stage] = round(
            mean(r.get("response_chars", {}).get(stage, 0) for r in runs)
        )
    cache_hit_rates = [r.get("cache_hit_rate", 0) for r in runs]

    llm = {
        "calls_by_stage": calls_by_stage,
        "prompt_chars_by_stage": prompt_chars_by_stage,
        "response_chars_by_stage": response_chars_by_stage,
        "cache_hit_rate": round(mean(cache_hit_rates), 2),
    }

    # Sandbox section
    venv_hits = [r.get("sandbox_venv_cache_hit") for r in runs]
    venv_hits_valid = [v for v in venv_hits if v is not None]
    venv_cache_hit_rate = (
        round(sum(1 for v in venv_hits_valid if v) / len(venv_hits_valid), 2)
        if venv_hits_valid
        else 0.0
    )
    venv_setup_times = [r.get("sandbox_venv_setup_time_s") for r in runs]
    venv_setup_valid = [v for v in venv_setup_times if v is not None]
    mean_venv_setup = round(mean(venv_setup_valid), 3) if venv_setup_valid else 0.0

    sandbox = {
        "venv_cache_hit_rate": venv_cache_hit_rate,
        "mean_venv_setup_time_s": mean_venv_setup,
    }

    # Backward-compat fields alongside new structure
    model_calls = [r["model_calls"] for r in runs]
    prompt_bytes = [r["prompt_payload_bytes"]["total"] for r in runs]
    response_bytes = [r["response_payload_bytes"]["total"] for r in runs]

    return {
        "total_time_stats": total_time_stats,
        "per_stage_time_stats": per_stage_time_stats,
        "llm": llm,
        "sandbox": sandbox,
        "mean_total_wall_s": total_time_stats["mean"],
        "min_total_wall_s": round(min(totals), 3),
        "max_total_wall_s": round(max(totals), 3),
        "mean_model_calls": round(mean(model_calls), 1),
        "mean_model_calls_by_stage": calls_by_stage,
        "mean_prompt_bytes": round(mean(prompt_bytes)),
        "mean_response_bytes": round(mean(response_bytes)),
    }


# ── CLI ──────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark the autonomous pipeline across N runs.")
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        help="Number of pipeline runs (default: 1)",
    )
    parser.add_argument(
        "--project-dir",
        required=True,
        help="Path to the project directory (must have completed intake)",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output JSON path (default: bench/results_<sha>.json)",
    )
    args = parser.parse_args()

    sha = _git_sha()
    runs: list[dict] = []

    for i in range(args.runs):
        print(f"[bench] Run {i + 1}/{args.runs} ...", flush=True)
        result = run_pipeline(args.project_dir)
        runs.append(result)
        print(
            f"[bench]   done in {result['total_wall_s']}s ({result['model_calls']} model calls)",
            flush=True,
        )

    output = {
        "git_sha": sha,
        "run_count": args.runs,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "config": {"runs": args.runs, "project_dir": args.project_dir},
        "runs": runs,
        "aggregate": _aggregate(runs),
    }

    out_path = args.out or str(_BENCH_DIR / f"results_{sha}.json")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(output, indent=2) + "\n")
    print(f"[bench] Results written to {out_path}")


if __name__ == "__main__":
    main()
