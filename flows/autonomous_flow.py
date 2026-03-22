"""Main Prefect flow — THE entry point. Catches DecisionRequired exceptions."""

import argparse
import logging

from dotenv import load_dotenv

load_dotenv()

from prefect import flow

from engine.cache import evict_stale_llm_cache
from engine.context import get_state_dir, init as init_context
from engine.decision_gates import (
    DecisionRequired,
    handle_gate,
    require_decision,
    save_decision,
)
from engine.notifier import notify
from engine.sandbox import evict_stale_venv_cache
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

    # Lazy cache eviction — clean up stale entries at the start of each run.
    # Runs are infrequent (minutes–hours apart), so the overhead is negligible.
    # TTLs are configurable via config.yml → cache section.
    import yaml

    cache_cfg = {}
    config_path = get_state_dir().parent / "config.yml"
    if config_path.exists():
        with open(config_path) as f:
            full_cfg = yaml.safe_load(f) or {}
        cache_cfg = full_cfg.get("cache") or {}

    llm_ttl = cache_cfg.get("llm_ttl_days", 30)
    venv_ttl = cache_cfg.get("venv_ttl_days", 7)
    if llm_ttl > 0:
        evict_stale_llm_cache(ttl_days=llm_ttl)
    if venv_ttl > 0:
        evict_stale_venv_cache(ttl_days=venv_ttl)

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
