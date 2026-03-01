# Benchmark Runner

Measure pipeline efficiency before and after optimizations by running the full
autonomous pipeline N times against the same project spec.

## What's Measured

- **Wall-clock time** per stage and total (mean + median)
- **Model calls** (trace entries with a non-null `model` field), total and per-stage
- **Prompt payload** sizes (bytes and chars) for the 3 LLM stages (design, implement, verify)
- **Response payload** sizes (bytes and chars) from LLM output files
- **Cache hit rate** (ratio of cache hits to total model calls)
- **Sandbox metrics** (venv cache hit rate, venv setup time)

Results are written as structured JSON keyed by git SHA for easy before/after diffing.

## Prerequisites

1. Intake must be completed for the target project (`state/inputs/` populated)
2. API keys configured in `.env`
3. `templates/DECISION_GATES.yml` with all gate policies set to `skip` or `auto`

## Usage

```bash
python bench/benchmark_runs.py --runs 3 --project-dir ~/projects/myapp
```

Options:
- `--runs N` — number of pipeline iterations (default: 1)
- `--project-dir DIR` — path to the project with completed intake (required)
- `--out PATH` — custom output path (default: `bench/results_<sha>.json`)

## Comparing Results

Use the comparison tool to print deltas between two benchmark runs:

```bash
python bench/compare_results.py bench/results_abc1234.json bench/results_def5678.json
```

Example output:

```
Comparing: abc1234 -> def5678
Runs: 3 -> 3
============================================================

--- Wall Time ---
  Mean:   45.2s -> 32.1s  (-29.0%)
  Median: 44.8s -> 31.5s  (-29.7%)

--- Per-Stage Time (mean) ---
  bootstrap   : 0.1s -> 0.1s  (+0.0%)
  design      : 12.1s -> 8.5s  (-29.8%)
  implement   : 20.1s -> 15.3s  (-23.9%)
  verify      : 4.5s -> 0.2s  (-95.6%)

--- LLM ---
  Model calls: 3.0 -> 1.0  (-66.7%)
    design: 1.0 -> 0.0
    implement: 1.0 -> 1.0
    verify: 1.0 -> 0.0
  Cache hit rate: 0% -> 67%

============================================================
```

## JSON Format Reference

### Per-run entry

```json
{
  "run_id": "abc123",
  "total_wall_s": 45.2,
  "stage_wall_s": {"bootstrap": 0.1, "design": 15.3, "implement": 20.1, "extract": 0.2, "test": 5.0, "verify": 4.5},
  "model_calls": 3,
  "model_calls_by_stage": {"design": 1, "implement": 1, "verify": 1},
  "cache_hits": 0,
  "cache_hit_rate": 0.0,
  "sandbox_venv_cache_hit": false,
  "sandbox_venv_setup_time_s": 2.3,
  "prompt_payload_bytes": {"design": 2048, "implement": 4096, "verify": 3072, "total": 9216},
  "response_payload_bytes": {"design": 8192, "implement": 16384, "verify": 2048, "total": 26624},
  "prompt_chars": {"design": 2048, "implement": 4096, "verify": 3072, "total": 9216},
  "response_chars": {"design": 8192, "implement": 16384, "verify": 2048, "total": 26624}
}
```

### Results file (aggregate)

```json
{
  "git_sha": "1019aa5",
  "run_count": 3,
  "timestamp": "2026-02-28T12:00:00+00:00",
  "config": {"runs": 3, "project_dir": "..."},
  "runs": ["...per-run dicts..."],
  "aggregate": {
    "total_time_stats": {"mean": 45.0, "median": 44.8},
    "per_stage_time_stats": {
      "bootstrap": {"mean": 0.1, "median": 0.1},
      "design": {"mean": 12.1, "median": 11.9},
      "implement": {"mean": 15.3, "median": 15.0},
      "extract": {"mean": 0.2, "median": 0.2},
      "test": {"mean": 5.0, "median": 4.8},
      "verify": {"mean": 4.5, "median": 4.3}
    },
    "llm": {
      "calls_by_stage": {"design": 1.0, "implement": 1.0, "verify": 1.0},
      "prompt_chars_by_stage": {"design": 5000, "implement": 8000, "verify": 3000},
      "response_chars_by_stage": {"design": 3000, "implement": 6000, "verify": 2000},
      "cache_hit_rate": 0.0
    },
    "sandbox": {
      "venv_cache_hit_rate": 0.5,
      "mean_venv_setup_time_s": 2.3
    },
    "mean_total_wall_s": 45.0,
    "min_total_wall_s": 43.1,
    "max_total_wall_s": 47.2,
    "mean_model_calls": 3.0,
    "mean_model_calls_by_stage": {"design": 1.0, "implement": 1.0, "verify": 1.0},
    "mean_prompt_bytes": 9216,
    "mean_response_bytes": 26624
  }
}
```

## Future Enhancements

- Capture API token usage from provider responses (currently discarded by `llm_provider.generate()`)
- Memory profiling per stage
- Automated regression detection with threshold alerts
