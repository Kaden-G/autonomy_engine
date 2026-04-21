# Autonomy Engine v2.0

![CI](https://github.com/Kaden-G/autonomy-engine/actions/workflows/ci.yml/badge.svg)
![Tests](https://img.shields.io/badge/tests-569%20passing-brightgreen)
![Coverage](https://img.shields.io/badge/coverage-61%25%20engine-blue)
![Security](https://img.shields.io/badge/bandit-1%20known%20%7C%200%20unexpected-yellow)
![Deps](https://img.shields.io/badge/pip--audit-0%20project%20vulns-brightgreen)
![Deps Pinned](https://img.shields.io/badge/deps-pinned%20%28lock%20file%29-blue)
![Docker](https://img.shields.io/badge/docker-compose%20up-blue)
![Orchestration](https://img.shields.io/badge/orchestration-LangGraph-purple)

An autonomous software build pipeline that turns a project description into working, tested code — with human approval gates, tamper-evident audit trails, and strict quality contracts that keep AI-generated output on-spec.

Built on [LangGraph](https://langchain-ai.github.io/langgraph/) (stateful graph orchestration) and compatible with Claude and OpenAI as the underlying AI models.

---

## Live Demo

> **[Try the Autonomy Engine without an API key](https://kaden-g-solo.streamlit.app)**

The hosted demo runs off a trimmed mirror of this repo (`kaden-g/solo`) and is limited to 3 pipeline runs per session. For unlimited access, clone this repo — see [Quickstart](#quickstart-60-seconds) below.

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
- Fully unsupervised production deployments without monitoring — the engine supports unattended runs but benefits from log aggregation and alerting
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
│ (LangGraph StateGraph)   │     Config says how to run (model, budget, gates)
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

## Orchestration: LangGraph (v2.0)

Version 2.0 migrates the pipeline orchestration from Prefect to [LangGraph](https://langchain-ai.github.io/langgraph/), a stateful graph framework for building agent workflows. The engine modules, state directory structure, audit trail, and dashboard are unchanged — only the orchestration layer was replaced.

### Why LangGraph Over Prefect

The original Prefect-based flow was a linear sequence of `@task`-decorated functions with `pause_flow_run()` for human-in-the-loop gates. It worked, but had three pain points that a graph-based orchestrator solves naturally:

**Checkpoint-based resume.** If the pipeline fails at the test stage, Prefect re-runs from stage 1 — re-calling the LLM for design and implementation, burning $1-8 in API costs per re-run. LangGraph checkpoints state at every node boundary, so a failed run resumes from the last successful stage. For a pipeline that makes expensive LLM calls, this is the performance metric that matters most.

**Graph-native retry loops.** When tests fail, the ideal behavior is "re-implement with the test failures as context, then re-test." In a linear flow, that requires custom retry logic or manual re-runs. In a graph, it's a conditional edge from `test` back to `implement`, bounded by a configurable retry budget. The graph makes this control flow explicit and testable.

**Cleaner human-in-the-loop.** Prefect's `pause_flow_run()` requires the Prefect UI/API for decision input. LangGraph's `interrupt()` pauses the graph, serializes state to the checkpoint, and resumes with the decision injected via `Command(resume=...)`. No external UI dependency — the decision can come from a CLI, API, or dashboard.

### Pipeline Graph

```
                         ┌─────────────────────────────────────────────────────────────┐
                         │                   LangGraph StateGraph                      │
                         │                                                             │
                         │  ┌──────┐   ┌───────────┐   ┌────────┐   ┌───────────┐     │
                         │  │      │   │           │   │        │   │           │     │
                    ┌────┼──► init ├───► bootstrap ├───► design ├───► implement ├──┐  │
                    │    │  │      │   │           │   │   ⏸    │   │           │  │  │
                    │    │  └──────┘   └───────────┘   └────────┘   └───────────┘  │  │
                    │    │                                                          │  │
  invoke()          │    │  ┌──────────┐   ┌────────┐   ┌─────┐   ┌─────────┐     │  │
  ─────────────────►│    │  │          │   │        │   │     │   │         │     │  │
                    │    │  │ complete ◄───► verify ◄───► test◄───► extract ◄─────┘  │
                    │    │  │          │   │   ⏸    │   │  ⏸  │   │         │        │
                    │    │  └────┬─────┘   └────────┘   └──┬──┘   └─────────┘        │
                    │    │       │                          │                          │
                    │    │       ▼                     ┌────┴──────┐                   │
                    │    │      END                    │  retry →  │                   │
                    │    │                             │ implement │                   │
                    │    │                             └───────────┘                   │
                    │    └─────────────────────────────────────────────────────────────┘
                    │
                    │    ⏸ = interrupt() — pauses for human decision
                    │    Conditional edges check stage results and route accordingly
                    │    Any stage failure short-circuits to END
                    │    Retry loop bounded by max_retries (default: 1)
```

### What Changed vs. What Stayed

| Layer | v1.4 (Prefect) | v2.0 (LangGraph) |
|-------|----------------|-------------------|
| Orchestration | `@flow` / `@task` decorators, linear sequence | `StateGraph` with conditional edges and retry loops |
| Human-in-the-loop | `pause_flow_run()` via Prefect UI | `interrupt()` with checkpoint-based resume |
| State persistence | Re-run from scratch on failure | Checkpoint at every node, resume from last success |
| Retry on test failure | Manual re-run or custom logic | Graph edge: `test → implement` (configurable budget) |
| Engine modules | `engine/*.py` | **Unchanged** — orchestration-agnostic |
| State directory | `state/` on disk | **Unchanged** — same structure, same files |
| Audit trail | HMAC-SHA256 chain in `trace.jsonl` | **Unchanged** — same integrity guarantees |
| Dashboard | Streamlit reading from `state/` | **Unchanged** — reads same artifacts |
| LLM providers | `engine/llm_provider.py` (Claude + OpenAI) | **Unchanged** — no LangChain wrappers added |

The migration was designed as an adapter layer: `graph/nodes.py` wraps the existing `tasks/*.py` functions, translating between LangGraph's state-passing model and the tasks' file-based I/O. The tasks don't know LangGraph exists. This means the 522 existing engine tests pass without modification.

### Running with LangGraph

```bash
# New entry point (LangGraph orchestration)
python graph/pipeline.py

# With checkpoint persistence (resume on failure)
python graph/pipeline.py --checkpoint-db state/checkpoints.sqlite

# Resume an interrupted run
python graph/pipeline.py --thread-id <thread-id> --checkpoint-db state/checkpoints.sqlite

# Legacy entry point (retires 2026-05-21 — see Retired features below)
# Requires: pip install autonomy-engine[prefect]
python flows/autonomous_flow.py
```

---

## Core Principles

1. **If it isn't written down, it doesn't exist** — pipeline stages communicate through files, not in-memory data. Everything is inspectable.
2. **Contracts, not interpretation** — the AI receives a structured JSON contract (exact file lists, data type definitions, dependency maps) instead of vague prose instructions. This is how the engine prevents drift.
3. **Gates are policy, not behavior** — when the pipeline encounters a decision point, what happens (pause for human, auto-approve, or skip) is controlled by a policy file, not hard-coded.
4. **Structured state** — all pipeline artifacts live in organized subfolders, not a flat directory. Any auditor can navigate the run history.
5. **Tamper-evident traceability** — every step is logged with HMAC-SHA256 authentication (a cryptographic method that detects any after-the-fact modification to the log). See [Security Model](#security-model) for details.
6. **Spec says what, config says how** — the project spec captures *what to build*; `config.yml` captures *how to run the engine* (which AI model, budget limits, gate policies).

---

## Development Approach

This project was built with Claude as a development partner — architecture decisions, security model, code review, and implementation were all done in collaboration with AI. That's a deliberate choice, not an asterisk.

The engineering value of this project lives in the decisions: why HMAC-SHA256 over plain hash chains, why contracts instead of freeform prompts, why LangGraph's StateGraph over a hand-rolled state machine (and why it replaced Prefect in v2.0), where to draw the threat model boundary and document what's explicitly out of scope. Those decisions are mine. The ability to execute on them efficiently using AI tooling is the skill, not the shortcut.

This is also a project *about* AI-supervised pipelines — building it with AI-assisted development is practicing what it preaches.

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
- **MVP (Minimum Viable)** — tighter scope guidance plus a hard extraction cutoff. Best for quick prototyping or cost-conscious builds.
  - Design: max 5 components, 40 files *(advisory — injected into the design prompt)*
  - Per-chunk implementation: max 10 files *(advisory — injected into the implement prompt; not enforced mid-run)*
  - Extraction safety cutoff: 80 files / 750 KB *(enforced — the extract stage circuit-breaks if exceeded)*
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

The Streamlit dashboard is the primary interface for the engine — most users never touch the CLI. It covers the entire lifecycle: describe a project, estimate costs, launch a build, watch it run, inspect every artifact, and verify the audit trail.

**Launch:**
```bash
# Docker (recommended)
docker compose up                  # → http://localhost:8501

# Local
streamlit run dashboard/app.py     # → http://localhost:8501

# Point at a different project directory
AUTONOMY_ENGINE_PROJECT_DIR=/path/to/project streamlit run dashboard/app.py
```

The sidebar organizes pages into two groups.

### Main Pages

**Dashboard** — The landing page. Shows pipeline status with real pass/fail indicators (green = all checks passed, red = failures, amber = in progress, gray = pending). Also displays recent run history and cache hit statistics. Status indicators are evidence-backed — the dashboard never shows a false "success."

**Pipeline Explorer** — An interactive visual map of the six pipeline stages. Click any stage to see its inputs, outputs, and why it exists. This page is educational and doesn't require run data — useful for onboarding or explaining the architecture to stakeholders.

**Create Project** — Form-based intake that replaces the CLI's `intake new-project`. Fill in project name, description, requirements, and tech stack. Features include loading previous specs, viewing run history per project, and auto-saving form fields. One-click button to launch the pipeline directly from here.

**Run Pipeline** — Tier selection (MVP vs. Premium) with pre-run cost estimates showing projected token usage and dollar cost. Once launched, displays live progress with a trace timeline that updates as each stage completes. Decision gates surface here when the pipeline pauses for human approval.

### Security & Ops Pages

**Run Outputs** — Browse every artifact from a pipeline run, organized by stage (design, implement, extract, test, verify). Select a run from the dropdown, then click any output file to read it inline — architecture docs, generated code, test results, verification reports.

**Inspector** — The deep-dive view. Shows the complete trace timeline, individual evidence records (with command, exit code, and full output), every decision gate interaction, all artifacts, and the config snapshot that was active during the run.

**Audit Trail** — Visual hash-chain viewer. Each trace entry shows its HMAC signature and link to the previous entry. Includes a one-click integrity verification button that walks the chain and flags any broken links. Export the full trail to a file for offline review or compliance handoff.

**Configuration** — Read-only view of the active engine configuration: AI model settings, gate policies, sandbox config, approved check commands, and cache TTLs. Useful for verifying what settings a run will use before launching.

**Benchmarks** — Per-stage timing breakdowns, cache hit rates across runs, and actual vs. projected token usage comparisons. Helps identify which stages are bottlenecks and whether cost estimates are calibrated.

---

## Security Model

**The threat model in one paragraph:** The Autonomy Engine treats AI-generated code as untrusted output in a supervised pipeline. Every pipeline action is recorded in an HMAC-SHA256 tamper-evident audit trail that detects after-the-fact log modification — providing chain-of-custody guarantees for autonomous code generation. The engine enforces workspace isolation (generated code cannot overwrite engine files), path traversal protection (no directory escape attacks), contract compliance verification (output must match the approved design), and API key hygiene (secrets never appear in logs or prompts). The explicit non-goal is OS-level sandboxing — the engine assumes a human reviews generated code before deployment, and recommends containerization for higher-threat environments.

This section documents what the engine protects against and — just as importantly — what it doesn't. Honest threat boundaries are more useful than vague claims.

### Audit Log Integrity (HMAC-SHA256)

**What it is:** Every pipeline run generates a unique cryptographic key. Each log entry is signed with that key using HMAC-SHA256 (a standard method for creating a tamper-evident signature). Entries are also chained — each one references the signature of the previous entry — so inserting, deleting, or reordering entries breaks the chain.

**What this protects against:** If someone edits the audit log after the fact (for example, changing a "test failed" result to "test passed"), the signatures won't match and verification will flag the tampering. Unlike a plain hash chain (which an attacker could recompute from scratch), the HMAC approach requires the secret key — so modifying and re-signing the log isn't possible without it.

**What this does NOT protect against:** An attacker with access to both the log file and the key file on disk can forge valid entries. In a production environment, the key should be stored in an external key management system (KMS or HSM). The current design is appropriate for development-time integrity verification, not adversarial forensics.

#### Audit trail verification

Verify a run's HMAC chain locally:

```bash
python -m engine.verify_trace --run-id <run-id>              # human output
python -m engine.verify_trace --run-id <run-id> --json       # CI-friendly
```

Exit codes: **0** chain valid · **1** tamper detected (HMAC mismatch, reorder, missing entry) · **2** verification impossible (missing `.trace_key`, missing `trace.jsonl`).

Example tamper output:

```text
[INVALID] run=20260421-abc entries=12
  failure at seq 3: HMAC mismatch — entry has been modified
```

CI enforces this as a property, not a claim. The [`trace-integrity`](.github/workflows/ci.yml) job runs `tests/test_trace_tampering_integration.py` on every PR — 7 named tests covering HMAC flipping, reorder, deletion, truncation-as-not-tamper, missing-key exit 2 distinct from tamper exit 1, and JSON-output parseability. If the CI checkout contains any `state/runs/*/trace.jsonl`, the job also verifies each of those real runs end-to-end. See [`tests/test_trace_tampering_integration.py`](tests/test_trace_tampering_integration.py) for the live-demo tests.

Framework mapping: **OWASP ASVS V7.1** (Log Tamper Protection) · **NIST AI RMF MEASURE 2.7** (Traceability).

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

**Streamlit Cloud / multi-tenant caveat.** When the dashboard runs on Streamlit Cloud, `dashboard/secrets_bridge.py` copies keys from `st.secrets` into `os.environ` so the pipeline subprocess inherits them. This is safe on Streamlit Cloud because every visitor gets their own container, but session-scoped env vars are visible to all subprocesses spawned by that instance. If you self-host in a shared multi-tenant setup, swap this bridge for a real secrets manager (AWS Secrets Manager, Vault, etc.).

### Sharing the project safely

When sharing this project as a zip (e.g., for code review), use:

```bash
make share-zip
```

This target produces `autonomy_engine_<date>_<time>.zip` with a curated exclusion list — `.env` files, `state/` run logs, key material (`*.key`, `*.pem`, `.trace_key`), virtual envs, caches, and OS metadata are all left out. The exclusions live in [`.zipignore`](.zipignore) as the single source of truth.

Before zipping, the target runs a secret-scan over both the staged file set and high-risk-named files in the working tree (`.env`, `.env.*`, `*.key`, `*.pem`, `.trace_key`). If any file matches a credential pattern (`sk-ant-`, `sk-proj-`, `AKIA…`, `ghp_…`, `xoxb-…`, `-----BEGIN`), **the target refuses to build the zip** and names the offending file. Files ending in `.example`, `.template`, or `.sample` are skipped (they contain illustrative placeholders by design).

After a successful build, the target prints the file size, file count, and full contents listing so you can review before sharing.

> **Limitation:** This protects the `make share-zip` flow only. A developer can still produce a zip via the file manager or `zip -r` directly. A pre-commit hook (`.githooks/pre-commit`) provides the analogous protection on the commit flow.

---

## Quality Assurance

Three independent tools provide ongoing due-diligence on the engine's own codebase (not the AI-generated projects — the engine tests those during every pipeline run).

### Test Coverage (pytest-cov)

The engine has 536 automated tests covering the core modules. Coverage of the `engine/` package is **61%** on the testable surface. Modules with 0% coverage (cost_estimator, usage_tracker, notifier, decision_gates) depend on the Prefect runtime, which is only available when running the full pipeline — they are fully exercised during real pipeline runs but can't be unit-tested in isolation.

High-coverage modules (90%+): cache, contract_checker, design_contract, evidence, extraction, model_registry, sandbox, spec_normalizer, tier_context, tracer.

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

## Quickstart (60 Seconds)

### Option A: Docker (recommended — zero local setup)

```bash
git clone https://github.com/Kaden-G/autonomy-engine.git
cd autonomy-engine
cp .env.example .env               # add your ANTHROPIC_API_KEY
docker compose up                  # → http://localhost:8501
```

That's it. The dashboard is running. Create a project, pick a tier, launch a build.

### Option B: Local install

```bash
git clone https://github.com/Kaden-G/autonomy-engine.git
cd autonomy-engine
pip install -r requirements.lock   # pinned production deps
pip install -e ".[dashboard]"      # adds Streamlit + Plotly
cp .env.example .env               # add your ANTHROPIC_API_KEY
streamlit run dashboard/app.py     # → http://localhost:8501
```

### Option C: CLI only (no dashboard)

```bash
pip install -r requirements.lock
pip install -e .
cp .env.example .env               # add your ANTHROPIC_API_KEY
python -m intake.intake new-project
python graph/pipeline.py           # LangGraph orchestration (v2.0)
```

## Setup (Development)

```bash
pip install -r requirements.lock    # production: pinned versions
pip install -e ".[dev,dashboard]"   # editable with dev tools + dashboard
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

Primary (v2.0 — LangGraph):

```bash
# Run the pipeline
python graph/pipeline.py

# Run against an external project directory
python graph/pipeline.py --project-dir ~/projects/solo1

# With checkpoint persistence (resumable across restarts)
python graph/pipeline.py --checkpoint-db state/checkpoints.sqlite
```

The engine will refuse to start if intake has not been completed. Gate approvals surface inline via LangGraph's `interrupt()` (no external UI required) — or use the **Run Pipeline** page in the dashboard for tier selection, cost estimates, and live monitoring.

<details>
<summary><b>Legacy (v1.x — Prefect)</b>: still works if you install with <code>pip install "autonomy-engine[prefect]"</code>. <b>Retires 2026-05-21.</b></summary>

```bash
# Start Prefect server (separate terminal)
prefect server start

# Run the pipeline
python flows/autonomous_flow.py

# Run against an external project directory
python flows/autonomous_flow.py --project-dir ~/projects/solo1
```

The Prefect UI is available at `http://localhost:4200` for monitoring and gate approvals. New projects should use `graph/pipeline.py`. The Prefect entry point is kept until 2026-05-21 for backward compatibility — see [Retired features](#retired-features) and [docs/prefect-sunset-audit.md](docs/prefect-sunset-audit.md) for the full retirement plan.

</details>

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

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `AE_ENV` | *(unset)* | Loads `config.<env>.yml` (e.g., `production` → `config.production.yml`) |
| `AE_CONFIG_PATH` | *(unset)* | Explicit config file path (overrides `AE_ENV`) |
| `AE_LOG_FORMAT` | `text` | Log output format: `text` (human-readable) or `json` (structured) |
| `AE_LOG_LEVEL` | `INFO` | Root log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

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
  mode: auto               # always_llm | auto (default) | never_llm
  llm_on_fail_summary: true
  llm_on_pass_summary: true

cache:
  llm_ttl_days: 30         # delete LLM cache entries older than 30 days (0 = disable)
  venv_ttl_days: 7          # delete sandbox venvs older than 7 days (0 = disable)

checks: []                 # approved test commands (see config.yml for examples)
```

### models.yml — Model Registry

Defines output-token limits and per-million-token pricing for all supported models. Both `llm_provider.py` and `cost_estimator.py` load from this file. Adding a new model requires only a YAML edit:

```yaml
models:
  claude-sonnet-4:
    provider: claude
    max_output_tokens: 64000
    pricing:
      input: 3.00
      output: 15.00
  gpt-4o:
    provider: openai
    max_output_tokens: 16384
    pricing:
      input: 2.50
      output: 10.00

defaults:
  max_output_tokens: 4096
  pricing:
    input: 0.0
    output: 0.0
```

Prefix matching is supported — `claude-sonnet-4` matches `claude-sonnet-4-20250514`. A project-local `models.yml` overrides the engine default, enabling per-project model customization.

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

- **`always_llm`** — always use the AI for verification analysis (original v1.x behavior)
- **`auto`** — skip the AI when test results make the outcome obvious, i.e. all passed or all failed (default)
- **`never_llm`** — purely rule-based verification with structured issue breakdown (zero AI cost)

### Sandbox Caching

Test environments (Python virtualenvs) are cached and reused across runs when dependencies haven't changed. A shared package cache further speeds up setup.

### Cache Eviction

Both the LLM response cache and sandbox virtualenv cache are automatically cleaned at the start of each pipeline run. Entries older than the configured TTL are deleted — 30 days for LLM responses, 7 days for virtualenvs by default. TTLs are configurable in `config.yml` under the `cache` section. Set a TTL to `0` to disable eviction for that cache type.

### Benchmarking

```bash
python bench/benchmark_runs.py --runs 3 --project-dir ~/projects/myapp
python bench/compare_results.py bench/results_old.json bench/results_new.json
```

See `bench/README.md` for full documentation.

---

## Resilience

### Retry Logic (Exponential Backoff)

All LLM API calls are wrapped with automatic retry on transient failures — rate limits (429), server errors (5xx), timeouts, and connection drops. The engine retries up to 3 times with exponential backoff (2s → 4s → 8s). Non-transient errors (authentication, validation) fail fast. Retry parameters are configurable per-provider in `config.yml` under `llm.retry.max_retries` and `llm.retry.backoff_base`.

### Thread Safety

Module-level state (run IDs, hash chains, project context, tier selection, token overrides) uses `threading.local()` for isolation. This means multiple pipeline runs can execute concurrently in the same process without corrupting each other's audit trails or context. A module proxy pattern (`sys.modules` replacement) preserves backward compatibility with direct attribute access in tests.

### Robust Contract Extraction

Contract and manifest extraction uses parse-based scanning instead of regex. JSON blocks are found by scanning all fenced code blocks and running `json.loads` on each — no regex for the JSON itself. TypeScript interfaces and enums are extracted with a brace-counting state machine that correctly handles nested types like `{ database: { host: string } }`. Python class extraction captures the full body (fields and methods) using indentation tracking. The old regex patterns silently truncated nested structures; the new approach handles arbitrary nesting depth.

---

## Operations

### Environment-Specific Configuration

The engine supports per-environment config files for dev/staging/production deployments:

```bash
# Use an environment-specific config
AE_ENV=production python graph/pipeline.py
# → loads config.production.yml

# Or specify an explicit config path
AE_CONFIG_PATH=my-config.yml python graph/pipeline.py
```

Resolution order: `AE_CONFIG_PATH` (explicit path) → `AE_ENV` (loads `config.<env>.yml`) → `config.yml` (default). This is backward compatible — unset both variables and the engine behaves exactly as before.

### Model Registry (models.yml)

Model output-token limits and per-million-token pricing are defined in a single `models.yml` file — not hardcoded in Python. Adding a new model is a one-line YAML edit with no code changes. Both `llm_provider.py` (for token limits) and `cost_estimator.py` (for pricing) load from this shared registry. Prefix matching is supported: `claude-sonnet-4` matches dated variants like `claude-sonnet-4-20250514`.

### Structured Logging

Two output modes controlled by `AE_LOG_FORMAT`:

```bash
# Human-readable (default)
AE_LOG_FORMAT=text python graph/pipeline.py

# JSON-lines for log aggregation (Datadog, CloudWatch, ELK)
AE_LOG_FORMAT=json AE_LOG_LEVEL=DEBUG python graph/pipeline.py
```

JSON mode emits one self-contained JSON object per line with `timestamp`, `level`, `logger`, `message`, and any structured `extra` fields. Log level is controlled by `AE_LOG_LEVEL` (default: `INFO`).

### Graceful Shutdown

The pipeline installs SIGTERM and SIGINT handlers at startup. When a container orchestrator (ECS, Kubernetes) sends SIGTERM, the engine writes a `shutdown` trace entry to the audit log before exiting, keeping the HMAC chain valid. A second signal forces immediate exit. Exit codes follow Unix convention: `128 + signal number`.

### Dependency Pinning

All dependencies in `pyproject.toml` have upper bounds (e.g., `anthropic>=0.40,<1`) to prevent breaking changes from surprise major-version releases. Exact versions are locked in `requirements.lock` (production) and `requirements-dev.lock` (development), generated by `pip-compile`. For reproducible deployments:

```bash
pip install -r requirements.lock
```

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
  model_registry.py   Config-driven model limits and pricing (loads models.yml)
  extraction.py       Robust JSON/type extraction — brace-counting, not regex
  log_config.py       Centralized logging — text or JSON-lines output
  tracer.py           Tamper-evident audit log (HMAC-SHA256 hash chain)
  evidence.py         Test runner — auto-detects checks, captures structured results
  report.py           Audit bundle exporter (compressed archive with full run data)
  notifier.py         Notification stub — logs-only by default; intentional
                      extension point (swap in Slack/email/PagerDuty)
  design_contract.py  Design contract schema, validation (15+ checks), extraction
  contract_checker.py Post-build compliance check — did the AI follow the contract?
  spec_normalizer.py  Normalizes user input, flags ambiguity, structures for design
  tier_context.py     Injects tier-appropriate scope guidance into AI prompts
  cost_estimator.py   Pre-run token and cost estimation (pricing from models.yml)
  usage_tracker.py    Post-run actual vs. projected token usage comparison
  cache.py            Deterministic AI response caching with TTL eviction

graph/                LangGraph orchestration (v2.0 — primary entry point)
  pipeline.py         StateGraph, conditional edges, retry loop, checkpointing
  nodes.py            Thin adapters over tasks/*.py (one node per stage)
  state.py            PipelineState TypedDict — the shape of run state

flows/                Prefect flow definition (legacy v1.x — retires 2026-05-21)
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
  rate_limiter.py     Session-scoped run limiter for the hosted demo
  secrets_bridge.py   Copies API keys from st.secrets → os.environ at startup
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

models.yml            Model registry — output limits and pricing per model
requirements.lock     Pinned production dependencies (pip-compile output)
requirements-dev.lock Pinned dev dependencies (pip-compile output)
Dockerfile            Multi-stage container build (dashboard)
docker-compose.yml    One-command launch with volume mounts
.dockerignore         Keeps build context lean

.github/workflows/
  ci.yml              GitHub Actions: lint, test (3.10–3.12), import check, security

tests/                Automated test suite (569 tests, 61% engine coverage)
bench/                Performance benchmarking tools
specs/                Reference spec files for manual testing (not consumed by the pipeline)
```

---

## Retired features

**v1.x Prefect flows retire 2026-05-21.** Use `graph/pipeline.py` (LangGraph) going forward.

The `flows/autonomous_flow.py` entry point, the `@flow` decorator in `engine/compat.py`, and the `pause_flow_run` / `RunInput` / `require_decision` path in `engine/decision_gates.py` all sunset on 2026-05-21 (30 days from 2026-04-21). Until then, they remain callable if Prefect is installed (`pip install "autonomy-engine[prefect]"`), with banners on each deprecated symbol.

Tests that exercise only the Prefect flow (`tests/test_production_readiness.py`) are gated behind `RUN_DEPRECATED_TESTS=1` and skipped by default in CI.

Full retirement plan: [docs/prefect-sunset-audit.md](docs/prefect-sunset-audit.md).
