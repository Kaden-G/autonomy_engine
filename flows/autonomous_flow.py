"""Main Prefect flow — THE entry point. Catches DecisionRequired exceptions."""

import argparse
import logging

from dotenv import load_dotenv
load_dotenv()

from prefect import flow

from engine.context import get_state_dir, init as init_context
from engine.decision_gates import (
    DecisionRequired,
    handle_gate,
    require_decision,
    save_decision,
)
from engine.notifier import notify
from engine.tracer import init_run
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

    # Initialize run — creates state/runs/<run_id>/ and resets hash chain.
    # Must happen before any task that calls trace().
    run_id = init_run()
    logger.info("Run %s started.", run_id)

    # Step 1: Bootstrap — verify inputs and scaffold directories
    logger.info("Starting bootstrap...")
    bootstrap_project()

    # Step 2: Design — LLM generates architecture
    logger.info("Starting design...")
    handle_gate(design_system, "design", _on_pause)

    # Step 3: Implement — LLM generates code from design
    logger.info("Starting implementation...")
    handle_gate(implement_system, "implement", _on_pause)

    # Step 4: Extract — write implementation files to project folder
    logger.info("Starting extraction...")
    extract_project()

    # Step 5: Test — run approved checks against extracted project
    logger.info("Starting tests...")
    handle_gate(test_system, "test", _on_pause)

    # Step 6: Verify — LLM assesses execution evidence
    logger.info("Starting verification...")
    handle_gate(verify_system, "verify", _on_pause)

    notify("Autonomous build flow completed.")
    logger.info("Flow completed successfully.")


def _on_pause(exc: DecisionRequired) -> None:
    """Handle a pause-policy gate: notify, collect human input, save decision."""
    logger.info("Decision required: %s at %s (options: %s)", exc.gate, exc.stage, exc.options)
    notify(f"Decision required: {exc.gate}")

    decision_input = require_decision(exc.gate, exc.options)
    logger.info("Decision received: %s -> %s", exc.gate, decision_input.choice)

    save_decision(
        gate=exc.gate,
        stage=exc.stage,
        allowed_options=exc.options,
        selected=decision_input.choice,
        rationale=decision_input.rationale,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the Autonomous Build Flow")
    parser.add_argument(
        "--project-dir",
        default=None,
        help="Path to an external project directory (default: engine root)",
    )
    cli_args = parser.parse_args()
    autonomous_build(project_dir=cli_args.project_dir)
