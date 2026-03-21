# Autonomy Engine v1.3

![Tests](https://img.shields.io/badge/tests-265%20passing-brightgreen)
![Coverage](https://img.shields.io/badge/coverage-61%25%20engine-blue)
![Security](https://img.shields.io/badge/bandit-1%20known%20%7C%200%20unexpected-yellow)
![Deps](https://img.shields.io/badge/pip--audit-0%20project%20vulns-brightgreen)

An autonomous software build pipeline that turns a project description into working, tested code — with human approval gates, tamper-evident audit trails, and strict quality contracts that keep AI-generated output on-spec.

Built on [Prefect](https://www.prefect.io/) (workflow orchestration) and compatible with Claude and OpenAI as the underlying AI models.

---

## The Problem This Solves

When you ask an AI model to write an entire software project, three things consistently go wrong:

1. **Drift.** The AI forgets decisions it made earlier and contradicts itself across files.
2. **No receipts.** You can't prove what the AI was asked, what it produced, or whether anyone reviewed it.
3. **All-or-nothing.** The output is either accepted wholesale or thrown away — there's no structured quality gate.

The Autonomy Engine addresses all three by wrapping AI code generation in a pipeline with formal contracts, evidence-based testing, and a cryptographically signed audit trail.

---

## How It Works (The 60-Second Version)

```
You describe what to build
        ↓
Engine creates a binding design contract (JSON blueprint)
        ↓
AI writes code in chunks, each checked against the contract
        ↓
Code is extracted into a standalone project folder
        ↓
Automated tests run: syntax, imports, linting, type safety, contract compliance
        ↓
A verification report gives a go/no-go recommendation
        ↓
Every step is recorded in a tamper-evident audit log
```

At any stage, the pipeline can pause and ask a human to approve before continuing. You stay in control; the AI stays in its lane.

---

## What This Is For

- Automating structured build workflows where traceability matters
- Enforcing that AI-generated code matches the approved design — no silent deviations
- Providing human oversight at critical decision points (architecture, test failures, final sign-off)
- Maintaining a complete, tamper-evident record of every prompt, decision, and output
- Comparing actual AI costs (tokens used) against pre-run estimates

## What This Is NOT For

- Replacing human judgment on security, ethics, or architecture
- Unsupervised production deployments — this is a supervised tool
- General-purpose AI agent framework — this is a specific pipeline, not a platform
- Real-time or latency-sensitive workflows

---

## Supported Project Types

The engine doesn't just generate code — it also tests, lints, type-checks, and verifies it. That full pipeline (generate → extract → test → verify) requires the engine to understand the project's toolchain. Today, two ecosystems are fully supported end-to-end:

### Python (fully supported)

Detected by: `requirements.txt`, `pyproject.toml`, or `setup.py`

The engine auto-configures: dependency install (`pip`), syntax validation (`py_compile`), import resolution (custom AST-based checker), linting with auto-fix (`ruff`), type checking (`mypy`), and unit tests (`pytest` when a test directory or config is present). A Python virtualenv is created in the sandbox, cached across runs when dependencies haven't changed.

### Node.js / TypeScript (fully supported)

Detected by: `package.json`

The engine auto-configures: dependency install (`npm`), TypeScript type checking (`tsc` or a `typecheck` script), build (`npm run build` when configured), linting (`npm run lint` when configured), and tests (`npm test` when configured, with special handling for React test runners). Dependencies are installed in an isolated `node_modules` with a shared npm cache.

### Other Languages (not yet supported for testing)

The AI can *design and generate* code in any language — Go, Rust, Java, C#, etc. — because the design and implementation stages are language-agnostic (they work from the contract, not from language-specific tooling). However, the **test stage will skip automated checks** for unrecognized project types, which means the verification stage has less evidence to work with. The contract compliance checker (which validates file lists, size budgets, and data type definitions) still runs regardless of language.

**What it would take to add a new language:** Each new ecosystem needs three things — a project-type detector (e.g., "if `go.mod` exists → Go project"), a dependency installer, and a set of check commands (build, lint, test). In the current codebase, that's roughly 30–50 lines in `engine/evidence.py` (auto-detection) and `engine/sandbox.py` (environment setup). The architecture is designed for this — `auto_detect_checks` is a straightforward if/elif chain, and new branches follow the same pattern as the existing Python and Node.js ones.

---

## Architecture

The engine has two phases: human-driven intake (you describe the project) and machine-driven execution (the pipeline builds it).

```
┌──────────────────────────┐
│ Intake Layer             │  ← Phase 0: human-driven, blocking
│ (CLI or web dashboard)   │     You describe what to build
└────────────┬─────────────┘
             │ validated project spec
┌────────────▼─────────────┐
│ Normalized Project Spec  │  ← The "contract" between you and the engine
│ (state/inputs/)          │     Machine-readable, no ambiguity
└────────────┬─────────────┘
             │ read-only from here on
┌────────────▼─────────────┐
│ Autonomous Engine        │  ← Phase 1: machine-driven pipeline
│ (Prefect flow)           │     Config says how to run (model, budget, gates)
└──────────────────────────┘
```

### Pipeline Stages

```
[intake]    ──→ Project spec + requirements          (human provides input)
                    ↓
[bootstrap] ──→ Validate inputs, start audit log     (sanity check)
                    ↓
[design]    ──→ Architecture + design contract        (AI designs the system)
                │   May pause for human approval
                ↓
[implement] ──→ Generated code, chunk by chunk        (AI writes code to contract)
                │   Each chunk is checked against the design
                ↓
[extract]   ──→ Standalone project folder             (code is written to disk)
                │   Safety limits on file count and size
                ↓
[test]      ──→ Automated quality checks              (syntax, imports, lint, types)
                │   Plus contract compliance verification
                ↓
[verify]    ──→ Go/no-go recommendation               (AI or rule-based verdict)
```

The **extract** step parses the AI's output for code blocks, then writes each file to a project folder. A safety cutoff (called a "circuit breaker") halts extraction if the output exceeds size limits — 80 files / 750 KB for MVP tier, 250 files / 5 MB for Premium.

---

## Core Principles

1. **If it isn't written down, it doesn't exist** — pipeline stages communicate through files, not in-memory data. Everything is inspectable.
2. **Contracts, not interpretation** — the AI receives a structured JSON contract (exact file lists, data type definitions, dependency maps) instead of vague prose instructions. This is how the engine prevents drift.
3. **Gates are policy, not behavior** — when the pipeline encounters a decision point, what happens (pause for human, auto-approve, or skip) is controlled by a policy file, not hard-coded.
4. **Structured state** — all pipeline artifacts live in organized subfolders, not a flat directory. Any auditor can navigate the run history.
5. **Tamper-evident traceability** — every step is logged with HMAC-SHA256 authentication (a cryptographic method that detects any after-the-fact modification to the log). See [Security Model](#security-model) for details.
6. **Spec says what, config says how** — the project spec captures *what to build*; `config.yml` captures *how to run the engine* (which AI model, budget limits, gate policies).

---

## Contract System

The contract system is the engine's primary defense against **AI interpretation drift** — the tendency for an AI to forget or reinterpret decisions made earlier in the pipeline. This is the root cause of most cross-file inconsistencies (wrong field names, missing files, imports that reference things that don't exist).

### Design Contract

The design stage produces a `DESIGN_CONTRACT.json` alongside a human-readable `ARCHITECTURE.md`. The contract is a structured JSON file that specifies exactly what the implementation stage must produce:

- **Components** — each with an exact file list, dependency declarations, and a file budget (max number of files)
- **Canonical types** — shared data structures (think: "a User always has these fields, defined in this file"). Every implementation chunk receives these definitions verbatim so the AI can't invent its own versions.
- **Tech decisions** — locked-in choices with rationale, so the AI doesn't second-guess them mid-build
- **Import maps** — which component is allowed to depend on which

The contract is validated at creation time with 15+ automated checks (duplicate detection, phantom dependency references, budget overflows, type reference validity).

### Contract Compliance Checker

After the AI writes code and it's extracted to disk, the compliance checker verifies the output against the design contract:

- **Missing files** — the contract says file X should exist, but it wasn't produced
- **Extra files** — files were produced that aren't in any component's plan
- **Budget violations** — a component produced more files than allowed
- **Type integrity** — the canonical types appear in the correct files with the expected fields

Results are saved as structured evidence records that feed into testing and verification.

---

## Tier System

Before launching a build, you choose a tier that controls the AI's output budget and cost:

- **Premium** — full output budget, no scope restrictions, higher AI cost. Best for production-quality output with thorough documentation.
- **MVP (Minimum Viable)** — hard limits enforced at every level. Best for quick prototyping or cost-conscious builds.
  - Design: max 5 components, 40 files
  - Per-chunk implementation: max 10 files
  - Extraction safety cutoff: 80 files / 750 KB
  - Dashboard shows estimated savings vs. Premium before you commit

---

## Test & Verification

### Auto-Detect Checks

The test stage inspects the extracted project and automatically selects appropriate quality checks based on the project type:

**Node.js / TypeScript projects** (detected by `package.json`): dependency install, type checking, build, lint, tests.

**Python projects** (detected by `pyproject.toml` or `requirements.txt`): dependency install, syntax validation, import resolution, lint (ruff), type checking (mypy), unit tests (pytest). Structural checks are always mandatory.

### Structural Issue Analysis

The verification stage classifies failures into categories — type errors, import errors, lint errors, build errors, test failures, and contract compliance issues. Each category gets specific diagnostic output. When the AI-powered verification path is used, it receives this classification so it can provide targeted root-cause analysis instead of a generic summary.

### Evidence Records

Every check produces a structured JSON evidence record containing the command that ran, its exit code, full output, timestamps, and environment metadata. Think of these as the "test receipts" — they feed into the dashboard's evidence viewer and the verification stage's analysis.

---

## Decision Gates

At critical moments during the pipeline, a **decision gate** can pause execution and require human input. Gate behavior is controlled per-stage via a policy file (`DECISION_GATES.yml`):

- **pause** — stop and wait for a human to approve or redirect (default for the design stage)
- **auto** — automatically select the configured default option
- **skip** — proceed without stopping (default for implement, test, verify)

Gates trigger on architectural tradeoffs, test failures, and verification rejection — not on missing requirements (those are caught at intake before the pipeline starts).

---

## Dashboard

A web-based dashboard (built with Streamlit) provides a visual interface for the full pipeline lifecycle.

**Install and launch:**
```bash
pip install -e ".[dashboard]"
streamlit run dashboard/app.py
```

Or point it at a specific project:
```bash
AUTONOMY_ENGINE_PROJECT_DIR=/path/to/project streamlit run dashboard/app.py
```

**Navigation** is organized into two groups:

*Main:*
- **Dashboard** — pipeline status with real pass/fail indicators, recent runs, cache statistics
- **Pipeline Explorer** — interactive visual map of the pipeline stages, inputs, outputs, and why each step matters (educational, no run data required)
- **Create Project** — form-based intake with project management (load previous specs, view run history)
- **Run Pipeline** — tier selection with cost estimates, live progress with trace timeline

*Security & Ops:*
- **Run Outputs** — every artifact from a pipeline run, organized by stage with clickable file viewers (select a run, click any output to read it inline)
- **Inspector** — detailed trace timeline, evidence records, decisions, artifacts, and config snapshot per run
- **Audit Trail** — visual hash chain with integrity verification and export to file
- **Configuration** — active AI model settings, gate policies, sandbox config, approved check commands
- **Benchmarks** — per-stage timing, cache hit rates, actual vs. projected token usage

Pipeline status indicators reflect real evidence: green only when the stage ran and all checks passed, red when checks failed, amber when in progress, gray when pending. The dashboard never shows a false "success."

---

## Security Model

This section documents what the engine protects against and — just as importantly — what it doesn't. Honest threat boundaries are more useful than vague claims.

### Audit Log Integrity (HMAC-SHA256)

**What it is:** Every pipeline run generates a unique cryptographic key. Each log entry is signed with that key using HMAC-SHA256 (a standard method for creating a tamper-evident signature). Entries are also chained — each one references the signature of the previous entry — so inserting, deleting, or reordering entries breaks the chain.

**What this protects against:** If someone edits the audit log after the fact (for example, changing a "test failed" result to "test passed"), the signatures won't match and verification will flag the tampering. Unlike a plain hash chain (which an attacker could recompute from scratch), the HMAC approach requires the secret key — so modifying and re-signing the log isn't possible without it.

**What this does NOT protect against:** An attacker with access to both the log file and the key file on disk can forge valid entries. In a production environment, the key should be stored in an external key management system (KMS or HSM). The current design is appropriate for development-time integrity verification, not adversarial forensics.

### Workspace Isolation

**What it is:** The test stage runs AI-generated code in a temporary directory with its own isolated environment. Dependencies are installed from the project's requirements and cached for reuse.

**What this provides:** File isolation (generated code can't overwrite engine files), dependency isolation (project packages don't pollute the host system), and automatic cleanup.

**What this does NOT provide:** Operating-system-level sandboxing. The generated code runs as the same user with full network and file access. There are no containers, no system-call filtering, and no network restrictions. For running untrusted AI output in a higher-security context, wrap execution in Docker or a similar container. The engine assumes a supervised workflow where a human reviews generated code before deployment.

### Path Traversal Protection

The extract stage validates every output file path to prevent directory escape attacks (e.g., `../../etc/passwd`). Absolute paths, parent traversal (`..`), and empty path segments are all rejected.

### Content Validation

Extracted Python files are validated for correct syntax, resolvable imports, and lint compliance. The contract compliance checker verifies that the output matches the design contract's file list, size budgets, and data type definitions.

**Known limitation:** The type checker uses text matching (checking that type and field names appear in the file), not full code structure analysis. A future improvement would parse actual class definitions for exact matching.

### API Key Handling

API keys are loaded from a `.env` file (which is excluded from version control) and never appear in audit logs, prompts, or output. A pre-commit hook rejects any attempt to commit files containing key patterns.

---

## Quality Assurance

Three independent tools provide ongoing due-diligence on the engine's own codebase (not the AI-generated projects — the engine tests those during every pipeline run).

### Test Coverage (pytest-cov)

The engine has 265+ automated tests covering the core modules. Coverage of the `engine/` package is **61%** on the testable surface. Modules with 0% coverage (cost_estimator, usage_tracker, notifier, decision_gates) depend on the Prefect runtime, which is only available when running the full pipeline — they are fully exercised during real pipeline runs but can't be unit-tested in isolation.

High-coverage modules (90%+): cache, contract_checker, design_contract, evidence, sandbox, spec_normalizer, tier_context, tracer.

### Security Scan (bandit)

Bandit (Python SAST — Static Application Security Testing) scans the engine for common security anti-patterns. Current results across 2,834 lines of code:

**1 HIGH (known, intentional):** `subprocess call with shell=True` in `engine/evidence.py`. This is the test runner — it executes pre-approved commands from `config.yml` in a sandboxed workspace. The commands are never AI-generated; the AI only produces code, not shell commands. Annotated with `# nosec B602` and documented in the Security Model section.

**8 LOW (informational):** All are `subprocess` import/call notices in `engine/sandbox.py` and `engine/evidence.py`. These are core to the engine's job (running tests in isolated environments) and use controlled, non-user-supplied arguments.

**0 MEDIUM, 0 unexpected findings.**

### Dependency Audit (pip-audit)

pip-audit checks all installed packages against the Python Advisory Database for known vulnerabilities. Current results for project dependencies: **0 vulnerabilities found.**

System-level packages in the development environment (tornado 6.1, twisted 22.1.0, wheel 0.37.1) have known CVEs but are not project dependencies — they ship with the OS and don't affect the engine.

---

## Audit Reports

After a run completes, export a self-contained audit bundle:

```bash
python -m engine.report --run-id <id> [--out path] [--project-dir dir]
```

This produces a compressed archive containing the full trace, config snapshot, evidence records, decisions, a rebuilt artifact manifest, and an integrity verification result. Useful for compliance reviews or sharing results with stakeholders who don't have access to the dashboard.

---

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
2. Add your API keys to `.env` (Claude and/or OpenAI)
3. Enable the pre-commit hook (prevents accidental key commits):
   ```bash
   git config core.hooksPath .githooks
   ```

**Security:** The `.env` file is excluded from version control. The pre-commit hook rejects any commit containing API key patterns. Never share `.env` — use `.env.example` as the template.

## Usage

### Step 1: Intake (required)

Describe what you want to build — interactively or from a YAML file:

```bash
# Interactive intake (guided prompts)
python -m intake.intake new-project

# From an existing spec file
python -m intake.intake from-file path/to/project_spec.yml

# Using a separate project directory
python -m intake.intake --project-dir ~/projects/solo1 new-project

# Edit an existing spec
python -m intake.intake edit

# Validate a spec without running the pipeline
python -m intake.intake validate path/to/project_spec.yml
```

Or use the **Create Project** page in the dashboard for a form-based experience with auto-saving fields and one-click pipeline launch.

### Step 2: Run the engine

```bash
# Start Prefect server (separate terminal)
prefect server start

# Run the pipeline
python flows/autonomous_flow.py

# Run against an external project directory
python flows/autonomous_flow.py --project-dir ~/projects/solo1
```

The engine will refuse to start if intake has not been completed. The Prefect UI is available at `http://localhost:4200` for monitoring and gate approvals.

Or use the **Run Pipeline** page in the dashboard for tier selection, cost estimates, and live monitoring.

### Output

After a successful run, the generated project files appear in a sibling directory:

```
~/Desktop/
├── autonomy_engine/      # this engine
└── my-project/           # the generated project
    ├── app.py
    ├── requirements.txt
    ├── models/
    │   └── ...
    └── ...
```

A manifest of all extracted files is saved to `state/build/MANIFEST.md`.

---

## Configuration

### config.yml — Runtime Settings

Controls how the engine runs (which AI model, token budgets, gate behavior, sandbox settings):

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
  enabled: false           # log-only; replace engine/notifier.py for Slack/email

sandbox:
  enabled: true            # run tests in isolated workspace (see Security Model)
  install_deps: true

verify:
  mode: always_llm         # always_llm | auto | never_llm
  llm_on_fail_summary: true
  llm_on_pass_summary: true

checks: []                 # approved test commands (see config.yml for examples)
```

### DECISION_GATES.yml — Gate Policies

Controls what happens at each decision point:

```yaml
gates:
  design:
    policy: "pause"        # stop for human approval
  test:
    policy: "skip"         # auto-continue
    default_option: "continue"
  verify:
    policy: "skip"         # auto-continue
    default_option: "accept"
```

---

## Efficiency Features

### Per-Stage Model Selection

Each pipeline stage can use a different AI model. Use a cheaper/faster model for verification and a more capable one for implementation. Configure in `config.yml` under `llm.models`.

### AI Response Caching

Responses are cached based on the exact inputs (prompt, model, parameters). If you re-run the pipeline without changing inputs, cached responses are reused — no additional API cost. Cache entries are immutable (first response wins, never overwritten).

### Selective Verify

The verification stage supports three modes to balance thoroughness against cost:

- **`always_llm`** — always use the AI for verification analysis (default)
- **`auto`** — skip the AI when test results make the outcome obvious (all passed or all failed)
- **`never_llm`** — purely rule-based verification with structured issue breakdown (zero AI cost)

### Sandbox Caching

Test environments (Python virtualenvs) are cached and reused across runs when dependencies haven't changed. A shared package cache further speeds up setup.

### Benchmarking

```bash
python bench/benchmark_runs.py --runs 3 --project-dir ~/projects/myapp
python bench/compare_results.py bench/results_old.json bench/results_new.json
```

See `bench/README.md` for full documentation.

---

## Project Structure

```
intake/               Project intake — how you describe what to build
  schema.py           Project spec definition and validation
  renderer.py         Generates structured artifacts from the validated spec
  intake.py           CLI entry point (new-project, from-file, edit, validate)

engine/               Core engine modules
  context.py          Path resolution — figures out where project files live
  decision_gates.py   Gate policies (pause/auto/skip) loaded from config
  llm_provider.py     AI model interface — Claude and OpenAI behind one API
  tracer.py           Tamper-evident audit log (HMAC-SHA256 hash chain)
  evidence.py         Test runner — auto-detects checks, captures structured results
  report.py           Audit bundle exporter (compressed archive with full run data)
  notifier.py         Notification stub (swap in Slack/email/PagerDuty)
  design_contract.py  Design contract schema, validation (15+ checks), extraction
  contract_checker.py Post-build compliance check — did the AI follow the contract?
  spec_normalizer.py  Normalizes user input, flags ambiguity, structures for design
  tier_context.py     Injects tier-appropriate scope guidance into AI prompts
  cost_estimator.py   Pre-run token and cost estimation for tier selection
  usage_tracker.py    Post-run actual vs. projected token usage comparison
  cache.py            Deterministic AI response caching

flows/                Prefect flow definition — the main pipeline entry point
tasks/                Individual pipeline stages (one file per stage)
  bootstrap.py        Input validation and audit log initialization
  design.py           Architecture and design contract generation (AI-powered)
  implement.py        Contract-driven code generation, chunk by chunk (AI-powered)
  extract.py          Code extraction with safety limits (no AI — pure parsing)
  test.py             Automated quality checks + contract compliance
  verify.py           Go/no-go analysis — AI-powered or rule-based

templates/            Gate policies and AI prompt templates
  prompts/            The actual prompts sent to the AI (tracked by hash in audit log)
    design.txt        Architecture prompt — includes contract JSON requirements
    implement.txt     Single-call implementation prompt
    implement_chunk.txt  Per-chunk prompt with canonical type injection
    verify.txt        Verification prompt with structural analysis format

dashboard/            Web dashboard (Streamlit)
  app.py              Entry point, navigation, page routing
  theme.py            Centralized dark-mode styling
  data_loader.py      Reads pipeline data from disk (no engine imports)
  pipeline_runner.py  Launches pipeline as a subprocess
  pages/              Page modules (home, create_project, run_pipeline, etc.)
  components/         Reusable UI components (pipeline visual, trace timeline, etc.)

state/                Runtime artifacts (excluded from version control)
  runs/<id>/          Per-run audit log, evidence records, and decisions
  inputs/             Project spec and rendered intake artifacts
  designs/            Architecture documents + design contract
  implementations/    AI-generated code
  tests/              Test and verification results
  build/              Extraction manifest
  cache/llm/          Cached AI responses
  sandbox_cache/      Cached test environments

tests/                Automated test suite (265 tests, 61% engine coverage)
bench/                Performance benchmarking tools
```
