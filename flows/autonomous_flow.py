"""Main Prefect flow — THE entry point. Catches DecisionRequired exceptions.

Supports environment-specific config files and graceful shutdown via
SIGTERM/SIGINT.  See ``_setup_signal_handlers`` and ``_load_config``
for details.
"""

import argparse
import logging
import os
import signal
import sys

from dotenv import load_dotenv

load_dotenv()

from engine.log_config import configure_logging

configure_logging()

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
from engine.tracer import init_run, trace
from tasks.bootstrap import bootstrap_project
from tasks.design import design_system
from tasks.implement import implement_system
from tasks.test import test_system
from tasks.verify import verify_system
from tasks.extract import extract_project

logger = logging.getLogger(__name__)

# ── Graceful shutdown ─────────────────────────────────────────────────────────
# Container orchestrators (ECS, K8s) send SIGTERM before killing the process.
# Without a handler, the audit trail would be left incomplete.  We catch the
# signal, log a trace entry so the HMAC chain stays valid, and exit cleanly.
#
# Why not just let Prefect handle it?  Prefect cancels tasks, but doesn't know
# about our tracer.  The trace("shutdown") entry closes the audit chain.

_SHUTTING_DOWN = False


def _shutdown_handler(signum: int, _frame) -> None:
    """Handle SIGTERM/SIGINT: log an audit entry and exit cleanly."""
    global _SHUTTING_DOWN
    if _SHUTTING_DOWN:
        # Second signal — force exit
        logger.warning("Forced shutdown (second signal). Exiting immediately.")
        sys.exit(1)

    _SHUTTING_DOWN = True
    sig_name = signal.Signals(signum).name
    logger.info("Received %s — shutting down gracefully...", sig_name)

    # Best-effort trace entry so the audit chain records the interruption.
    # If init_run() was never called, this will silently fail (no-op).
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


# ── Environment-aware config loading ─────────────────────────────────────────
# Supports three modes:
#   1. AE_CONFIG_PATH env var → use that exact file
#   2. AE_ENV env var (e.g. "production") → use config.<env>.yml
#   3. Neither → use config.yml (default, backward compatible)
#
# This lets you maintain config.dev.yml, config.staging.yml, config.production.yml
# side by side without code changes.  Container deploys just set AE_ENV.


def _load_config(project_root) -> dict:
    """Load the appropriate config file based on environment.

    Resolution order:
        1. ``AE_CONFIG_PATH`` — explicit path to a config file
        2. ``AE_ENV`` — looks for ``config.<env>.yml`` next to config.yml
        3. Falls back to ``config.yml``
    """
    import yaml

    explicit_path = os.environ.get("AE_CONFIG_PATH")
    if explicit_path:
        config_path = project_root / explicit_path
        if not config_path.exists():
            raise FileNotFoundError(
                f"AE_CONFIG_PATH={explicit_path} does not exist at {config_path}"
            )
        logger.info("Loading config from AE_CONFIG_PATH: %s", config_path)
    else:
        env_name = os.environ.get("AE_ENV", "").strip().lower()
        if env_name:
            config_path = project_root / f"config.{env_name}.yml"
            if not config_path.exists():
                raise FileNotFoundError(
                    f"AE_ENV={env_name} but config.{env_name}.yml not found at {config_path}. "
                    f"Create it or unset AE_ENV to use the default config.yml."
                )
            logger.info("Loading config for AE_ENV=%s: %s", env_name, config_path)
        else:
            config_path = project_root / "config.yml"
            if not config_path.exists():
                logger.warning("No config.yml found at %s — using defaults.", project_root)
                return {}

    with open(config_path) as f:
        return yaml.safe_load(f) or {}

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

    # Install signal handlers BEFORE any work — so even init failures
    # get a clean shutdown trace.
    _setup_signal_handlers()

    # Initialize project context
    init_context(project_dir)

    # Phase 0 gate: intake must be complete
    _verify_intake()

    # Initialize run — creates state/runs/<run_id>/ and resets hash chain.
    # Must happen before any task that calls trace().
    run_id = init_run()
    logger.info("Run %s started.", run_id)

    # Load environment-aware config (respects AE_CONFIG_PATH and AE_ENV)
    project_root = get_state_dir().parent
    full_cfg = _load_config(project_root)
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
