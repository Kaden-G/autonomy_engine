# Migration history: Prefect → LangGraph

In v2.0, the pipeline orchestration was migrated from Prefect to
[LangGraph](https://langchain-ai.github.io/langgraph/). This document records
what changed, why, and how the migration was structured. The Prefect entry
point and compatibility shims have since been fully removed; LangGraph is the
only orchestrator in the codebase.

## Contents

- [Why LangGraph](#why-langgraph)
- [What changed vs. what stayed](#what-changed-vs-what-stayed)
- [Migration structure](#migration-structure)

---

## Why LangGraph

The original Prefect-based flow was a linear sequence of `@task`-decorated
functions with `pause_flow_run()` for human-in-the-loop gates. It worked, but
had three pain points that a graph-based orchestrator solves naturally:

**Checkpoint-based resume.** If the pipeline failed at the test stage, Prefect
re-ran from stage 1 — re-calling the LLM for design and implementation,
burning $1-8 in API costs per re-run. LangGraph checkpoints state at every
node boundary, so a failed run resumes from the last successful stage. For a
pipeline that makes expensive LLM calls, this was the performance metric that
mattered most.

**Graph-native retry loops.** When tests fail, the ideal behavior is
"re-implement with the test failures as context, then re-test." In a linear
flow, that required custom retry logic or manual re-runs. In a graph, it's a
conditional edge from `test` back to `implement`, bounded by a configurable
retry budget. The graph makes this control flow explicit and testable.

**Cleaner human-in-the-loop.** Prefect's `pause_flow_run()` required the
Prefect UI/API for decision input. LangGraph's `interrupt()` pauses the graph,
serializes state to the checkpoint, and resumes with the decision injected
via `Command(resume=...)`. No external UI dependency — the decision can come
from a CLI, API, or dashboard.

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

## Migration structure

The migration was designed as an adapter layer: `graph/nodes.py` wraps the
existing `tasks/*.py` functions, translating between LangGraph's state-passing
model and the tasks' file-based I/O. The tasks didn't know LangGraph existed.
This meant the existing engine tests passed without modification.

During the transition, a no-op `@task` shim in `engine/compat.py` kept the
`tasks/*.py` decorators valid while Prefect was a coexisting installation.
Once Prefect was retired, both the shim and the decorators were removed in a
single cleanup pass — leaving the task functions as plain Python callables
invoked by the LangGraph node adapters.
