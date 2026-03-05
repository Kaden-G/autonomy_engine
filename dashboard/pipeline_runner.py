"""Prefect-free pipeline runner for dashboard-launched builds.

Calls task functions directly via their ``.fn`` attribute (bypasses @task
decorator) and replaces the Prefect-dependent ``handle_gate`` with a
local version that only supports auto/skip policies (the dashboard cannot
handle Prefect's ``pause_flow_run``).

Usage (from project root):
    python -m dashboard.pipeline_runner --project-dir /path/to/project
"""

import argparse
import logging
import sys

from engine.context import get_state_dir, init as init_context
from engine.tracer import init_run

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")


# ── Required intake artifacts ────────────────────────────────────────────────

REQUIRED_INPUTS = [
    "inputs/project_spec.yml",
    "inputs/REQUIREMENTS.md",
    "inputs/CONSTRAINTS.md",
    "inputs/NON_GOALS.md",
    "inputs/ACCEPTANCE_CRITERIA.md",
]


def _verify_intake() -> None:
    state_dir = get_state_dir()
    missing = [f for f in REQUIRED_INPUTS if not (state_dir / f).exists()]
    if missing:
        raise RuntimeError(
            "Intake has not been completed. Missing: " + ", ".join(missing)
        )


# ── Prefect-free gate handler ────────────────────────────────────────────────

def _handle_gate(task_fn, stage: str) -> None:
    """Run a @task-decorated function via .fn(), applying auto/skip on gates."""
    from engine.decision_gates import DecisionRequired, get_gate_policy, save_decision

    try:
        task_fn.fn()
    except DecisionRequired as exc:
        policy = get_gate_policy(stage)
        logger.info(
            "DecisionRequired at %s (gate=%s), policy=%s",
            stage, exc.gate, policy.policy,
        )

        if policy.policy == "skip":
            logger.info("Skipping gate '%s' per policy", exc.gate)
            return

        if policy.policy == "pause":
            logger.warning(
                "Gate '%s' has pause policy but dashboard cannot pause. "
                "Auto-selecting first option instead.",
                exc.gate,
            )

        # auto or fallback from pause
        selected = policy.default_option or exc.options[0]
        logger.info("Auto-selecting '%s' for gate '%s'", selected, exc.gate)
        save_decision(
            gate=exc.gate,
            stage=exc.stage,
            allowed_options=exc.options,
            selected=selected,
            actor="dashboard-auto",
        )
        task_fn.fn()


# ── Main pipeline ────────────────────────────────────────────────────────────

def run_pipeline(project_dir: str) -> None:
    """Execute the full build pipeline without Prefect orchestration."""
    from engine.notifier import notify
    from tasks.bootstrap import bootstrap_project
    from tasks.design import design_system
    from tasks.extract import extract_project
    from tasks.implement import implement_system
    from tasks.test import test_system
    from tasks.verify import verify_system

    init_context(project_dir)
    _verify_intake()
    run_id = init_run()
    logger.info("Run %s started (dashboard mode).", run_id)

    logger.info("Starting bootstrap...")
    bootstrap_project.fn()

    logger.info("Starting design...")
    _handle_gate(design_system, "design")

    logger.info("Starting implementation...")
    _handle_gate(implement_system, "implement")

    logger.info("Starting extraction...")
    extract_project.fn()

    logger.info("Starting tests...")
    _handle_gate(test_system, "test")

    logger.info("Starting verification...")
    _handle_gate(verify_system, "verify")

    notify("Autonomous build flow completed (dashboard mode).")
    logger.info("Flow completed successfully.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run pipeline without Prefect")
    parser.add_argument("--project-dir", required=True)
    args = parser.parse_args()
    try:
        run_pipeline(args.project_dir)
    except Exception:
        logger.exception("Pipeline failed")
        sys.exit(1)
