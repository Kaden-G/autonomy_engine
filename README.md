# Autonomy Engine v1.2

![Tests](https://img.shields.io/badge/tests-337%20passing-brightgreen)

A Prefect-based autonomous build engine with human-in-the-loop decision gates.

## What This Is For

- Automating structured software build workflows (design, implement, test, verify, extract)
- Providing human oversight at critical decision points via Prefect's pause/resume
- Maintaining full traceability of every step, prompt, and decision in hash-chained `trace.jsonl`
- Supporting multiple LLM providers (Claude, OpenAI) behind a unified interface
- Extracting generated code into a standalone, ready-to-run project folder

## What This Is NOT For

- Replacing human judgment on ethical, security, or architectural decisions
- Unsupervised production deployments
- General-purpose AI agent framework — this is a specific pipeline, not a platform
- Real-time or latency-sensitive workflows

## Architecture

```
┌──────────────────────────┐
│ Intake Layer             │  ← Phase 0: human-driven, blocking
│ (intake CLI / YAML file) │
└────────────┬─────────────┘
             │ validated, complete
┌────────────▼─────────────┐
│ Normalized Project Spec  │  ← machine contract (what to build)
│ (state/inputs/)          │
└────────────┬─────────────┘
             │ read-only
┌────────────▼─────────────┐
│ Autonomous Engine        │  ← Phase 1: machine-driven, no ambiguity
│ (Prefect flow)           │     runtime config from config.yml (how to run)
└──────────────────────────┘
```

### Pipeline Stages

```
[intake]    ──→ state/inputs/project_spec.yml + rendered artifacts
                    ↓
[bootstrap] ──→ verify inputs, init trace.jsonl
                    ↓
[design]    ──→ state/designs/              ←── may pause at decision gate
                    ↓
[implement] ──→ state/implementations/
                    ↓
[extract]   ──→ ../<project-name>/          ←── standalone project folder
                 + state/build/MANIFEST.md
                    ↓
[test]      ──→ state/tests/                ←── may trigger gate on failures
                    ↓
[verify]    ──→ state/tests/VERIFICATION.md ←── may trigger gate on REJECTED
```

The **extract** step parses `IMPLEMENTATION.md` for fenced code blocks marked with
bold filenames (`**path/to/file.ext**`) or header filenames (`### file.ext`), then
writes each file to a sibling directory named after the project. No LLM call — pure
regex parsing.

## Quickstart

```bash
pip install -e ".[dev]"
python -m intake.intake new-project
python flows/autonomous_flow.py
```

### Core Principles

1. **If it isn't written, it doesn't exist** — tasks communicate through files in `state/`, not return values
2. **Gates are policy, not behavior** — tasks raise `DecisionRequired`; the flow applies the policy from `DECISION_GATES.yml`
3. **Structured state** — `state/` has predefined subfolders, not a flat directory
4. **Traceability** — every task appends to `trace.jsonl` with inputs, outputs, model, provider, max_tokens, and prompt hash
5. **Spec says what, config says how** — the project spec captures *what to build*; `config.yml` captures *how to run the engine* (LLM provider, sandbox, notifications)

### Decision Gates

Gate policies are defined in `templates/DECISION_GATES.yml` with three modes per stage:

- **pause** — block for human input via Prefect UI (default for `design`)
- **auto** — auto-select the configured default option
- **skip** — swallow the gate and continue (default for `implement`, `test`, `verify`)

Gates trigger on:
- Architectural tradeoffs (design stage, LLM signals ambiguity)
- Test failures (test stage, non-zero exit codes detected)
- Verification rejection (verify stage, LLM outputs REJECTED)

**Not** for missing requirements, clarification questions, or incomplete specs. Those are blocked at intake.

## Setup

```bash
cd ~/Desktop/autonomy_engine
pip install -e ".[dev]"
```

### Environment Setup

1. Copy the example environment file:
   ```bash
   cp .env.example .env
   ```
2. Fill in your API keys in `.env`
3. Enable the pre-commit hook:
   ```bash
   git config core.hooksPath .githooks
   ```

**Security:** The `.env` file is gitignored. The pre-commit hook will reject any attempt to commit files containing API keys. Never share `.env` — use `.env.example` as the template.

## Usage

### Step 1: Intake (required)

Interactive (engine root — default):
```bash
python -m intake.intake new-project
```

Interactive (external project directory):
```bash
python -m intake.intake --project-dir ~/projects/solo1 new-project
```

This scaffolds the project directory with a copy of `config.yml`, `templates/`, and
the `state/` folder structure, then runs the interactive intake. Edit the copied
templates to customize prompts per-project.

Or from a YAML file:
```bash
python -m intake.intake from-file path/to/project_spec.yml
python -m intake.intake --project-dir ~/projects/solo1 from-file path/to/project_spec.yml
```

Edit an existing spec:
```bash
python -m intake.intake edit
python -m intake.intake --project-dir ~/projects/solo1 edit
```

Validate only:
```bash
python -m intake.intake validate path/to/project_spec.yml
```

### Step 2: Run the engine

```bash
# Start Prefect server (separate terminal)
prefect server start

# Run the flow (engine root)
python flows/autonomous_flow.py

# Run the flow against an external project
python flows/autonomous_flow.py --project-dir ~/projects/solo1
```

The engine will refuse to start if intake has not been completed.

### Dashboard

The Autonomy Engine includes a web dashboard for pipeline monitoring, audit trail inspection, and performance benchmarking.

**Install:**
```bash
pip install -e ".[dashboard]"
```

**Launch:**
```bash
streamlit run dashboard/app.py
```

Or specify a project directory:
```bash
AUTONOMY_ENGINE_PROJECT_DIR=/path/to/project streamlit run dashboard/app.py
```

**Pages:**
- **Dashboard** — Pipeline status, recent runs, cache stats
- **Run Inspector** — Detailed trace timeline, evidence, decisions, artifacts per run
- **Audit Trail** — Hash chain visualization with integrity verification
- **Configuration** — Active settings, gate policies, check commands
- **Benchmarks** — Per-stage timing charts, cache hit rates, before/after comparison

### Audit Reports

After a run completes, export a self-contained audit bundle:

```bash
python -m engine.report --run-id <id> [--out path] [--project-dir dir]
```

This produces a `.tar.gz` containing the trace, config snapshot, evidence,
decisions, a rebuilt artifact manifest, and an integrity check result.
Failed integrity is recorded (not an error) so auditors can see exactly
what broke.

The flow will appear in the Prefect UI at `http://localhost:4200`. If a decision gate triggers with `pause` policy, resume from the UI.

After a successful run, the final project files are extracted to a sibling directory:

```
~/Desktop/
├── autonomy_engine/      # engine root
└── my-project/           # extracted project (slugified name from spec)
    ├── app.py
    ├── requirements.txt
    ├── models/
    │   └── ...
    └── ...
```

A manifest of all extracted files is saved to `state/build/MANIFEST.md`.

### Project directory layout

When using `--project-dir`, the scaffolded directory looks like:

```
~/projects/solo1/
  config.yml              # Copied from engine — edit to customize
  templates/              # Copied from engine — edit to customize
    DECISION_GATES.yml
    prompts/
      design.txt, implement.txt, verify.txt
  state/
    runs/<run_id>/trace.jsonl
    inputs/ designs/ implementations/ tests/ decisions/ build/
```

Without `--project-dir`, all state and config lives in the engine root (unchanged
from previous behavior).

## Efficiency Features

### Per-Stage Model Selection

Each pipeline stage can use a different LLM model. Configure via `config.yml`:

```yaml
llm:
  models:
    design: "claude-sonnet-4-20250514"      # cheaper model for structuring
    implement: "claude-sonnet-4-20250514"    # full model for code generation
    verify: "claude-sonnet-4-20250514"       # cheaper model for summarizing
```

If a stage key is missing, falls back to `llm.<provider>.model`.

### LLM Response Caching

Deterministic cache under `state/cache/llm/`. Cache key is derived from stage, prompt template hash, input content hash, model name, and generation parameters. If inputs haven't changed, the cached response is reused without an API call. Cache artifacts are immutable (first write wins, never overwritten).

### Selective Verify

The verify stage supports three modes to avoid unnecessary LLM calls:

- **`always_llm`** — always call the LLM (default, original behavior)
- **`auto`** — skip LLM when evidence makes outcome obvious (controlled by `llm_on_pass_summary` and `llm_on_fail_summary` flags)
- **`never_llm`** — always write a deterministic VERIFICATION.md

### Sandbox Venv Caching

Virtualenvs created for test sandbox execution are cached under `state/sandbox_cache/venvs/`, keyed on dependency spec + Python version + sandbox config. Subsequent runs with the same dependencies reuse the cached venv instead of creating a new one. A shared pip cache (`state/sandbox_cache/pip/`) further speeds up dependency installation.

### Benchmarking

Measure pipeline efficiency with the benchmark runner:

```bash
python bench/benchmark_runs.py --runs 3 --project-dir ~/projects/myapp
```

Compare before/after results:

```bash
python bench/compare_results.py bench/results_old.json bench/results_new.json
```

See `bench/README.md` for full documentation.

## Configuration

### config.yml — Runtime settings

```yaml
llm:
  provider: "claude"       # or "openai"
  max_tokens: 16384
  claude:
    model: "claude-sonnet-4-20250514"
  openai:
    model: "gpt-4o"
  models:                  # per-stage model overrides (optional)
    design: "claude-sonnet-4-20250514"
    implement: "claude-sonnet-4-20250514"
    verify: "claude-sonnet-4-20250514"

notifications:
  enabled: false           # log-only; replace engine/notifier.py for real alerts

sandbox:
  enabled: true            # isolate test execution in temp workspace
  install_deps: true

verify:
  mode: always_llm         # always_llm | auto | never_llm
  llm_on_fail_summary: true
  llm_on_pass_summary: true

checks: []                 # approved test commands (see config.yml for examples)
```

### DECISION_GATES.yml — Gate policies

```yaml
gates:
  design:
    policy: "pause"        # pause | auto | skip
  test:
    policy: "skip"
    default_option: "continue"
  verify:
    policy: "skip"
    default_option: "accept"
```

## Project Structure

```
intake/             Project intake CLI and Pydantic schema
  schema.py         ProjectSpec definition (what to build)
  renderer.py       Generates engine artifacts from validated spec
  intake.py         CLI entry point (new-project, from-file, edit, validate)
engine/             Core modules (LLM provider, gates, state, tracing, notifications)
  context.py        Singleton path context — resolves project vs engine root paths
  decision_gates.py Gate policies (pause/auto/skip) loaded from DECISION_GATES.yml
  llm_provider.py   Claude + OpenAI behind a unified interface
  tracer.py         Hash-chained trace entries (trace.jsonl)
  evidence.py       Structured command execution and evidence capture
  report.py         Audit bundle exporter (tar.gz with trace, evidence, integrity)
  notifier.py       Notification via Python logging (replace for real alerts)
                    Notification adapter is designed for extension — replace
                    engine/notifier.py to integrate Slack, email, or PagerDuty.
flows/              Prefect flow definition — the entry point
tasks/              Individual pipeline stages as Prefect tasks
templates/          Gate policies and LLM prompt templates
  prompts/          LLM prompt files (tracked by SHA-256 hash in trace)
state/              Runtime artifacts (gitignored except .gitkeep)
  runs/<id>/        Per-run trace, evidence, and decisions
  inputs/           Intake-generated artifacts (project_spec.yml + rendered markdown)
  designs/          Architecture documents
  implementations/  Generated code
  tests/            Test and verification results
  build/            Extraction manifest (MANIFEST.md)
tests/              Pytest test suite
```
