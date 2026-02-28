# Autonomy Engine v1.2

A Prefect-based autonomous build engine with human-in-the-loop decision gates.

## What This Is For

- Automating structured software build workflows (design, implement, test, verify, extract)
- Providing human oversight at critical decision points via Prefect's pause/resume
- Maintaining full traceability of every step, prompt, and decision in `TRACE.json`
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
│ Normalized Project Spec  │  ← machine contract
│ (state/inputs/)          │
└────────────┬─────────────┘
             │ read-only
┌────────────▼─────────────┐
│ Autonomous Engine        │  ← Phase 1: machine-driven, no ambiguity
│ (Prefect flow)           │
└──────────────────────────┘
```

### Pipeline Stages

```
[intake]    ──→ state/inputs/project_spec.yml + rendered artifacts
                    ↓
[bootstrap] ──→ verify inputs, init TRACE.json
                    ↓
[design]    ──→ state/designs/              ←── may pause at decision gate
                    ↓
[implement] ──→ state/implementations/
                    ↓
[test]      ──→ state/tests/
                    ↓
[verify]    ──→ state/tests/VERIFICATION.md
                    ↓
[extract]   ──→ ../<project-name>/          ←── standalone project folder
                 + state/build/MANIFEST.md
```

The **extract** step parses `IMPLEMENTATION.md` for fenced code blocks marked with
bold filenames (`**path/to/file.ext**`) or header filenames (`### file.ext`), then
writes each file to a sibling directory named after the project. No LLM call — pure
regex parsing.

### Core Principles

1. **If it isn't written, it doesn't exist** — tasks communicate through files in `state/`, not return values
2. **Gates are policy, not behavior** — tasks raise `DecisionRequired`; only the flow may pause execution
3. **Structured state** — `state/` has predefined subfolders, not a flat directory
4. **Traceability** — every task appends to `state/TRACE.json` with inputs, outputs, model, and prompt hash
5. **No ambiguity at runtime** — all ambiguity is resolved during intake, before execution begins

### Decision Gates

After intake, decision gates are **only** for:
- Architectural tradeoffs (A vs B)
- Performance vs cost decisions
- Optional enhancements

**Not** for missing requirements, clarification questions, or incomplete specs. Those are blocked at intake.

Gate definitions live in `templates/DECISION_GATES.yml`.

## Setup

```bash
cd ~/Desktop/autonomy_engine
pip install -e .
```

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

The flow will appear in the Prefect UI at `http://localhost:4200`. If a decision gate triggers, resume from the UI.

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
    REQUIREMENTS.md, CONSTRAINTS.md, NON_GOALS.md, ACCEPTANCE_CRITERIA.md
    prompts/
      design.txt, implement.txt, test.txt, verify.txt
  state/
    TRACE.json
    inputs/ designs/ implementations/ tests/ decisions/ build/
```

Without `--project-dir`, all state and config lives in the engine root (unchanged
from previous behavior).

## Configuration

Edit `config.yml` to switch LLM providers and configure notifications:

```yaml
llm:
  provider: "claude"  # or "openai"
```

Set API keys via environment variables: `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`.

## Project Structure

```
intake/             Project intake CLI and Pydantic schema
  schema.py         Canonical ProjectSpec definition
  renderer.py       Generates engine artifacts from validated spec
  intake.py         CLI entry point (new-project, from-file, validate)
engine/             Core modules (LLM provider, gates, state, tracing, notifications)
  context.py        Singleton path context — resolves project vs engine root paths
flows/              Prefect flow definition — the entry point
tasks/              Individual pipeline stages as Prefect tasks
templates/          Skeleton templates and prompt files
  prompts/          LLM prompt files (tracked by SHA-256 hash in TRACE.json)
state/              Runtime artifacts (gitignored except .gitkeep)
  inputs/           Intake-generated artifacts (project_spec.yml + rendered markdown)
  designs/          Architecture documents
  implementations/  Generated code
  tests/            Test and verification results
  decisions/        Human decisions recorded from gates
  build/            Extraction manifest (MANIFEST.md)
  TRACE.json        Traceability spine
```
