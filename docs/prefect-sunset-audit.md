# Prefect Sunset Audit

**Date:** 2026-04-21
**Sunset target:** 2026-05-21 (30 days from audit)
**Actual retirement:** 2026-04-25 (pulled forward 26 days)
**Status:** ✅ Complete
**Tracks:** P0-5, P2-6

> **Update 2026-04-25.** Retirement was pulled forward as part of the
> deps CVE cleanup — the Prefect transitive tree (`fakeredis` →
> `lupa`, `alembic`, `amplitude-analytics`, etc.) was the source of
> most of the pip-audit findings, and dropping it slimmed
> `requirements.lock` from 363 → 216 lines while resolving 9 of 10
> CVEs. `flows/`, `tests/test_production_readiness.py`,
> `engine/compat.py::{flow, pause_flow_run, RunInput}`, and
> `engine/decision_gates.py::{require_decision, DecisionInput}` are
> all gone. `engine/compat.py::task` remains as a single-symbol
> no-op shim so `tasks/*.py` files don't need to drop the decorator.

## Context

The Autonomy Engine migrated its orchestrator from Prefect to LangGraph
(`graph/pipeline.py`) in v2.0. Prefect is now an optional dependency. This
audit inventories every remaining Prefect reference in the tree, classifies
each, and either retires it immediately or marks it for the 2026-05-21
sunset.

### Categories

- **DEAD** — unreachable in LangGraph mode with no feature flag. Delete now.
- **LEGACY-GATED** — only reachable when Prefect is explicitly installed.
  Keep, mark with a sunset banner, delete on 2026-05-21.
- **STILL-ACTIVE** — referenced from `graph/` or non-`flows/` code. Must
  continue to work. If any path is wrong here, that's a latent bug → report.

## Findings

| file:line | symbol | category | disposition |
|---|---|---|---|
| `flows/autonomous_flow.py` (whole file) | Prefect `@flow` entry point | **LEGACY-GATED** | Add file-level banner. Run only if `prefect` installed. Delete on 2026-05-21. |
| `flows/autonomous_flow.py:22` | `from engine.compat import flow` | **LEGACY-GATED** | Covered by file banner. |
| `flows/autonomous_flow.py:29` | `require_decision` import | **LEGACY-GATED** | Covered by file banner. |
| `flows/autonomous_flow.py:223` | `require_decision(exc.gate, exc.options)` | **LEGACY-GATED** | Covered by file banner. |
| `engine/compat.py` (whole file) | Prefect compat shim | **STILL-ACTIVE** | @task stub still used by `tasks/*.py` under LangGraph. Docstring updated with sunset plan. No module-level `DeprecationWarning` (would fire on every pipeline run). |
| `engine/compat.py:21-27` | `try: from prefect import …` | **STILL-ACTIVE** | Required by @task passthrough. |
| `engine/compat.py:33-52` | `task` / `flow` no-op decorators | **STILL-ACTIVE** (task) / **LEGACY-GATED** (flow) | @task stays; @flow stub dies with `flows/` on 2026-05-21. |
| `engine/compat.py:54-63` | `pause_flow_run` NotImplementedError stub | **LEGACY-GATED** | Deletes with `flows/` on 2026-05-21. |
| `engine/compat.py:65-68` | `RunInput` no-op class | **LEGACY-GATED** | Deletes with `flows/` on 2026-05-21. |
| `engine/decision_gates.py:29` | `from engine.compat import pause_flow_run, RunInput` | **LEGACY-GATED** (imports) | Both imports only used by `require_decision` + `DecisionInput`, both LEGACY-GATED. Import line deletes with them on 2026-05-21. |
| `engine/decision_gates.py:53-57` | `class DecisionInput(RunInput)` | **LEGACY-GATED** | Add class-level banner. Used only by `require_decision`. |
| `engine/decision_gates.py:100-114` | `def require_decision(...)` | **LEGACY-GATED** | Add function-level banner. Only called from `flows/autonomous_flow.py:223`. Raises `NotImplementedError` under LangGraph. |
| `engine/decision_gates.py` — rest of module | `DecisionRequired`, `save_decision`, `get_gate_policy`, `handle_gate`, policy defaults | **STILL-ACTIVE** | Imported by `graph/nodes.py`. No change. |
| `tasks/bootstrap.py:8`, `design.py:17`, `extract.py:21`, `implement.py:19`, `test.py:17`, `verify.py:15` | `from engine.compat import task` | **STILL-ACTIVE** | No-op under LangGraph; real decorator under Prefect. No change. |
| `tasks/{6}.py` | `@task(name="...")` decorator | **STILL-ACTIVE** | Used by both orchestrators. No change. |
| `tests/test_production_readiness.py` (whole module) | Tests `_shutdown_handler`, `_setup_signal_handlers`, `_load_config` from `flows.autonomous_flow` | **LEGACY-GATED** | Add module-level `pytestmark = pytest.mark.skipif(os.getenv("RUN_DEPRECATED_TESTS") != "1", …)`. |
| `tests/test_decision_gates.py` | Tests `save_decision`, `handle_gate`, `DecisionRequired`, policy loading | **STILL-ACTIVE** | Touches no Prefect-specific API. No change. |
| `tests/test_task_gates.py` | Tests gate application in task wrappers | **STILL-ACTIVE** | No change. |
| `.github/workflows/ci.yml:111` | `import flows.autonomous_flow` in import-check | **STILL-ACTIVE** (via compat) | Works because compat provides no-op decorators. Add a one-line comment noting this entry will retire with `flows/`. |
| `pyproject.toml:38-41` | `prefect = ["prefect>=3.0,<4"]` optional extra | **LEGACY-GATED** | Extra stays until 2026-05-21, then removed. |
| `pyproject.toml:51` | `"flows/autonomous_flow.py" = ["E402"]` lint exemption | **LEGACY-GATED** | Removed when `flows/` is deleted. |
| `README.md` (7 refs) | `python flows/autonomous_flow.py` in Quickstart, Run modes, Config | **LEGACY-GATED** docs | Update to `./run.sh pipeline` (the post-v2.0 entry). Add "Retired features" subsection pointing at sunset date. |
| `run.sh:39` | `$PYTHON graph/pipeline.py "$@"` | **STILL-ACTIVE** | Already on LangGraph. No change. |
| `graph/nodes.py:45-46` | `logging.getLogger("prefect.task_runs").setLevel(CRITICAL)` | **STILL-ACTIVE** | Defensive log suppression — harmless when Prefect isn't installed. Delete with compat on 2026-05-21. |
| `graph/nodes.py:72` | `# Ported from flows/autonomous_flow.py` comment | **STILL-ACTIVE** (comment) | Kept until `flows/` deletion — after which the comment can drop the "ported" attribution. |
| `requirements.lock`, `requirements-dev.lock` | Prefect transitively pinned (e.g., `prefect==3.x`) | **LEGACY-GATED** | Lockfile entries drop when Prefect is removed from the extras on 2026-05-21. Not modified by this PR. |

## STILL-ACTIVE review (pushback check)

No STILL-ACTIVE symbol is a latent bug. Every item marked STILL-ACTIVE was
intentionally kept by the v2.0 migration to let both orchestrators coexist:

- `engine.compat.task` — a no-op decorator under LangGraph, the real
  Prefect decorator when `prefect` is installed. Both modes work.
- `engine/decision_gates.py` (non-`require_decision` API) — orchestrator-
  agnostic. `graph/nodes.py` uses the exceptions, policy loader, and
  `save_decision` directly.
- `ci.yml` import-check of `flows.autonomous_flow` — an inexpensive
  smoke-test that the legacy entry file still imports cleanly (via compat
  stubs). Provides early warning if a future refactor breaks compat.

No pushback required.

## Actions taken in this PR

1. **Banners** added to `flows/autonomous_flow.py` (file), and to
   `engine/decision_gates.py::DecisionInput` and
   `engine/decision_gates.py::require_decision` (symbol-level).
2. **`engine/compat.py`** docstring rewritten to spell out the sunset
   plan. No `warnings.warn(DeprecationWarning)` — `@task` is still
   actively used by all `tasks/*.py` under LangGraph, so firing on import
   would be noise, not signal.
3. **`tests/test_production_readiness.py`** gated behind
   `RUN_DEPRECATED_TESTS=1`.
4. **`.github/workflows/ci.yml`** — one-line comment noting that
   deprecated tests are not run by default.
5. **`README.md`** — Quickstart and Run commands updated from
   `python flows/autonomous_flow.py` to `./run.sh pipeline`; new
   "Retired features" subsection under Security Model.

## After 2026-05-21 (follow-up PR)

When the sunset date arrives:

- Delete `flows/` directory.
- Delete `engine/decision_gates.py::require_decision` function and
  `DecisionInput` class. Delete the compat import line for
  `pause_flow_run` and `RunInput`.
- Delete `engine/compat.py::pause_flow_run`, `RunInput`, and `@flow`
  no-op stubs. Keep `@task` if `tasks/*.py` still uses it (or
  eliminate the decorator calls and delete `engine/compat.py` entirely).
- Delete `tests/test_production_readiness.py` (or port the shutdown
  handler tests to `graph/nodes.py` first).
- Remove `flows.autonomous_flow` import from `.github/workflows/ci.yml`.
- Remove `prefect` extra from `pyproject.toml`. Remove the E402
  per-file-ignore for `flows/autonomous_flow.py`.
- Bump lockfiles to drop Prefect.
- Remove the "Retired features" section from README.

## Risk / mitigation

- **Remaining risk:** developers may continue to invoke
  `python flows/autonomous_flow.py` out of muscle memory during the
  30-day window.
- **Mitigation:** the file banner makes the deprecation visible on open;
  README "Retired features" section and updated Quickstart commands
  point at the new entry; the function-level banner on `require_decision`
  will show up in any IDE Go-to-Definition; `pause_flow_run`'s
  `NotImplementedError` gives a loud runtime signal if the legacy path
  is invoked without Prefect installed.
