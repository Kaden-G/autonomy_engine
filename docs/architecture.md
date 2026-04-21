# Architecture

## Contents

- [Phase model](#phase-model)
- [Pipeline stages](#pipeline-stages)
- [Orchestration: LangGraph](#orchestration-langgraph)
- [Core principles](#core-principles)
- [Contract system](#contract-system)
- [Tier system](#tier-system)
- [Test & verification](#test--verification)
- [Decision gates](#decision-gates)
- [Project structure](#project-structure)

---

## Phase model

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

## Pipeline stages

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

## Orchestration: LangGraph

The pipeline runs on [LangGraph](https://langchain-ai.github.io/langgraph/) — a stateful graph framework. For the Prefect → LangGraph migration story (why, what changed, sunset timeline), see [docs/migration-langgraph.md](migration-langgraph.md).

### Pipeline graph

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

## Core principles

1. **If it isn't written down, it doesn't exist** — pipeline stages communicate through files, not in-memory data. Everything is inspectable.
2. **Contracts, not interpretation** — the AI receives a structured JSON contract (exact file lists, data type definitions, dependency maps) instead of vague prose instructions. This is how the engine prevents drift.
3. **Gates are policy, not behavior** — when the pipeline encounters a decision point, what happens (pause for human, auto-approve, or skip) is controlled by a policy file, not hard-coded.
4. **Structured state** — all pipeline artifacts live in organized subfolders, not a flat directory. Any auditor can navigate the run history.
5. **Tamper-evident traceability** — every step is logged with HMAC-SHA256 authentication. See [docs/audit-trail.md](audit-trail.md).
6. **Spec says what, config says how** — the project spec captures *what to build*; `config.yml` captures *how to run the engine* (which AI model, budget limits, gate policies). See [docs/configuration.md](configuration.md).

## Contract system

The contract system is the engine's primary defense against **AI interpretation drift** — the tendency for an AI to forget or reinterpret decisions made earlier in the pipeline. This is the root cause of most cross-file inconsistencies (wrong field names, missing files, imports that reference things that don't exist).

### Design contract

The design stage produces a `DESIGN_CONTRACT.json` alongside a human-readable `ARCHITECTURE.md`. The contract is a structured JSON file that specifies exactly what the implementation stage must produce:

- **Components** — each with an exact file list, dependency declarations, and a file budget (max number of files)
- **Canonical types** — shared data structures (think: "a User always has these fields, defined in this file"). Every implementation chunk receives these definitions verbatim so the AI can't invent its own versions.
- **Tech decisions** — locked-in choices with rationale, so the AI doesn't second-guess them mid-build
- **Import maps** — which component is allowed to depend on which

The contract is validated at creation time with 15+ automated checks (duplicate detection, phantom dependency references, budget overflows, type reference validity).

### Contract compliance checker

After the AI writes code and it's extracted to disk, the compliance checker verifies the output against the design contract:

- **Missing files** — the contract says file X should exist, but it wasn't produced
- **Extra files** — files were produced that aren't in any component's plan
- **Budget violations** — a component produced more files than allowed
- **Type integrity** — the canonical types appear in the correct files with the expected fields

Results are saved as structured evidence records that feed into testing and verification.

## Tier system

Before launching a build, you choose a tier that controls the AI's output budget and cost:

- **Premium** — full output budget, no scope restrictions, higher AI cost. Best for production-quality output with thorough documentation.
- **MVP (Minimum Viable)** — tighter scope guidance plus a hard extraction cutoff. Best for quick prototyping or cost-conscious builds.
  - Design: max 5 components, 40 files *(advisory — injected into the design prompt)*
  - Per-chunk implementation: max 10 files *(advisory — injected into the implement prompt; not enforced mid-run)*
  - Extraction safety cutoff: 80 files / 750 KB *(enforced — the extract stage circuit-breaks if exceeded)*
  - Dashboard shows estimated savings vs. Premium before you commit

## Test & verification

### Auto-detect checks

The test stage inspects the extracted project and automatically selects appropriate quality checks based on the project type:

**Node.js / TypeScript projects** (detected by `package.json`): dependency install, type checking, build, lint, tests.

**Python projects** (detected by `pyproject.toml` or `requirements.txt`): dependency install, syntax validation, import resolution, lint (ruff), type checking (mypy), unit tests (pytest). Structural checks are always mandatory.

### Structural issue analysis

The verification stage classifies failures into categories — type errors, import errors, lint errors, build errors, test failures, and contract compliance issues. Each category gets specific diagnostic output. When the AI-powered verification path is used, it receives this classification so it can provide targeted root-cause analysis instead of a generic summary.

### Evidence records

Every check produces a structured JSON evidence record containing the command that ran, its exit code, full output, timestamps, and environment metadata. Think of these as the "test receipts" — they feed into the dashboard's evidence viewer and the verification stage's analysis.

## Decision gates

At critical moments during the pipeline, a **decision gate** can pause execution and require human input. Gate behavior is controlled per-stage via a policy file (`DECISION_GATES.yml`):

- **pause** — stop and wait for a human to approve or redirect (default for the design stage)
- **auto** — automatically select the configured default option
- **skip** — proceed without stopping (default for implement, test, verify)

Gates trigger on architectural tradeoffs, test failures, and verification rejection — not on missing requirements (those are caught at intake before the pipeline starts).

## Project structure

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
  prompt_guard.py     OWASP LLM01 defenses — sanitize, canary, pattern detection
  verify_trace.py     CLI for HMAC chain integrity verification
  compat.py           Prefect-shim (retires 2026-05-21 with flows/)
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
  ci.yml              GitHub Actions: lint, test (3.10–3.12), import check, security, trace-integrity

tests/                Automated test suite (661 tests, 67% engine coverage)
bench/                Performance benchmarking tools
specs/                Reference spec files for manual testing (not consumed by the pipeline)
```
