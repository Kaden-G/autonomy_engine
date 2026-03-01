"""Compare two benchmark result files and print deltas.

Usage:
    python bench/compare_results.py bench/results_old.json bench/results_new.json
"""

import argparse
import json
from pathlib import Path


def _pct(old: float, new: float) -> str:
    """Format a percentage delta between old and new values."""
    if old == 0:
        return "N/A (baseline was 0)"
    delta = ((new - old) / old) * 100
    sign = "+" if delta > 0 else ""
    return f"{sign}{delta:.1f}%"


def _fmt(value: float, unit: str = "") -> str:
    """Format a numeric value with optional unit."""
    if isinstance(value, float):
        formatted = f"{value:.1f}"
    else:
        formatted = str(value)
    return f"{formatted}{unit}" if unit else formatted


def compare(old_path: str, new_path: str) -> None:
    """Load two result files and print a comparison table."""
    old = json.loads(Path(old_path).read_text())
    new = json.loads(Path(new_path).read_text())

    old_agg = old.get("aggregate", {})
    new_agg = new.get("aggregate", {})

    old_sha = old.get("git_sha", "?")
    new_sha = new.get("git_sha", "?")

    print(f"Comparing: {old_sha} -> {new_sha}")
    print(
        f"Runs: {old.get('run_count', len(old.get('runs', [])))} -> "
        f"{new.get('run_count', len(new.get('runs', [])))}"
    )
    print("=" * 60)

    # Total time
    old_time_stats = old_agg.get("total_time_stats", {})
    new_time_stats = new_agg.get("total_time_stats", {})
    old_mean = old_time_stats.get("mean", old_agg.get("mean_total_wall_s", 0))
    new_mean = new_time_stats.get("mean", new_agg.get("mean_total_wall_s", 0))
    old_med = old_time_stats.get("median", 0)
    new_med = new_time_stats.get("median", 0)

    print("\n--- Wall Time ---")
    print(f"  Mean:   {_fmt(old_mean, 's')} -> {_fmt(new_mean, 's')}  ({_pct(old_mean, new_mean)})")
    if old_med or new_med:
        print(f"  Median: {_fmt(old_med, 's')} -> {_fmt(new_med, 's')}  ({_pct(old_med, new_med)})")

    # Per-stage times
    old_stages = old_agg.get("per_stage_time_stats", {})
    new_stages = new_agg.get("per_stage_time_stats", {})
    all_stages = sorted(set(old_stages) | set(new_stages))
    if all_stages:
        print("\n--- Per-Stage Time (mean) ---")
        for stage in all_stages:
            o = old_stages.get(stage, {}).get("mean", 0)
            n = new_stages.get(stage, {}).get("mean", 0)
            print(f"  {stage:12s}: {_fmt(o, 's')} -> {_fmt(n, 's')}  ({_pct(o, n)})")

    # Model calls
    old_calls = old_agg.get("mean_model_calls", 0)
    new_calls = new_agg.get("mean_model_calls", 0)
    print("\n--- LLM ---")
    print(f"  Model calls: {_fmt(old_calls)} -> {_fmt(new_calls)}  ({_pct(old_calls, new_calls)})")

    # Calls by stage
    old_llm = old_agg.get("llm", {})
    new_llm = new_agg.get("llm", {})
    old_cbs = old_llm.get("calls_by_stage", old_agg.get("mean_model_calls_by_stage", {}))
    new_cbs = new_llm.get("calls_by_stage", new_agg.get("mean_model_calls_by_stage", {}))
    llm_stages = sorted(set(old_cbs) | set(new_cbs))
    if llm_stages:
        for stage in llm_stages:
            o = old_cbs.get(stage, 0)
            n = new_cbs.get(stage, 0)
            print(f"    {stage}: {_fmt(o)} -> {_fmt(n)}")

    # Cache hit rate
    old_chr = old_llm.get("cache_hit_rate", 0)
    new_chr = new_llm.get("cache_hit_rate", 0)
    print(f"  Cache hit rate: {old_chr:.0%} -> {new_chr:.0%}")

    # Prompt sizes
    old_pcs = old_llm.get("prompt_chars_by_stage", {})
    new_pcs = new_llm.get("prompt_chars_by_stage", {})
    if old_pcs or new_pcs:
        print("\n--- Prompt Chars by Stage ---")
        for stage in ("design", "implement", "verify"):
            o = old_pcs.get(stage, 0)
            n = new_pcs.get(stage, 0)
            print(f"  {stage:12s}: {o} -> {n}  ({_pct(o, n)})")

    # Response sizes
    old_rcs = old_llm.get("response_chars_by_stage", {})
    new_rcs = new_llm.get("response_chars_by_stage", {})
    if old_rcs or new_rcs:
        print("\n--- Response Chars by Stage ---")
        for stage in ("design", "implement", "verify"):
            o = old_rcs.get(stage, 0)
            n = new_rcs.get(stage, 0)
            print(f"  {stage:12s}: {o} -> {n}  ({_pct(o, n)})")

    # Prompt bytes (backward compat)
    old_pb = old_agg.get("mean_prompt_bytes", 0)
    new_pb = new_agg.get("mean_prompt_bytes", 0)
    old_rb = old_agg.get("mean_response_bytes", 0)
    new_rb = new_agg.get("mean_response_bytes", 0)
    print("\n--- Payload Bytes ---")
    print(f"  Prompt:   {old_pb} -> {new_pb}  ({_pct(old_pb, new_pb)})")
    print(f"  Response: {old_rb} -> {new_rb}  ({_pct(old_rb, new_rb)})")

    # Sandbox
    old_sb = old_agg.get("sandbox", {})
    new_sb = new_agg.get("sandbox", {})
    if old_sb or new_sb:
        print("\n--- Sandbox ---")
        o_vhr = old_sb.get("venv_cache_hit_rate", 0)
        n_vhr = new_sb.get("venv_cache_hit_rate", 0)
        print(f"  Venv cache hit rate: {o_vhr:.0%} -> {n_vhr:.0%}")
        o_vst = old_sb.get("mean_venv_setup_time_s", 0)
        n_vst = new_sb.get("mean_venv_setup_time_s", 0)
        print(
            f"  Mean venv setup:    {_fmt(o_vst, 's')} -> {_fmt(n_vst, 's')}  ({_pct(o_vst, n_vst)})"
        )

    print("\n" + "=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare two benchmark results.")
    parser.add_argument("old", help="Path to baseline results JSON")
    parser.add_argument("new", help="Path to new results JSON")
    args = parser.parse_args()
    compare(args.old, args.new)


if __name__ == "__main__":
    main()
