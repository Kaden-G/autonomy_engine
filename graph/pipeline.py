"""Pipeline graph definition — the orchestration backbone.

This module builds the LangGraph StateGraph that replaces Prefect's
autonomous_build flow. The graph defines:
    - Linear progression: init → bootstrap → design → implement → extract → test → verify → complete
    - Conditional edges: test failures can route back to implement (retry loop)
    - Error short-circuits: any stage failure routes to an error terminal
    - Human-in-the-loop: decision gates use interrupt() at design, test, and verify nodes

Architecture decision — why a graph, not a chain:
    A chain (A→B→C→D) can't express "if D fails, go back to B and try again."
    A graph can. The implement→test retry loop is the primary motivator, but the
    graph structure also makes it trivial to add future branches (e.g., parallel
    test runners, alternative implementation strategies).

    The graph is compiled once and can be invoked multiple times with different
    initial states. Each invocation gets its own checkpoint thread, so concurrent
    runs don't interfere.

Checkpointing strategy (NIST AI RMF: MEASURE 2.6 — Resilience):
    We use SqliteSaver for local development and testing. In production,
    this should be swapped to PostgresSaver for durability and concurrent access.
    The checkpointer DB stores serialized PipelineState at every node boundary,
    enabling:
    1. Resume from last successful node after process death
    2. Replay and inspection of historical pipeline states
    3. Time-travel debugging (step back to any node's output)

    Known risk (POAM): SqliteSaver is single-writer. If multiple pipeline runs
    target the same SQLite file concurrently, writes will serialize (slow but safe).
    Remediation: switch to PostgresSaver for multi-tenant deployments.
"""

from __future__ import annotations

import argparse
import logging
from typing import Any, Literal

from dotenv import load_dotenv
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from graph.nodes import (
    bootstrap_node,
    complete_node,
    design_node,
    extract_node,
    implement_node,
    init_node,
    test_node,
    verify_node,
)
from graph.state import PipelineState, StageStatus

logger = logging.getLogger(__name__)


# ── Routing functions ───────────────────────────────────────────────────────
# These are pure functions that inspect state and return the next node name.
# They contain NO side effects — all decisions are based on stage_results.


def route_after_init(state: PipelineState) -> Literal["bootstrap", "__end__"]:
    """Route after initialization — proceed or abort if intake is missing."""
    init_result = state.get("stage_results", {}).get("init")
    if init_result and init_result.status == StageStatus.FAILED:
        return END
    return "bootstrap"


def route_after_bootstrap(state: PipelineState) -> Literal["design", "__end__"]:
    """Route after bootstrap — proceed or abort on failure."""
    result = state.get("stage_results", {}).get("bootstrap")
    if result and result.status == StageStatus.FAILED:
        return END
    return "design"


def route_after_design(state: PipelineState) -> Literal["implement", "__end__"]:
    """Route after design — proceed or abort on failure."""
    result = state.get("stage_results", {}).get("design")
    if result and result.status == StageStatus.FAILED:
        return END
    return "implement"


def route_after_implement(state: PipelineState) -> Literal["extract", "__end__"]:
    """Route after implement — proceed to extraction or abort."""
    result = state.get("stage_results", {}).get("implement")
    if result and result.status == StageStatus.FAILED:
        return END
    return "extract"


def route_after_extract(state: PipelineState) -> Literal["test", "__end__"]:
    """Route after extract — proceed to testing or abort."""
    result = state.get("stage_results", {}).get("extract")
    if result and result.status == StageStatus.FAILED:
        return END
    return "test"


def route_after_test(
    state: PipelineState,
) -> Literal["verify", "implement", "__end__"]:
    """Route after test — the most interesting routing decision.

    Three possible outcomes:
    1. Tests passed → verify
    2. Tests failed + retries remaining → implement (retry loop)
    3. Tests failed + no retries (or aborted) → END

    The retry loop is the key architectural advantage of using a graph.
    In the Prefect flow, retrying required re-running the entire pipeline
    or custom retry logic. Here, it's just an edge.
    """
    result = state.get("stage_results", {}).get("test")
    if result is None:
        return END

    # Explicit abort — no retry
    if result.status == StageStatus.FAILED:
        abort_decision = result.metadata.get("decision") == "abort" if result.metadata else False
        if abort_decision or state.get("error"):
            return END

    # Tests passed (including "continue past failures" decisions)
    if result.status in (StageStatus.PASSED, StageStatus.SKIPPED):
        return "verify"

    # Tests failed — check retry budget
    retry_count = state.get("retry_count", 0)
    max_retries = state.get("max_retries", 1)

    if retry_count < max_retries:
        logger.info(
            "Test failures detected. Retrying implementation (%d/%d).",
            retry_count + 1,
            max_retries,
        )
        return "implement"

    logger.warning(
        "Test failures detected but retry budget exhausted (%d/%d).", retry_count, max_retries
    )
    return END


def route_after_verify(state: PipelineState) -> Literal["complete", "__end__"]:
    """Route after verify — complete or abort."""
    result = state.get("stage_results", {}).get("verify")
    if result and result.status == StageStatus.FAILED:
        return END
    return "complete"


# ── Graph builder ───────────────────────────────────────────────────────────


def build_graph(checkpointer=None) -> Any:
    """Construct and compile the pipeline StateGraph.

    Args:
        checkpointer: LangGraph checkpointer instance. If None, uses MemorySaver
                      (in-memory, suitable for testing). For production, pass
                      SqliteSaver or PostgresSaver.

    Returns:
        Compiled graph (CompiledStateGraph) ready for .invoke() or .stream().

    The graph structure:

        init ──→ bootstrap ──→ design ──→ implement ──→ extract ──→ test ──→ verify ──→ complete
                                              ↑                        │
                                              └────── retry loop ──────┘

    Each arrow is a conditional edge that checks the previous stage's result.
    Any failure short-circuits to END (unless retries are available).
    """
    if checkpointer is None:
        checkpointer = MemorySaver()

    # Define the graph with our typed state schema
    graph = StateGraph(PipelineState)

    # ── Add nodes ───────────────────────────────────────────────────────────
    # Each node is a function: PipelineState → partial state update dict.
    # Node names match the stage names used in stage_results for consistency.
    graph.add_node("init", init_node)
    graph.add_node("bootstrap", bootstrap_node)
    graph.add_node("design", design_node)
    graph.add_node("implement", implement_node)
    graph.add_node("extract", extract_node)
    graph.add_node("test", test_node)
    graph.add_node("verify", verify_node)
    graph.add_node("complete", complete_node)

    # ── Set entry point ─────────────────────────────────────────────────────
    graph.set_entry_point("init")

    # ── Add conditional edges ───────────────────────────────────────────────
    # Each edge inspects state and routes to the next node or END.
    # This is where the graph's power shows — the retry loop from test→implement
    # is just another edge, not special-case code.
    graph.add_conditional_edges("init", route_after_init)
    graph.add_conditional_edges("bootstrap", route_after_bootstrap)
    graph.add_conditional_edges("design", route_after_design)
    graph.add_conditional_edges("implement", route_after_implement)
    graph.add_conditional_edges("extract", route_after_extract)
    graph.add_conditional_edges("test", route_after_test)
    graph.add_conditional_edges("verify", route_after_verify)

    # complete is a terminal node — just goes to END
    graph.add_edge("complete", END)

    # ── Compile ─────────────────────────────────────────────────────────────
    # Compilation validates the graph structure (no orphan nodes, reachable END)
    # and produces an executable CompiledStateGraph.
    compiled = graph.compile(checkpointer=checkpointer)
    logger.info("Pipeline graph compiled successfully.")
    return compiled


# ── Runner ──────────────────────────────────────────────────────────────────


def run_pipeline(
    project_dir: str | None = None,
    checkpointer=None,
    thread_id: str | None = None,
) -> PipelineState:
    """Build and run the pipeline graph.

    This is the primary entry point — the LangGraph equivalent of
    autonomous_build() in the Prefect flow.

    Args:
        project_dir: Path to project directory (None = engine root).
        checkpointer: LangGraph checkpointer. None = MemorySaver (testing).
        thread_id: Checkpoint thread ID for resume support. If None, generates
                   a new one (no resume). Pass the same thread_id to resume
                   a previously interrupted pipeline.

    Returns:
        Final PipelineState dict with all stage results and decisions.

    Example — fresh run:
        >>> result = run_pipeline(project_dir="/path/to/project")
        >>> print(result["stage_results"]["verify"].status)
        StageStatus.PASSED

    Example — resume after interrupt:
        >>> from langgraph.types import Command
        >>> # First run pauses at design gate...
        >>> result = run_pipeline(thread_id="my-run-123")
        >>> # Resume with human decision:
        >>> graph = build_graph(checkpointer=my_checkpointer)
        >>> graph.invoke(
        ...     Command(resume={"choice": "monolith", "rationale": "Simpler for MVP"}),
        ...     config={"configurable": {"thread_id": "my-run-123"}},
        ... )
    """
    load_dotenv()

    graph = build_graph(checkpointer=checkpointer)

    # Initial state — minimal, nodes populate the rest
    initial_state: PipelineState = {
        "project_dir": project_dir,
        "stage_results": {},
        "decisions": {},
        "current_stage": "init",
        "error": None,
        "retry_count": 0,
        "max_retries": 1,
    }

    # Config for checkpointing — thread_id enables resume
    config = {}
    if thread_id:
        config["configurable"] = {"thread_id": thread_id}
    else:
        # Generate a unique thread_id so checkpoints don't collide
        import uuid

        config["configurable"] = {"thread_id": uuid.uuid4().hex[:12]}

    tid = config["configurable"]["thread_id"]
    logger.info(
        "Starting pipeline (thread_id=%s, project_dir=%s)",
        tid,
        project_dir or "engine root",
    )

    result = graph.invoke(initial_state, config=config)

    # Attach thread_id to result so callers can resume interrupted graphs
    result["_thread_id"] = tid

    # Write a machine-readable status file for out-of-process pollers
    # (primarily the dashboard). Cheaper than parsing stdout.
    _write_run_status(result, thread_id=tid)
    return result


def _write_run_status(result: dict, *, thread_id: str) -> None:
    """Persist a status.json describing the run's terminal state.

    One of: ``"complete"``, ``"failed"``, ``"paused"``.  Written to
    ``state/runs/<run_id>/status.json``.  Pollers (the dashboard) read this
    to decide whether to show a gate form, an error, or the success view.
    """
    import json
    from engine.context import get_state_dir

    run_id = result.get("run_id")
    if not run_id:
        return  # Nothing to attach the status to.

    if result.get("__interrupt__"):
        state = "paused"
    elif result.get("error"):
        state = "failed"
    elif result.get("current_stage") == "complete":
        state = "complete"
    else:
        state = "paused"  # Unknown terminal state — treat conservatively.

    payload = {
        "state": state,
        "thread_id": thread_id,
        "current_stage": result.get("current_stage"),
        "error": result.get("error"),
    }
    try:
        path = get_state_dir() / "runs" / run_id / "status.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload))
    except OSError as e:
        logger.warning("Could not write status.json: %s", e)


# ── CLI entry point ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the Autonomous Build Pipeline (LangGraph)")
    parser.add_argument(
        "--project-dir",
        default=None,
        help="Path to an external project directory (default: engine root)",
    )
    parser.add_argument(
        "--thread-id",
        default=None,
        help="Checkpoint thread ID for resume support",
    )
    parser.add_argument(
        "--checkpoint-db",
        default=None,
        help="Path to SQLite checkpoint database (default: in-memory)",
    )
    parser.add_argument(
        "--tier",
        default=None,
        choices=["mvp", "premium"],
        help="Build tier: mvp (cost-conscious) or premium (full output)",
    )
    args = parser.parse_args()

    # Tier must be set before the graph runs — tier_context is read during
    # design/implement prompting.
    if args.tier:
        from engine.tier_context import set_tier

        set_tier(args.tier)

    # Set up checkpointer based on args
    cp = None
    cp_conn = None
    if args.checkpoint_db:
        try:
            import sqlite3

            from langgraph.checkpoint.sqlite import SqliteSaver

            # langgraph-checkpoint-sqlite>=2.x changed `SqliteSaver.from_conn_string`
            # to a context manager (`_GeneratorContextManager`), which `graph.compile`
            # rejects with "Invalid checkpointer". Construct the saver directly from
            # a sqlite3 connection — same pattern dashboard/pages/run_pipeline.py
            # uses for the in-process resume path.
            cp_conn = sqlite3.connect(args.checkpoint_db, check_same_thread=False)
            cp = SqliteSaver(cp_conn)
            logger.info("Using SQLite checkpointer: %s", args.checkpoint_db)
        except ImportError:
            logger.warning(
                "langgraph-checkpoint-sqlite not installed. "
                "Install with: pip install langgraph-checkpoint-sqlite. "
                "Falling back to in-memory checkpointer."
            )

    try:
        result = run_pipeline(
            project_dir=args.project_dir,
            checkpointer=cp,
            thread_id=args.thread_id,
        )
    finally:
        if cp_conn is not None:
            cp_conn.close()

    # Report final status
    if result.get("error"):
        logger.error("Pipeline failed: %s", result["error"])
        exit(1)
    elif result.get("current_stage") == "complete":
        logger.info("Pipeline completed successfully.")
        exit(0)
    else:
        # Graph was interrupted (e.g., human decision required at a gate).
        # With a persistent checkpointer, resume with:
        #   python graph/pipeline.py --thread-id <id> --checkpoint-db <db>
        stage = result.get("current_stage", "unknown")
        tid = result.get("_thread_id", "unknown")
        logger.info(
            "Pipeline paused at '%s' stage — awaiting human decision. "
            "To resume: python graph/pipeline.py --thread-id %s --checkpoint-db <db>",
            stage,
            tid,
        )
        exit(0)
