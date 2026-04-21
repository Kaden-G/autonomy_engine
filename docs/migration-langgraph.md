# LangGraph Migration

## Contents

- [Why LangGraph over Prefect](#why-langgraph-over-prefect)
- [What changed vs. what stayed](#what-changed-vs-what-stayed)
- [Running with LangGraph](#running-with-langgraph)
- [Retired features / Prefect sunset](#retired-features--prefect-sunset)
- [Compat shim rationale](#compat-shim-rationale)

---

## Why LangGraph over Prefect

Version 2.0 migrates the pipeline orchestration from Prefect to [LangGraph](https://langchain-ai.github.io/langgraph/), a stateful graph framework for building agent workflows. The engine modules, state directory structure, audit trail, and dashboard are unchanged — only the orchestration layer was replaced.

The original Prefect-based flow was a linear sequence of `@task`-decorated functions with `pause_flow_run()` for human-in-the-loop gates. It worked, but had three pain points that a graph-based orchestrator solves naturally:

**Checkpoint-based resume.** If the pipeline fails at the test stage, Prefect re-runs from stage 1 — re-calling the LLM for design and implementation, burning $1-8 in API costs per re-run. LangGraph checkpoints state at every node boundary, so a failed run resumes from the last successful stage. For a pipeline that makes expensive LLM calls, this is the performance metric that matters most.

**Graph-native retry loops.** When tests fail, the ideal behavior is "re-implement with the test failures as context, then re-test." In a linear flow, that requires custom retry logic or manual re-runs. In a graph, it's a conditional edge from `test` back to `implement`, bounded by a configurable retry budget. The graph makes this control flow explicit and testable.

**Cleaner human-in-the-loop.** Prefect's `pause_flow_run()` requires the Prefect UI/API for decision input. LangGraph's `interrupt()` pauses the graph, serializes state to the checkpoint, and resumes with the decision injected via `Command(resume=...)`. No external UI dependency — the decision can come from a CLI, API, or dashboard.

## What changed vs. what stayed

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

The migration was designed as an adapter layer: `graph/nodes.py` wraps the existing `tasks/*.py` functions, translating between LangGraph's state-passing model and the tasks' file-based I/O. The tasks don't know LangGraph exists. This means the existing engine tests pass without modification.

## Running with LangGraph

```bash
# New entry point (LangGraph orchestration)
python graph/pipeline.py

# With checkpoint persistence (resume on failure)
python graph/pipeline.py --checkpoint-db state/checkpoints.sqlite

# Resume an interrupted run
python graph/pipeline.py --thread-id <thread-id> --checkpoint-db state/checkpoints.sqlite

# Legacy entry point (retires 2026-05-21)
# Requires: pip install "autonomy-engine[prefect]"
python flows/autonomous_flow.py
```

## Retired features / Prefect sunset

**v1.x Prefect flows retire 2026-05-21.** Use `graph/pipeline.py` (LangGraph) going forward.

The `flows/autonomous_flow.py` entry point, the `@flow` decorator in `engine/compat.py`, and the `pause_flow_run` / `RunInput` / `require_decision` path in `engine/decision_gates.py` all sunset on 2026-05-21 (30 days from 2026-04-21). Until then, they remain callable if Prefect is installed (`pip install "autonomy-engine[prefect]"`), with banners on each deprecated symbol.

Tests that exercise only the Prefect flow (`tests/test_production_readiness.py`'s `TestGracefulShutdown` and `TestConfigLoading` classes) are gated behind `RUN_DEPRECATED_TESTS=1` and skipped by default in CI. `TestStructuredLogging` is NOT gated — it tests `engine.log_config`, which is orchestrator-agnostic and STILL-ACTIVE.

Full retirement plan: [docs/prefect-sunset-audit.md](prefect-sunset-audit.md).

## Compat shim rationale

`engine/compat.py` is a transitional module. It provides:

- No-op `@task` / `@flow` decorators when Prefect is not installed — so `tasks/*.py` continue to use the `@task(name="…")` decorator in both modes without conditional imports.
- `pause_flow_run` stub that raises `NotImplementedError` when invoked without Prefect — LangGraph mode should never reach this path; if it does, the error is loud.
- `RunInput` stub class so `engine/decision_gates.py::DecisionInput(RunInput)` still parses.

There is **no module-level `DeprecationWarning`** on import: the `@task` decorator is still actively used by every task file under LangGraph, so firing a warning on import would be noise, not signal. The sunset signals are: the banner on `flows/autonomous_flow.py`, the symbol-level banners on `DecisionInput` and `require_decision`, the `NotImplementedError` in `pause_flow_run`, and this doc.
