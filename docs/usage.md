# Usage

## Contents

- [Supported project types](#supported-project-types)
- [Quickstart (60 seconds)](#quickstart-60-seconds)
- [Setup (development)](#setup-development)
- [Step 1: Intake](#step-1-intake)
- [Step 2: Run the engine](#step-2-run-the-engine)
- [Output](#output)
- [Dashboard](#dashboard)

---

## Supported project types

The engine doesn't just generate code — it also tests, lints, type-checks, and verifies it. That full pipeline (generate → extract → test → verify) requires the engine to understand the project's toolchain. Today, two ecosystems are fully supported end-to-end:

### Python (fully supported)

Detected by: `requirements.txt`, `pyproject.toml`, or `setup.py`

The engine auto-configures: dependency install (`pip`), syntax validation (`py_compile`), import resolution (custom AST-based checker), linting with auto-fix (`ruff`), type checking (`mypy`), and unit tests (`pytest` when a test directory or config is present). A Python virtualenv is created in the sandbox, cached across runs when dependencies haven't changed.

### Node.js / TypeScript (fully supported)

Detected by: `package.json`

The engine auto-configures: dependency install (`npm`), TypeScript type checking (`tsc` or a `typecheck` script), build (`npm run build` when configured), linting (`npm run lint` when configured), and tests (`npm test` when configured, with special handling for React test runners). Dependencies are installed in an isolated `node_modules` with a shared npm cache.

### Other languages (not yet supported for testing)

The AI can *design and generate* code in any language — Go, Rust, Java, C#, etc. — because the design and implementation stages are language-agnostic (they work from the contract, not from language-specific tooling). However, the **test stage will skip automated checks** for unrecognized project types, which means the verification stage has less evidence to work with. The contract compliance checker (which validates file lists, size budgets, and data type definitions) still runs regardless of language.

**What it would take to add a new language:** Each new ecosystem needs three things — a project-type detector (e.g., "if `go.mod` exists → Go project"), a dependency installer, and a set of check commands (build, lint, test). In the current codebase, that's roughly 30–50 lines in `engine/evidence.py` (auto-detection) and `engine/sandbox.py` (environment setup). The architecture is designed for this — `auto_detect_checks` is a straightforward if/elif chain, and new branches follow the same pattern as the existing Python and Node.js ones.

## Quickstart (60 seconds)

### Option A: Docker (recommended — zero local setup)

```bash
git clone https://github.com/Kaden-G/autonomy_engine.git
cd autonomy_engine
cp .env.example .env               # add your ANTHROPIC_API_KEY
docker compose up                  # → http://localhost:8501
```

That's it. The dashboard is running. Create a project, pick a tier, launch a build.

### Option B: Local install

```bash
git clone https://github.com/Kaden-G/autonomy_engine.git
cd autonomy_engine
pip install -r requirements.txt   # pinned production deps
pip install -e ".[dashboard]"      # adds Streamlit + Plotly
cp .env.example .env               # add your ANTHROPIC_API_KEY
streamlit run dashboard/app.py     # → http://localhost:8501
```

### Option C: CLI only (no dashboard)

```bash
pip install -r requirements.txt
pip install -e .
cp .env.example .env               # add your ANTHROPIC_API_KEY
python -m intake.intake new-project
python graph/pipeline.py           # LangGraph orchestration (v2.0)
```

## Setup (development)

```bash
pip install -r requirements.txt    # production: pinned versions
pip install -e ".[dev,dashboard]"   # editable with dev tools + dashboard
```

### Environment setup

1. Copy the example environment file:
   ```bash
   cp .env.example .env
   ```
2. Add your API keys to `.env` (Claude and/or OpenAI)
3. Enable the pre-commit hook (prevents accidental key commits):
   ```bash
   git config core.hooksPath .githooks
   ```

**Security:** The `.env` file is excluded from version control. The pre-commit hook rejects any commit containing API key patterns. Never share `.env` — use `.env.example` as the template. See [docs/threat-model.md](threat-model.md#api-key-handling) for more.

## Step 1: Intake

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

## Step 2: Run the engine

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

The Prefect UI is available at `http://localhost:4200` for monitoring and gate approvals. New projects should use `graph/pipeline.py`. See [docs/migration-langgraph.md](migration-langgraph.md) for the full retirement plan.

</details>

## Output

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

### Main pages

**Dashboard** — The landing page. Shows pipeline status with real pass/fail indicators (green = all checks passed, red = failures, amber = in progress, gray = pending). Also displays recent run history and cache hit statistics. Status indicators are evidence-backed — the dashboard never shows a false "success."

**Pipeline Explorer** — An interactive visual map of the six pipeline stages. Click any stage to see its inputs, outputs, and why it exists. This page is educational and doesn't require run data — useful for onboarding or explaining the architecture to stakeholders.

**Create Project** — Form-based intake that replaces the CLI's `intake new-project`. Fill in project name, description, requirements, and tech stack. Features include loading previous specs, viewing run history per project, and auto-saving form fields. One-click button to launch the pipeline directly from here.

**Run Pipeline** — Tier selection (MVP vs. Premium) with pre-run cost estimates showing projected token usage and dollar cost. Once launched, displays live progress with a trace timeline that updates as each stage completes. Decision gates surface here when the pipeline pauses for human approval.

### Security & ops pages

**Run Outputs** — Browse every artifact from a pipeline run, organized by stage (design, implement, extract, test, verify). Select a run from the dropdown, then click any output file to read it inline — architecture docs, generated code, test results, verification reports.

**Inspector** — The deep-dive view. Shows the complete trace timeline, individual evidence records (with command, exit code, and full output), every decision gate interaction, all artifacts, and the config snapshot that was active during the run.

**Audit Trail** — Visual hash-chain viewer. Each trace entry shows its HMAC signature and link to the previous entry. Includes a one-click integrity verification button that walks the chain and flags any broken links. Export the full trail to a file for offline review or compliance handoff. See [docs/audit-trail.md](audit-trail.md) for the CLI equivalent.

**Configuration** — Read-only view of the active engine configuration: AI model settings, gate policies, sandbox config, approved check commands, and cache TTLs. Useful for verifying what settings a run will use before launching.

**Benchmarks** — Per-stage timing breakdowns, cache hit rates across runs, and actual vs. projected token usage comparisons. Helps identify which stages are bottlenecks and whether cost estimates are calibrated.
