"""Decision gates — human approval checkpoints in the pipeline.

At critical moments (architecture review, test failure triage, final sign-off),
the pipeline can pause and ask a human to approve before continuing.  This module
manages those checkpoints.

Every decision is recorded as a structured JSON file (who approved, what they
chose, when, and why) — creating an auditable record of human oversight.

Gate behavior is controlled per-stage via a policy file (DECISION_GATES.yml):
    - **pause**: stop and wait for a human to approve or redirect
    - **auto**: automatically select the configured default option
    - **skip**: proceed without stopping

Technical details:
    Decisions live at ``state/runs/<run_id>/decisions/<gate>.json``.
    The selected option is validated against the allowed list at save time.
"""

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import yaml
from prefect import pause_flow_run
from prefect.input import RunInput

from engine.context import get_state_dir, get_templates_dir
from engine.tracer import get_run_id, trace

logger = logging.getLogger(__name__)


# ── Exception ────────────────────────────────────────────────────────────────


class DecisionRequired(Exception):
    """Raised by tasks when a human decision is needed before proceeding."""

    def __init__(self, gate: str, stage: str, options: list[str]):
        self.gate = gate
        self.stage = stage
        self.options = options
        super().__init__(f"Decision required at {stage}: {gate} (options: {options})")


# ── Prefect UI input schema ─────────────────────────────────────────────────


class DecisionInput(RunInput):
    """Schema for human decision input via Prefect UI."""

    choice: str
    rationale: str = ""


# ── Path helpers ─────────────────────────────────────────────────────────────


def _gate_slug(gate: str) -> str:
    """Normalize a gate name to a filesystem-safe slug."""
    return gate.lower().replace(" ", "_")[:60]


def _decision_path(gate: str) -> Path:
    """Return the path to a decision record for the active run."""
    slug = _gate_slug(gate)
    return get_state_dir() / "runs" / get_run_id() / "decisions" / f"{slug}.json"


# ── Actor resolution ──────────────────────────────────────────────────────────


def _resolve_actor(actor: str | None) -> str:
    """Determine the actor identity using a best-effort strategy.

    Priority:
    1. Explicit *actor* parameter (if not None)
    2. ``AE_ACTOR`` environment variable
    3. System username from ``USER`` / ``LOGNAME`` / ``USERNAME``
    4. Fallback ``"human"``
    """
    if actor is not None:
        return actor
    ae_actor = os.environ.get("AE_ACTOR")
    if ae_actor:
        return ae_actor
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or os.environ.get("USERNAME")
    if user:
        return f"human:{user}"
    return "human"


# ── Core API ─────────────────────────────────────────────────────────────────


def require_decision(gate: str, options: list[str]) -> DecisionInput:
    """Pause the flow and wait for a human decision via Prefect UI.

    Only called from flows/autonomous_flow.py — never from tasks directly.
    Returns a ``DecisionInput`` with ``.choice`` and ``.rationale``.
    """
    description = f"**{gate}**\n\nOptions:\n"
    for opt in options:
        description += f"- `{opt}`\n"

    result: DecisionInput = pause_flow_run(
        wait_for_input=DecisionInput,
        timeout=86400,
    )
    return result


def save_decision(
    gate: str,
    stage: str,
    allowed_options: list[str],
    selected: str,
    actor: str | None = None,
    rationale: str = "",
) -> None:
    """Persist a validated decision record to the active run.

    *actor* is resolved via ``_resolve_actor`` — explicit value, ``AE_ACTOR``
    env var, system username, or ``"human"`` fallback.

    Raises ``ValueError`` if *selected* is not in *allowed_options*.
    """
    if selected not in allowed_options:
        raise ValueError(
            f"Invalid choice '{selected}' for gate '{gate}'. Must be one of: {allowed_options}"
        )

    resolved_actor = _resolve_actor(actor)

    record = {
        "run_id": get_run_id(),
        "gate": gate,
        "stage": stage,
        "allowed_options": allowed_options,
        "selected": selected,
        "actor": resolved_actor,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "rationale": rationale,
    }

    path = _decision_path(gate)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2) + "\n")

    # Emit a trace entry for the decision event
    rel_path = str(path.relative_to(get_state_dir()))
    trace(
        task="decision",
        inputs=[],
        outputs=[rel_path],
        extra={
            "gate": gate,
            "stage": stage,
            "selected": selected,
            "actor": resolved_actor,
            "has_rationale": bool(rationale),
        },
    )


def decision_exists(gate: str) -> bool:
    """Check whether a decision has been recorded for *gate* in the active run."""
    return _decision_path(gate).exists()


def load_decision(gate: str) -> dict:
    """Load the decision record for *gate* in the active run.

    Raises ``FileNotFoundError`` if no decision exists for this gate/run.
    """
    path = _decision_path(gate)
    if not path.exists():
        raise FileNotFoundError(f"No decision record for gate '{gate}' in run {get_run_id()}")
    return json.loads(path.read_text())


# ── Gate policies ────────────────────────────────────────────────────────────

_VALID_POLICIES = {"pause", "auto", "skip"}

_DEFAULT_POLICIES = {
    "design": "pause",
    "implement": "skip",
    "test": "skip",
    "verify": "pause",
}


@dataclass(frozen=True)
class GatePolicy:
    """Immutable policy for a single decision gate."""

    stage: str
    policy: str
    default_option: str | None = None
    description: str = ""

    def __post_init__(self):
        if self.policy not in _VALID_POLICIES:
            raise ValueError(
                f"Invalid gate policy '{self.policy}' for stage '{self.stage}'. "
                f"Must be one of: {sorted(_VALID_POLICIES)}"
            )


def get_gate_policy(stage: str) -> GatePolicy:
    """Load the gate policy for *stage* from ``DECISION_GATES.yml``.

    Falls back to built-in defaults if the file is missing, malformed,
    or does not contain an entry for *stage*.  Never crashes.
    """
    default_policy = _DEFAULT_POLICIES.get(stage, "skip")

    try:
        gates_path = get_templates_dir() / "DECISION_GATES.yml"
        data = yaml.safe_load(gates_path.read_text())
        gates = data.get("gates", {})
        if not isinstance(gates, dict):
            logger.warning("DECISION_GATES.yml: 'gates' is not a dict, using defaults")
            return GatePolicy(stage=stage, policy=default_policy)

        if stage not in gates:
            return GatePolicy(stage=stage, policy=default_policy)

        entry = gates[stage]
        return GatePolicy(
            stage=stage,
            policy=entry.get("policy", default_policy),
            default_option=entry.get("default_option"),
            description=entry.get("description", ""),
        )
    except Exception:
        logger.warning(
            "Failed to load DECISION_GATES.yml for stage '%s', using default policy '%s'",
            stage,
            default_policy,
            exc_info=True,
        )
        return GatePolicy(stage=stage, policy=default_policy)


def handle_gate(
    task_fn: Callable[[], None],
    stage: str,
    on_pause: Callable[[DecisionRequired], None],
) -> None:
    """Run *task_fn* and apply the gate policy if ``DecisionRequired`` is raised.

    - **skip**: swallow the exception, return without re-running.
    - **auto**: auto-save a decision using ``default_option`` (or the first
      option if none configured), then re-run the task.
    - **pause**: delegate to *on_pause* (which handles human interaction),
      then re-run the task.
    """
    try:
        task_fn()
        return  # no gate triggered — policy is irrelevant
    except DecisionRequired as exc:
        policy = get_gate_policy(stage)
        logger.info(
            "DecisionRequired at %s (gate=%s), policy=%s",
            stage,
            exc.gate,
            policy.policy,
        )

        if policy.policy == "skip":
            logger.info("Skipping gate '%s' per policy", exc.gate)
            return

        if policy.policy == "auto":
            selected = policy.default_option or exc.options[0]
            logger.info("Auto-selecting '%s' for gate '%s'", selected, exc.gate)
            save_decision(
                gate=exc.gate,
                stage=exc.stage,
                allowed_options=exc.options,
                selected=selected,
                actor="auto-policy",
            )
            task_fn()
            return

        # policy == "pause"
        on_pause(exc)
        task_fn()
