"""Graph nodes — thin wrappers that adapt existing task functions to LangGraph.

Architecture decision:
    Each node follows the same pattern: read state → call existing task → update state.
    The actual work is delegated to tasks/*.py and engine/*.py, keeping this module
    as a pure adapter layer. If you're looking for business logic, look at the task
    modules, not here.

    Why not just use the task functions directly as nodes? Two reasons:
    1. LangGraph nodes must accept and return state dicts. Our tasks use side effects
       (reading/writing files via state_loader) and return None.
    2. We need to capture structured StageResult metadata (artifacts, errors) that
       the tasks don't currently return — they trace it instead.

    This adapter pattern means we can switch orchestrators again in the future
    without touching the task logic. The tasks don't know or care that LangGraph
    exists.

Human-in-the-loop design (OWASP LLM Top 10: LLM09 - Overreliance):
    Decision gates use LangGraph's interrupt() to pause execution and wait for
    human input. This is the pipeline's defense against overreliance on AI output —
    humans review architecture decisions, triage test failures, and approve/reject
    verification results before the pipeline continues.

    The interrupt() pattern keeps state in the checkpoint (no re-execution
    needed), exposes the decision context through the graph state
    (inspectable, replayable), and resumes via graph.invoke() with the
    decision injected.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
from pathlib import Path
from typing import Any

from langgraph.types import interrupt

from engine.cache import evict_stale_llm_cache
from engine.context import get_state_dir, init as init_context
from engine.decision_gates import (
    DecisionRequired,
    get_gate_policy,
    save_decision,
)
from engine.log_config import configure_logging
from engine.notifier import notify
from engine.sandbox import evict_stale_venv_cache
from engine.tracer import init_run, trace
from graph.state import Decision, PipelineState, StageResult, StageStatus

# Import task functions — these are the actual workers
from tasks.bootstrap import bootstrap_project
from tasks.design import design_system
from tasks.extract import ExtractionValidationError, extract_project
from tasks.implement import implement_system
from tasks.test import test_system
from tasks.verify import verify_system

logger = logging.getLogger(__name__)

# ── Graceful shutdown ─────────────────────────────────────────────────────────
# Ported from flows/autonomous_flow.py. The audit trail must be closed cleanly
# even if the process is killed. LangGraph's checkpointer handles state
# persistence, but the HMAC chain needs an explicit shutdown entry.

_SHUTTING_DOWN = False


def _shutdown_handler(signum: int, _frame) -> None:
    """Handle SIGTERM/SIGINT: log an audit entry and exit cleanly."""
    global _SHUTTING_DOWN
    if _SHUTTING_DOWN:
        logger.warning("Forced shutdown (second signal). Exiting immediately.")
        sys.exit(1)

    _SHUTTING_DOWN = True
    sig_name = signal.Signals(signum).name
    logger.info("Received %s — shutting down gracefully...", sig_name)

    try:
        trace(
            task="shutdown",
            inputs=[],
            outputs=[],
            model=None,
            prompt_hash=None,
            extra={"signal": sig_name, "reason": "graceful shutdown"},
        )
    except Exception:
        pass  # Don't let tracer errors prevent clean exit

    sys.exit(128 + signum)


def _setup_signal_handlers() -> None:
    """Install SIGTERM and SIGINT handlers for graceful shutdown."""
    signal.signal(signal.SIGTERM, _shutdown_handler)
    signal.signal(signal.SIGINT, _shutdown_handler)


# ── Helper: resolve actor for decisions ─────────────────────────────────────


def _resolve_actor() -> str:
    """Determine the actor identity for decision records."""
    ae_actor = os.environ.get("AE_ACTOR")
    if ae_actor:
        return ae_actor
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or os.environ.get("USERNAME")
    return f"human:{user}" if user else "human"


# ── Helper: persist pending-gate marker for out-of-process UIs ──────────────


def _pending_gate_path(state: PipelineState):
    """Path to the pending-gate marker file for this run, or None if run_id unset."""
    from engine.context import get_state_dir

    run_id = state.get("run_id")
    if not run_id:
        return None
    return get_state_dir() / "runs" / run_id / "pending_gate.json"


def _write_pending_gate(state: PipelineState, exc: DecisionRequired) -> None:
    """Drop a pending_gate.json the dashboard can poll before interrupt() fires.

    Why a file instead of relying on LangGraph's checkpoint: the dashboard polls
    from a different process than the one that pauses, and reading the checkpoint
    there means reopening the SQLite DB + rebuilding the graph on every rerun.
    A ~200-byte JSON file is the cheap path.
    """
    import json

    path = _pending_gate_path(state)
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "gate": exc.gate,
                    "stage": exc.stage,
                    "options": exc.options,
                    "message": f"Decision required: {exc.gate}",
                }
            )
        )
    except OSError as e:
        logger.warning("Could not write pending_gate.json: %s", e)


def _clear_pending_gate(state: PipelineState) -> None:
    """Remove the pending marker after the gate resolves."""
    path = _pending_gate_path(state)
    if path is None or not path.exists():
        return
    try:
        path.unlink()
    except OSError as e:
        logger.warning("Could not remove pending_gate.json: %s", e)


# ── Helper: handle decision gate logic ──────────────────────────────────────


def _handle_decision_gate(exc: DecisionRequired, state: PipelineState) -> Decision | None:
    """Apply gate policy and return a Decision, or use interrupt() for human input.

    This consolidates the three gate behaviors (skip/auto/pause) into a single
    function. The caller doesn't need to know which policy applies — it just
    gets back a Decision (or None for skip).

    Returns:
        Decision if a decision was made (auto or human via interrupt).
        None if the gate policy is 'skip'.
    """
    policy = get_gate_policy(exc.stage)
    logger.info(
        "DecisionRequired at %s (gate=%s), policy=%s",
        exc.stage,
        exc.gate,
        policy.policy,
    )

    if policy.policy == "skip":
        logger.info("Skipping gate '%s' per policy", exc.gate)
        return None

    if policy.policy == "auto":
        selected = policy.default_option or exc.options[0]
        logger.info("Auto-selecting '%s' for gate '%s'", selected, exc.gate)
        decision = Decision(
            gate=exc.gate,
            stage=exc.stage,
            selected=selected,
            actor="auto-policy",
        )
        save_decision(
            gate=exc.gate,
            stage=exc.stage,
            allowed_options=exc.options,
            selected=selected,
            actor="auto-policy",
        )
        return decision

    # policy == "pause" → interrupt for human input
    # LangGraph serializes this state to the checkpoint, then pauses.
    # The caller resumes the graph with a Command containing the decision.
    logger.info("Pausing for human decision at gate '%s'", exc.gate)

    # Persist gate metadata so out-of-process callers (e.g. the dashboard)
    # can surface it without importing langgraph or reopening the checkpoint.
    _write_pending_gate(state, exc)

    human_input = interrupt(
        {
            "gate": exc.gate,
            "stage": exc.stage,
            "options": exc.options,
            "message": f"Decision required: {exc.gate}",
        }
    )

    # Resume path — decision is in, clear the pending marker.
    _clear_pending_gate(state)

    # When resumed, human_input contains the decision dict
    # Expected shape: {"choice": "accept", "rationale": "Looks good"}
    selected = human_input.get("choice", exc.options[0])
    rationale = human_input.get("rationale", "")

    decision = Decision(
        gate=exc.gate,
        stage=exc.stage,
        selected=selected,
        actor=_resolve_actor(),
        rationale=rationale,
    )
    save_decision(
        gate=exc.gate,
        stage=exc.stage,
        allowed_options=exc.options,
        selected=selected,
        actor=decision.actor,
        rationale=rationale,
    )
    return decision


# ── Config loading ──────────────────────────────────────────────────────────


def _load_config(project_root: Path) -> dict:
    """Load environment-aware config (same logic as autonomous_flow.py)."""
    import yaml

    explicit_path = os.environ.get("AE_CONFIG_PATH")
    if explicit_path:
        config_path = project_root / explicit_path
        if not config_path.exists():
            raise FileNotFoundError(
                f"AE_CONFIG_PATH={explicit_path} does not exist at {config_path}"
            )
    else:
        env_name = os.environ.get("AE_ENV", "").strip().lower()
        if env_name:
            config_path = project_root / f"config.{env_name}.yml"
            if not config_path.exists():
                raise FileNotFoundError(f"AE_ENV={env_name} but config.{env_name}.yml not found")
        else:
            config_path = project_root / "config.yml"
            if not config_path.exists():
                return {}

    with open(config_path) as f:
        return yaml.safe_load(f) or {}


# ── Required intake files ───────────────────────────────────────────────────

REQUIRED_INPUTS = [
    "inputs/project_spec.yml",
    "inputs/REQUIREMENTS.md",
    "inputs/CONSTRAINTS.md",
    "inputs/NON_GOALS.md",
    "inputs/ACCEPTANCE_CRITERIA.md",
]


# ═══════════════════════════════════════════════════════════════════════════════
# GRAPH NODES
#
# Each node is a function: PipelineState → partial PipelineState update.
# LangGraph merges the returned dict into the existing state automatically.
# Nodes should only return the keys they modify — not the full state.
# ═══════════════════════════════════════════════════════════════════════════════


def init_node(state: PipelineState) -> dict[str, Any]:
    """Initialize the pipeline: set up context, create run, load config.

    This is the graph's entry point. Everything that needs to happen exactly
    once before any stage runs goes here.

    Note: Signal handlers are installed here because this is the first node
    to execute. If the process dies during any subsequent node, the shutdown
    handler ensures the HMAC chain is closed properly.
    """
    configure_logging()
    _setup_signal_handlers()

    # Initialize project context (sets thread-local project directory)
    init_context(state.get("project_dir"))

    # Verify intake completion — hard gate
    state_dir = get_state_dir()
    missing = [f for f in REQUIRED_INPUTS if not (state_dir / f).exists()]
    if missing:
        return {
            "error": ("Intake not completed. Missing: " + ", ".join(missing)),
            "current_stage": "init",
            "stage_results": {
                "init": StageResult(
                    status=StageStatus.FAILED,
                    error="Missing intake files: " + ", ".join(missing),
                )
            },
        }

    # Initialize run — creates run directory, HMAC key, config snapshot
    run_id = init_run()
    logger.info("Run %s started (LangGraph orchestration).", run_id)

    # Load config and evict stale caches
    project_root = state_dir.parent
    config = _load_config(project_root)

    cache_cfg = config.get("cache") or {}
    llm_ttl = cache_cfg.get("llm_ttl_days", 30)
    venv_ttl = cache_cfg.get("venv_ttl_days", 7)
    if llm_ttl > 0:
        evict_stale_llm_cache(ttl_days=llm_ttl)
    if venv_ttl > 0:
        evict_stale_venv_cache(ttl_days=venv_ttl)

    return {
        "run_id": run_id,
        "config": config,
        "current_stage": "init",
        "stage_results": {
            "init": StageResult(status=StageStatus.PASSED),
        },
        "decisions": {},
        "error": None,
        "retry_count": 0,
        "max_retries": config.get("pipeline", {}).get("max_retries", 1),
    }


def bootstrap_node(state: PipelineState) -> dict[str, Any]:
    """Scaffold directories and verify inputs.

    Wraps tasks.bootstrap.bootstrap_project(). No decision gate here —
    bootstrap is deterministic and fast.
    """
    logger.info("Starting bootstrap...")
    try:
        bootstrap_project()
        return {
            "current_stage": "bootstrap",
            "stage_results": {
                **state.get("stage_results", {}),
                "bootstrap": StageResult(
                    status=StageStatus.PASSED,
                    artifacts=["config_snapshot.yml"],
                ),
            },
        }
    except Exception as e:
        logger.error("Bootstrap failed: %s", e)
        return {
            "current_stage": "bootstrap",
            "stage_results": {
                **state.get("stage_results", {}),
                "bootstrap": StageResult(
                    status=StageStatus.FAILED,
                    error=str(e),
                ),
            },
            "error": f"Bootstrap failed: {e}",
        }


def design_node(state: PipelineState) -> dict[str, Any]:
    """Generate architecture and design contract via LLM.

    Decision gate: if the LLM detects architectural ambiguity, it raises
    DecisionRequired. We use interrupt() to pause for human input, then
    re-run the design task with the decision injected.
    """
    logger.info("Starting design...")
    try:
        design_system()
        return {
            "current_stage": "design",
            "stage_results": {
                **state.get("stage_results", {}),
                "design": StageResult(
                    status=StageStatus.PASSED,
                    artifacts=[
                        "designs/ARCHITECTURE.md",
                        "designs/DESIGN_CONTRACT.json",
                    ],
                ),
            },
        }
    except DecisionRequired as exc:
        decision = _handle_decision_gate(exc, state)
        if decision is None:
            # Skip policy — mark as passed and move on
            return {
                "current_stage": "design",
                "stage_results": {
                    **state.get("stage_results", {}),
                    "design": StageResult(
                        status=StageStatus.SKIPPED,
                        metadata={"gate_skipped": exc.gate},
                    ),
                },
            }

        # Decision was made (auto or human) — re-run design with it
        logger.info("Re-running design with decision: %s", decision.selected)
        design_system()
        return {
            "current_stage": "design",
            "stage_results": {
                **state.get("stage_results", {}),
                "design": StageResult(
                    status=StageStatus.PASSED,
                    artifacts=[
                        "designs/ARCHITECTURE.md",
                        "designs/DESIGN_CONTRACT.json",
                    ],
                    metadata={"decision": decision.selected},
                ),
            },
            "decisions": {
                **state.get("decisions", {}),
                exc.gate: decision,
            },
        }
    except Exception as e:
        logger.error("Design failed: %s", e)
        return {
            "current_stage": "design",
            "stage_results": {
                **state.get("stage_results", {}),
                "design": StageResult(
                    status=StageStatus.FAILED,
                    error=str(e),
                ),
            },
            "error": f"Design failed: {e}",
        }


def implement_node(state: PipelineState) -> dict[str, Any]:
    """Generate code from the design contract via LLM.

    No decision gate in the current implementation — the implement stage
    follows the design contract deterministically. Future enhancement:
    add a gate if the implementation deviates significantly from the contract.
    """
    logger.info("Starting implementation...")

    # Re-entries via the test → implement retry edge must increment retry_count;
    # route_after_test relies on it to bound the loop. Detect a retry by a prior
    # implement StageResult in state.
    prior_results = state.get("stage_results", {})
    is_retry = "implement" in prior_results
    retry_update: dict[str, Any] = (
        {"retry_count": state.get("retry_count", 0) + 1} if is_retry else {}
    )
    if is_retry:
        logger.info("Implement retry %d", retry_update["retry_count"])

    try:
        implement_system()
        return {
            "current_stage": "implement",
            "stage_results": {
                **prior_results,
                "implement": StageResult(
                    status=StageStatus.PASSED,
                    artifacts=[
                        "implementations/IMPLEMENTATION.md",
                        "implementations/FILE_MANIFEST.json",
                    ],
                ),
            },
            **retry_update,
        }
    except Exception as e:
        logger.error("Implementation failed: %s", e)
        return {
            "current_stage": "implement",
            "stage_results": {
                **prior_results,
                "implement": StageResult(
                    status=StageStatus.FAILED,
                    error=str(e),
                ),
            },
            "error": f"Implementation failed: {e}",
            **retry_update,
        }


def extract_node(state: PipelineState) -> dict[str, Any]:
    """Parse AI output into files on disk.

    No decision gate. Has a circuit breaker for safety (file count/size limits).
    Validation failures (syntax errors etc.) surface as a distinct failure
    type so route_after_extract can send them back to implement for retry —
    other failures (manifest schema violations, IO errors) terminate.
    """
    logger.info("Starting extraction...")
    try:
        extract_project()
        return {
            "current_stage": "extract",
            "stage_results": {
                **state.get("stage_results", {}),
                "extract": StageResult(
                    status=StageStatus.PASSED,
                    artifacts=["build/MANIFEST.md"],
                ),
            },
        }
    except ExtractionValidationError as e:
        logger.error("Extraction validation failed: %s", e)
        return {
            "current_stage": "extract",
            "stage_results": {
                **state.get("stage_results", {}),
                "extract": StageResult(
                    status=StageStatus.FAILED,
                    error=str(e),
                    metadata={
                        "failure_type": "validation",
                        "failures": e.failures,
                    },
                ),
            },
            "error": f"Extraction validation failed: {e}",
        }
    except Exception as e:
        logger.error("Extraction failed: %s", e)
        return {
            "current_stage": "extract",
            "stage_results": {
                **state.get("stage_results", {}),
                "extract": StageResult(
                    status=StageStatus.FAILED,
                    error=str(e),
                ),
            },
            "error": f"Extraction failed: {e}",
        }


def test_node(state: PipelineState) -> dict[str, Any]:
    """Run automated checks against extracted project.

    Decision gate: if tests fail, raises DecisionRequired with options
    ["continue", "abort"]. Uses interrupt() for human triage.
    """
    logger.info("Starting tests...")
    try:
        test_system()
        return {
            "current_stage": "test",
            "stage_results": {
                **state.get("stage_results", {}),
                "test": StageResult(
                    status=StageStatus.PASSED,
                    artifacts=["tests/TEST_RESULTS.md"],
                ),
            },
        }
    except DecisionRequired as exc:
        decision = _handle_decision_gate(exc, state)
        if decision is None:
            return {
                "current_stage": "test",
                "stage_results": {
                    **state.get("stage_results", {}),
                    "test": StageResult(
                        status=StageStatus.PASSED,
                        metadata={"gate_skipped": exc.gate, "had_failures": True},
                    ),
                },
            }

        if decision.selected == "abort":
            return {
                "current_stage": "test",
                "stage_results": {
                    **state.get("stage_results", {}),
                    "test": StageResult(
                        status=StageStatus.FAILED,
                        error="Aborted by human decision",
                        metadata={"decision": "abort"},
                    ),
                },
                "decisions": {
                    **state.get("decisions", {}),
                    exc.gate: decision,
                },
                "error": "Pipeline aborted at test triage",
            }

        # "continue" — re-run test to get a clean result with decision in place
        logger.info("Continuing past test failures per human decision")
        test_system()
        return {
            "current_stage": "test",
            "stage_results": {
                **state.get("stage_results", {}),
                "test": StageResult(
                    status=StageStatus.PASSED,
                    artifacts=["tests/TEST_RESULTS.md"],
                    metadata={"decision": "continue", "had_failures": True},
                ),
            },
            "decisions": {
                **state.get("decisions", {}),
                exc.gate: decision,
            },
        }
    except Exception as e:
        logger.error("Test stage failed: %s", e)
        return {
            "current_stage": "test",
            "stage_results": {
                **state.get("stage_results", {}),
                "test": StageResult(
                    status=StageStatus.FAILED,
                    error=str(e),
                ),
            },
            "error": f"Test stage failed: {e}",
        }


def verify_node(state: PipelineState) -> dict[str, Any]:
    """Assess test evidence and produce go/no-go recommendation.

    Decision gate: if verification result is REJECTED, raises DecisionRequired
    with options ["accept", "reject"]. Uses interrupt() for human review.
    """
    logger.info("Starting verification...")
    try:
        verify_system()
        return {
            "current_stage": "verify",
            "stage_results": {
                **state.get("stage_results", {}),
                "verify": StageResult(
                    status=StageStatus.PASSED,
                    artifacts=["tests/VERIFICATION.md"],
                ),
            },
        }
    except DecisionRequired as exc:
        decision = _handle_decision_gate(exc, state)
        if decision is None:
            return {
                "current_stage": "verify",
                "stage_results": {
                    **state.get("stage_results", {}),
                    "verify": StageResult(
                        status=StageStatus.PASSED,
                        metadata={"gate_skipped": exc.gate},
                    ),
                },
            }

        if decision.selected == "reject":
            return {
                "current_stage": "verify",
                "stage_results": {
                    **state.get("stage_results", {}),
                    "verify": StageResult(
                        status=StageStatus.FAILED,
                        error="Rejected by human reviewer",
                        metadata={"decision": "reject"},
                    ),
                },
                "decisions": {
                    **state.get("decisions", {}),
                    exc.gate: decision,
                },
                "error": "Verification rejected",
            }

        # "accept" — mark as passed with override noted
        logger.info("Verification accepted by human override")
        return {
            "current_stage": "verify",
            "stage_results": {
                **state.get("stage_results", {}),
                "verify": StageResult(
                    status=StageStatus.PASSED,
                    artifacts=["tests/VERIFICATION.md"],
                    metadata={"decision": "accept", "human_override": True},
                ),
            },
            "decisions": {
                **state.get("decisions", {}),
                exc.gate: decision,
            },
        }
    except Exception as e:
        logger.error("Verification failed: %s", e)
        return {
            "current_stage": "verify",
            "stage_results": {
                **state.get("stage_results", {}),
                "verify": StageResult(
                    status=StageStatus.FAILED,
                    error=str(e),
                ),
            },
            "error": f"Verification failed: {e}",
        }


def complete_node(state: PipelineState) -> dict[str, Any]:
    """Terminal node — pipeline completed successfully.

    Sends notification and logs final trace entry.
    """
    notify("Autonomous build flow completed (LangGraph).")
    logger.info("Pipeline completed successfully. Run: %s", state.get("run_id", "unknown"))
    return {
        "current_stage": "complete",
    }
