"""Main Prefect flow — THE entry point. Catches DecisionRequired exceptions."""

import argparse
import logging

from dotenv import load_dotenv
load_dotenv()

from prefect import flow

from engine.context import get_state_dir, init as init_context
from engine.decision_gates import DecisionRequired, require_decision, save_decision
from engine.notifier import notify
from tasks.bootstrap import bootstrap_project
from tasks.design import design_system
from tasks.implement import implement_system
from tasks.test import test_system
from tasks.verify import verify_system
from tasks.extract import extract_project

logger = logging.getLogger(__name__)

REQUIRED_INPUTS = [
    "inputs/project_spec.yml",
    "inputs/REQUIREMENTS.md",
    "inputs/CONSTRAINTS.md",
    "inputs/NON_GOALS.md",
    "inputs/ACCEPTANCE_CRITERIA.md",
]


def _verify_intake() -> None:
    """Hard gate: refuse to run if intake has not been completed."""
    state_dir = get_state_dir()
    missing = [f for f in REQUIRED_INPUTS if not (state_dir / f).exists()]
    if missing:
        raise RuntimeError(
            "Intake has not been completed. Run intake first:\n"
            "  python -m intake.intake new-project\n"
            f"Missing: {', '.join(missing)}"
        )


@flow(name="Autonomous Build Flow")
def autonomous_build(project_dir: str | None = None) -> None:
    """Run the full autonomous build pipeline with human-in-the-loop gates."""

    # Initialize project context
    init_context(project_dir)

    # Phase 0 gate: intake must be complete
    _verify_intake()

    # Step 1: Bootstrap — verify inputs and initialize trace
    logger.info("Starting bootstrap...")
    bootstrap_project()

    # Step 2: Design — LLM generates architecture
    logger.info("Starting design...")
    _run_with_gate(design_system)

    # Step 3: Implement — LLM generates code from design
    logger.info("Starting implementation...")
    implement_system()

    # Step 4: Test — validate implementation
    logger.info("Starting tests...")
    test_system()

    # Step 5: Verify — check acceptance criteria
    logger.info("Starting verification...")
    verify_system()

    # Step 6: Extract — write implementation files to project folder
    logger.info("Starting extraction...")
    extract_project()

    notify("Autonomous build flow completed.")
    logger.info("Flow completed successfully.")


def _run_with_gate(task_fn) -> None:
    """Run a task, catch DecisionRequired, pause for human input, then re-run."""
    try:
        task_fn()
    except DecisionRequired as e:
        logger.info("Decision required: %s (options: %s)", e.summary, e.options)
        notify(f"Decision required: {e.summary}")

        choice = require_decision(e.summary, e.options)
        logger.info("Decision received: %s -> %s", e.summary, choice)

        save_decision(e.summary, choice)
        task_fn()  # Re-run with decision now available in state/decisions/


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the Autonomous Build Flow")
    parser.add_argument(
        "--project-dir",
        default=None,
        help="Path to an external project directory (default: engine root)",
    )
    cli_args = parser.parse_args()
    autonomous_build(project_dir=cli_args.project_dir)
