# Benchmark Runner

Measure pipeline efficiency before and after optimizations by running the full
autonomous pipeline N times against the same project spec.

## What's Measured

- **Wall-clock time** per stage and total
- **Model calls** (trace entries with a non-null `model` field), total and per-stage
- **Prompt payload** sizes (bytes and chars) for the 3 LLM stages (design, implement, verify)
- **Response payload** sizes (bytes and chars) from LLM output files
- **Cache hits** (trace entries with `extra.cache_hit`)

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

1. Run a baseline benchmark on the current code
2. Make your optimization changes
3. Run a second benchmark
4. Diff the two JSON files:

```bash
diff <(python -m json.tool bench/results_abc1234.json) \
     <(python -m json.tool bench/results_def5678.json)
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
  "prompt_payload_bytes": {"design": 2048, "implement": 4096, "verify": 3072, "total": 9216},
  "response_payload_bytes": {"design": 8192, "implement": 16384, "verify": 2048, "total": 26624},
  "prompt_chars": {"design": 2048, "implement": 4096, "verify": 3072, "total": 9216},
  "response_chars": {"design": 8192, "implement": 16384, "verify": 2048, "total": 26624}
}
```

### Results file

```json
{
  "git_sha": "1019aa5",
  "timestamp": "2026-02-28T12:00:00+00:00",
  "config": {"runs": 3, "project_dir": "..."},
  "runs": ["...per-run dicts..."],
  "aggregate": {
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
