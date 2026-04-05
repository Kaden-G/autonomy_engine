"""Pipeline state schema — the single source of truth flowing through the graph.

Design decision:
    We use TypedDict (not Pydantic) because LangGraph's StateGraph requires it
    for its built-in state merging and checkpointing. TypedDict gives us static
    type checking without runtime validation overhead on every node transition.

    State is intentionally thin — it tracks pipeline metadata and control flow,
    NOT the actual artifacts. Artifacts still live on disk under state/ because:
    1. The HMAC audit trail hashes files on disk, not in-memory objects
    2. The dashboard reads from state/ directly (no engine import)
    3. File-based state is inspectable, debuggable, and survives process death

    Think of PipelineState as the "routing slip" stapled to a package moving
    through a warehouse — it says where the package has been and where it goes
    next, but the actual goods are on the shelves (state/ directory).

Security note (NIST AI RMF: GOVERN 1.1):
    The state object is serialized by LangGraph's checkpointer. It contains
    run metadata (run_id, stage results, decision records) but deliberately
    excludes secrets (API keys, HMAC keys). The checkpointer DB should still
    be access-controlled since it contains decision rationales and prompts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypedDict


class StageStatus(str, Enum):
    """Outcome of a pipeline stage.

    Using an enum instead of raw strings prevents typos from silently
    corrupting control flow ("passed" vs "passd" would be a silent bug
    with strings, a loud NameError with an enum).
    """

    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    AWAITING_DECISION = "awaiting_decision"


@dataclass(frozen=True)
class StageResult:
    """Immutable record of a stage's execution outcome.

    Frozen dataclass because stage results should never be mutated
    after creation — they're part of the audit trail.
    """

    status: StageStatus
    artifacts: list[str] = field(default_factory=list)  # Paths relative to state/
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Decision:
    """Record of a human or auto-policy decision at a gate.

    Maps 1:1 with the existing decision JSON files in
    state/runs/<run_id>/decisions/, but lives in the graph state
    for routing purposes.
    """

    gate: str
    stage: str
    selected: str
    actor: str
    rationale: str = ""


class PipelineState(TypedDict, total=False):
    """State flowing through the LangGraph pipeline.

    Fields:
        project_dir: Absolute path to the project directory (or None for engine root).
                     Set once at graph invocation, never mutated.

        run_id: Unique identifier for this pipeline run. Set by the bootstrap node
                after calling init_run(). Used by all downstream nodes for tracing.

        config: Loaded configuration dict (from config.yml). Passed through state
                so nodes don't need to re-read the file — but the canonical source
                remains the config_snapshot.yml in the run directory.

        stage_results: Map of stage name → StageResult. Each node writes its own
                       entry. Downstream nodes can inspect upstream results for
                       conditional routing (e.g., test failures trigger retry).

        decisions: Map of gate name → Decision. Written by interrupt handlers.
                   Consumed by conditional edges to route the graph.

        current_stage: Name of the currently executing stage. Used by the dashboard
                       for progress display. Updated at the start of each node.

        error: If the pipeline fails, the error message is captured here rather
               than lost in a stack trace. The verify node reads this to include
               failure context in its report.

        retry_count: Number of implement→test retry cycles completed. Bounded by
                     max_retries in config to prevent infinite loops (and infinite
                     LLM bills). Default 0.

        max_retries: Maximum implement→test retry cycles. Default 1 (one retry).
                     Configurable via config.yml. Set to 0 to disable retries.
    """

    # ── Immutable context (set once at invocation) ──────────────────────────
    project_dir: str | None

    # ── Run identity ────────────────────────────────────────────────────────
    run_id: str

    # ── Configuration ───────────────────────────────────────────────────────
    config: dict[str, Any]

    # ── Stage tracking ──────────────────────────────────────────────────────
    stage_results: dict[str, StageResult]
    current_stage: str

    # ── Decision gate state ─────────────────────────────────────────────────
    decisions: dict[str, Decision]

    # ── Error capture ───────────────────────────────────────────────────────
    error: str | None

    # ── Retry loop control ──────────────────────────────────────────────────
    retry_count: int
    max_retries: int
