"""Pipeline runner — launches builds from the dashboard without requiring Prefect.

When you click "Run Pipeline" in the dashboard, this module executes the pipeline
stages directly (bypassing the Prefect orchestrator).  Decision gates operate in
auto/skip mode only — interactive pause-for-human-approval requires the Prefect UI.

After the run completes, it generates a usage report comparing actual token
consumption against the pre-run estimate.

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

def run_pipeline(
    project_dir: str,
    skip_estimate: bool = False,
    tier_name: str | None = None,
) -> None:
    """Execute the full build pipeline without Prefect orchestration.

    *tier_name* can be ``"premium"`` or ``"mvp"``.  When provided the
    cost estimate is computed silently and the matching tier budgets are
    applied — no interactive prompt.  When omitted (and *skip_estimate*
    is False) the user is prompted interactively on stdin.
    """
    from engine.cost_estimator import (
        TierName, build_tiers, estimate_run, prompt_tier_selection,
    )
    from engine.llm_provider import set_stage_token_overrides
    from engine.notifier import notify
    from tasks.bootstrap import bootstrap_project
    from tasks.design import design_system
    from tasks.extract import extract_project
    from tasks.implement import implement_system
    from tasks.test import test_system
    from tasks.verify import verify_system

    init_context(project_dir)
    _verify_intake()

    # ── Cost estimate + tier selection ──────────────────────────────────
    if not skip_estimate:
        estimate = estimate_run(project_dir)

        if tier_name:
            # Non-interactive: tier passed via CLI (e.g. from Streamlit UI)
            selected_tier_name = TierName(tier_name)
            tiers = build_tiers(estimate)
            tier = tiers[selected_tier_name]
        else:
            # Interactive: prompt on stdin (terminal usage)
            tier = prompt_tier_selection(estimate)

        set_stage_token_overrides(tier.max_tokens_per_stage)

        from engine.tier_context import set_tier
        set_tier(tier.name.value)

        logger.info(
            "Tier '%s' selected — estimated cost $%.4f",
            tier.name.value, tier.estimated_cost_usd,
        )

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

    # ── Generate usage report (actual vs projected) ──────────────────
    try:
        from engine.usage_tracker import build_usage_report, save_usage_report
        usage_report = build_usage_report(
            run_id,
            estimate=estimate if not skip_estimate else None,
            tier_name=tier.name.value if not skip_estimate else "",
        )
        save_usage_report(run_id, usage_report)
        logger.info("Usage: %s", usage_report.summary())
    except Exception:
        logger.warning("Could not generate usage report", exc_info=True)

    notify("Autonomous build flow completed (dashboard mode).")
    logger.info("Flow completed successfully.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run pipeline without Prefect")
    parser.add_argument("--project-dir", required=True)
    parser.add_argument(
        "--tier", choices=["premium", "mvp"], default=None,
        help="Select build tier (premium or mvp). Skips interactive prompt.",
    )
    parser.add_argument(
        "--skip-estimate", action="store_true",
        help="Skip cost estimate and tier selection entirely (use config defaults)",
    )
    args = parser.parse_args()
    try:
        run_pipeline(
            args.project_dir,
            skip_estimate=args.skip_estimate,
            tier_name=args.tier,
        )
    except Exception:
        logger.exception("Pipeline failed")
        sys.exit(1)
