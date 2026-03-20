# Autonomy Engine v1.3

![Tests](https://img.shields.io/badge/tests-259%20collected-brightgreen)

A Prefect-based autonomous build engine that turns project specs into working software — with human-in-the-loop decision gates, cryptographic traceability, and strict contract enforcement between pipeline stages.

## What This Is For

- Automating structured software build workflows (design → implement → extract → test → verify)
- Enforcing strict contracts between pipeline stages so LLMs can't drift from the design
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
│ (intake CLI / Dashboard) │
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
[design]    ──→ state/designs/              ←── DESIGN_CONTRACT.json + ARCHITECTURE.md
                │                                may pause at decision gate
                │ canonical types + component contracts
                ↓
[implement] ──→ state/implementations/      ←── contract-driven chunked implementation
                │                                each chunk gets canonical schema + file list
                ↓
[extract]   ──→ ../<project-name>/          ←── standalone project folder
                 + state/build/MANIFEST.md       circuit breaker enforces file/size limits
                    ↓
[test]      ──→ state/tests/                ←── auto-detect checks + contract compliance
                │                                mandatory type/import/lint validation
                ↓
[verify]    ──→ state/tests/VERIFICATION.md ←── structural issue analysis + go/no-go
```

The **extract** step parses `IMPLEMENTATION.md` for fenced code blocks marked with
bold filenames (`**path/to/file.ext**`) or header filenames (`### file.ext`), then
writes each file to a sibling directory named after the project. No LLM call — pure
regex parsing. A circuit breaker halts extraction if the output exceeds tier-appropriate
file count or byte size limits (MVP: 80 files / 750KB, Premium: 250 files / 5MB).

## Quickstart

```bash
pip install -e ".[dev]"
python -m intake.intake new-project
python flows/autonomous_flow.py
```

Or use the dashboard:

```bash
pip install -e ".[dashboard]"
streamlit run dashboard/app.py
```

### Core Principles

1. **If it isn't written, it doesn't exist** — tasks communicate through files in `state/`, not return values
2. **Contracts, not interpretation** — structured JSON contracts replace prose handoffs between stages; LLMs get exact file lists, canonical types, and dependency graphs instead of vague instructions
3. **Gates are policy, not behavior** — tasks raise `DecisionRequired`; the flow applies the policy from `DECISION_GATES.yml`
4. **Structured state** — `state/` has predefined subfolders, not a flat directory
5. **Traceability** — every task appends to `trace.jsonl` with inputs, outputs, model, provider, max_tokens, and prompt hash
6. **Spec says what, config says how** — the project spec captures *what to build*; `config.yml` captures *how to run the engine* (LLM provider, sandbox, notifications)

## Contract System

The contract system is the engine's defense against LLM interpretation drift — the root cause of most cross-chunk inconsistencies (wrong field names, missing files, phantom imports).

### Design Contract

The design stage produces `DESIGN_CONTRACT.json` alongside `ARCHITECTURE.md`. This structured JSON specifies exactly what the implementation stage must produce:

- **Components** with exact file lists, dependency declarations, and file budgets
- **Canonical types** with field definitions, kinds (interface/class/enum), and owning file paths
- **Tech decisions** with rationale (so the LLM doesn't second-guess them)
- **Import maps** declaring which components depend on which

The contract is validated at creation time (15+ checks for duplicates, phantom dependencies, budget overflows, type reference validity).

### Canonical Type Schema

Every implementation chunk receives the canonical type schema as authoritative law. Types are defined once in the design contract and injected verbatim into every chunk's prompt. The rules are strict: use exact names, don't add fields, don't rename fields.

### Contract Compliance Checker

After extraction, the contract checker validates the output against the design:

- Missing files (contract says X, only Y produced)
- Extra files (produced but not in any component's plan)
- Per-component and total file budget violations
- Canonical type presence (type name exists in the right file)
- Field presence (expected fields appear in the file)

Results are saved as evidence records that feed into the test and verify stages.

### Spec Normalizer

User input is normalized before reaching the design stage. The normalizer parses `project_spec.yml` into structured fields, detects ambiguous tech choices (e.g., "React or Vue"), assigns feature priorities, and splits constraints into categories.

## Tier System

The engine supports two build tiers:

- **Premium** — full output budget, higher cost, no scope restrictions
- **MVP** — hard limits enforced at every level:
  - Design guidance mandates ≤5 components, ≤40 files
  - Per-chunk implementation ceiling of 10 files
  - Circuit breaker halts extraction at 80 files / 750KB
  - Cost estimate shows ~savings vs Premium

Tier selection happens in the dashboard before launch, with per-stage token breakdowns and cost estimates.

## Test & Verification

### Auto-Detect Checks

The test stage inspects the extracted project and automatically discovers appropriate checks:

**Node.js/TypeScript projects** (detected via `package.json`): npm install, typecheck (tsc --noEmit), build, lint, test — in that order.

**Python projects** (detected via `pyproject.toml` / `requirements.txt`): pip install, syntax check (py_compile), import validation (ast-based), ruff lint, mypy typecheck, pytest — always includes mandatory structural checks.

### Structural Issue Analysis

The verify stage classifies failures into categories: type errors, import errors, lint errors, build errors, test failures, and contract compliance issues. Each category gets specific diagnostic output and actionable guidance. The LLM-powered verify path receives this classification so it can give targeted root-cause analysis instead of generic summaries.

### Evidence Records

Every check produces a structured JSON evidence record with exit code, stdout, stderr, timestamps, and environment metadata. These feed into the dashboard's evidence viewer and the verify stage's analysis.

## Decision Gates

Gate policies are defined in `templates/DECISION_GATES.yml` with three modes per stage:

- **pause** — block for human input via Prefect UI (default for `design`)
- **auto** — auto-select the configured default option
- **skip** — swallow the gate and continue (default for `implement`, `test`, `verify`)

Gates trigger on architectural tradeoffs (design stage), test failures (test stage), and verification rejection (verify stage). Not for missing requirements or incomplete specs — those are blocked at intake.

## Dashboard

The dashboard is a Streamlit app with dark-mode native styling.

**Install and launch:**
```bash
pip install -e ".[dashboard]"
streamlit run dashboard/app.py
```

Or specify a project directory:
```bash
AUTONOMY_ENGINE_PROJECT_DIR=/path/to/project streamlit run dashboard/app.py
```

**Navigation** is organized into two groups:

*Main:*
- **Dashboard** — Pipeline status (with evidence-aware pass/fail), recent runs, cache stats
- **Create Project** — Form-based intake with project manager (load previous, clear, view run history)
- **Run Pipeline** — Tier selection, cost estimates, live progress with trace timeline

*Security & Ops:*
- **Inspector** — Detailed trace timeline, evidence records, decisions, artifacts, config snapshot per run
- **Audit Trail** — Hash chain visualization with integrity verification and export
- **Configuration** — Active LLM settings, gate policies, sandbox config, check commands
- **Benchmarks** — Per-stage timing charts, cache hit rates, before/after comparison

Pipeline stage indicators reflect actual status: green only when the stage ran successfully with passing evidence, red when checks failed, amber when in progress, gray when pending. The dashboard never blindly shows "success" — it reads evidence records and shows real pass/fail counts with diagnostic output.

### Theme System

All dashboard colors, typography, and spacing are centralized in `dashboard/theme.py`. Custom HTML elements use dark-mode-native colors (translucent surfaces, light text). Streamlit's native theming handles standard elements. This avoids the "white block on dark background" problem.

## Audit Reports

After a run completes, export a self-contained audit bundle:

```bash
python -m engine.report --run-id <id> [--out path] [--project-dir dir]
```

This produces a `.tar.gz` containing the trace, config snapshot, evidence,
decisions, a rebuilt artifact manifest, and an integrity check result.

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

Or use the **Create Project** page in the dashboard for a form-based intake with auto-saving fields, project loading, and one-click pipeline launch.

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

Or use the **Run Pipeline** page in the dashboard for tier selection, cost estimates, and live monitoring.

### Project directory layout

When using `--project-dir`, the scaffolded directory looks like:

```
~/projects/solo1/
  config.yml              # Copied from engine — edit to customize
  templates/              # Copied from engine — edit to customize
    DECISION_GATES.yml
    prompts/
      design.txt, implement.txt, implement_chunk.txt, verify.txt
  state/
    runs/<run_id>/trace.jsonl
    inputs/ designs/ implementations/ tests/ decisions/ build/
    cache/llm/            # LLM response cache
    sandbox_cache/        # Venv + pip cache
```

Without `--project-dir`, all state and config lives in the engine root.

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

## Efficiency Features

### Per-Stage Model Selection

Each pipeline stage can use a different LLM model. Configure via `config.yml` under `llm.models`. If a stage key is missing, falls back to `llm.<provider>.model`.

### LLM Response Caching

Deterministic cache under `state/cache/llm/`. Cache key is derived from stage, prompt template hash, input content hash, model name, and generation parameters. If inputs haven't changed, the cached response is reused without an API call. Cache artifacts are immutable (first write wins, never overwritten).

### Selective Verify

The verify stage supports three modes to avoid unnecessary LLM calls:

- **`always_llm`** — always call the LLM (default)
- **`auto`** — skip LLM when evidence makes outcome obvious (controlled by `llm_on_pass_summary` and `llm_on_fail_summary` flags)
- **`never_llm`** — always write a deterministic VERIFICATION.md with structural issue breakdown

### Sandbox Venv Caching

Virtualenvs created for test sandbox execution are cached under `state/sandbox_cache/venvs/`, keyed on dependency spec + Python version + sandbox config. Subsequent runs with the same dependencies reuse the cached venv. A shared pip cache (`state/sandbox_cache/pip/`) further speeds up dependency installation.

### Benchmarking

```bash
python bench/benchmark_runs.py --runs 3 --project-dir ~/projects/myapp
python bench/compare_results.py bench/results_old.json bench/results_new.json
```

See `bench/README.md` for full documentation.

## Project Structure

```
intake/             Project intake CLI and Pydantic schema
  schema.py         ProjectSpec definition (what to build)
  renderer.py       Generates engine artifacts from validated spec
  intake.py         CLI entry point (new-project, from-file, edit, validate)

engine/             Core modules
  context.py        Singleton path context — resolves project vs engine root paths
  decision_gates.py Gate policies (pause/auto/skip) from DECISION_GATES.yml
  llm_provider.py   Claude + OpenAI behind a unified interface
  tracer.py         Hash-chained trace entries (trace.jsonl)
  evidence.py       Auto-detect checks, structured execution, evidence capture
  report.py         Audit bundle exporter (tar.gz with trace, evidence, integrity)
  notifier.py       Notification via logging (replace for Slack/email/PagerDuty)
  design_contract.py  Design contract schema, validation (15+ checks), extraction
  contract_checker.py Post-extraction compliance validation against design contract
  spec_normalizer.py  Normalize user input, detect ambiguity, structure for design
  tier_context.py     Tier-aware scope guidance injected into LLM prompts
  cost_estimator.py   Heuristic token/cost estimation for tier selection
  cache.py            Deterministic LLM response caching

flows/              Prefect flow definition — the entry point
tasks/              Individual pipeline stages as Prefect tasks
  bootstrap.py      Input validation, trace initialization
  design.py         Architecture + design contract generation
  implement.py      Contract-driven chunked implementation with canonical schemas
  extract.py        Code extraction with circuit breaker
  test.py           Auto-detect checks + contract compliance
  verify.py         Structural issue classification + LLM/deterministic verification

templates/          Gate policies and LLM prompt templates
  prompts/          LLM prompt files (tracked by SHA-256 hash in trace)
    design.txt      Design prompt with contract JSON requirements
    implement.txt   Single-call implementation prompt
    implement_chunk.txt  Per-chunk prompt with canonical schema injection
    verify.txt      Verification prompt with structural analysis

dashboard/          Streamlit web dashboard
  app.py            Entry point, sidebar navigation, page routing
  theme.py          Centralized dark-mode colors, typography, CSS, helper functions
  data_loader.py    Filesystem-based data loading (no engine imports)
  pipeline_runner.py  Subprocess launcher for pipeline execution
  pages/            Page modules (home, create_project, run_pipeline, etc.)
  components/       Reusable UI components (pipeline_visual, trace_timeline, etc.)

state/              Runtime artifacts (gitignored except .gitkeep)
  runs/<id>/        Per-run trace, evidence, and decisions
  inputs/           Intake-generated artifacts (project_spec.yml + rendered markdown)
  designs/          Architecture documents + DESIGN_CONTRACT.json
  implementations/  Generated code
  tests/            Test and verification results
  build/            Extraction manifest (MANIFEST.md)
  cache/llm/        LLM response cache
  sandbox_cache/    Venv + pip cache for test sandbox

tests/              Pytest test suite (259 tests)
bench/              Benchmark runner and comparison tools
```
