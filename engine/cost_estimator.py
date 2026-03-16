"""Upfront cost estimation and tier selection for pipeline runs.

Estimates token usage per LLM stage using input-size heuristics,
then presents the user with Premium vs MVP tier options including
projected costs and trade-offs.

Usage::

    from engine.cost_estimator import estimate_run, present_estimate
    estimate = estimate_run(project_dir)
    tier = present_estimate(estimate)  # returns chosen Tier
"""

from __future__ import annotations

import logging
import textwrap
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import yaml

from engine.context import get_state_dir, get_config_path, get_prompts_dir
from engine.llm_provider import get_model_limit, resolve_max_tokens

logger = logging.getLogger(__name__)

# ── Pricing per 1M tokens (USD) ────────────────────────────────────────────
# Updated as of 2025-05.  Add new models as they launch.

_PRICING: dict[str, dict[str, float]] = {
    # Claude models — prefix-matched (same as llm_provider)
    "claude-opus-4":     {"input": 15.00, "output": 75.00},
    "claude-sonnet-4":   {"input":  3.00, "output": 15.00},
    "claude-haiku-4":    {"input":  0.80, "output":  4.00},
    "claude-3-5-sonnet": {"input":  3.00, "output": 15.00},
    "claude-3-5-haiku":  {"input":  0.80, "output":  4.00},
    "claude-3-opus":     {"input": 15.00, "output": 75.00},
    # OpenAI models
    "gpt-4o":            {"input":  2.50, "output": 10.00},
    "gpt-4o-mini":       {"input":  0.15, "output":  0.60},
    "gpt-4-turbo":       {"input": 10.00, "output": 30.00},
    "o1":                {"input": 15.00, "output": 60.00},
    "o3-mini":           {"input":  1.10, "output":  4.40},
}

# ── Heuristic multipliers ──────────────────────────────────────────────────
# Ratio of estimated output tokens to input tokens, per stage.
# Derived from observed pipeline runs.  "design" produces moderate
# output; "implement" is the big one (full code); "verify" is brief.

_OUTPUT_MULTIPLIERS: dict[str, float] = {
    "design":    3.0,   # architecture doc ~3x the input prompt
    "implement": 6.0,   # full code + manifest is largest output
    "verify":    1.5,   # concise verdict against evidence
}

# ── Tier definitions ───────────────────────────────────────────────────────

class TierName(str, Enum):
    PREMIUM = "premium"
    MVP     = "mvp"


# Per-stage max_tokens multiplier relative to the model hard limit.
# Premium uses the full config budget; MVP caps each stage lower.
_TIER_STAGE_CAPS: dict[TierName, dict[str, float]] = {
    TierName.PREMIUM: {
        "design":    1.0,   # full budget
        "implement": 1.0,
        "verify":    1.0,
    },
    TierName.MVP: {
        "design":    0.25,  # 25% of budget — concise architecture
        "implement": 0.40,  # 40% — leaner code, fewer files
        "verify":    0.25,  # 25% — brief verdict
    },
}

_TIER_DESCRIPTIONS: dict[TierName, dict] = {
    TierName.PREMIUM: {
        "label": "Premium (Optimized)",
        "summary": "Full-detail architecture, comprehensive implementation with "
                   "tests/docs/configs, and thorough verification report.",
        "includes": [
            "Detailed architecture with rationale and alternatives considered",
            "Complete implementation with error handling, logging, and edge cases",
            "Configuration files, Dockerfiles, CI/CD scaffolding",
            "Comprehensive verification with per-criterion analysis",
        ],
    },
    TierName.MVP: {
        "label": "MVP (Minimum Viable)",
        "summary": "Lean architecture, core implementation only, and concise "
                   "pass/fail verification.",
        "includes": [
            "Concise architecture — one clear approach, minimal rationale",
            "Core implementation — happy-path logic, basic structure",
            "No extras (no Docker, CI/CD, or advanced configs)",
            "Brief verification — pass/fail per criterion, no deep analysis",
        ],
        "trade_offs": [
            "Less robust error handling and edge-case coverage",
            "Fewer generated files (no ancillary configs or docs)",
            "Architecture rationale is abbreviated — less useful for future reference",
            "Verification won't explain *why* something failed in detail",
        ],
    },
}


# ── Data classes ───────────────────────────────────────────────────────────

@dataclass
class StageEstimate:
    stage: str
    input_tokens: int
    output_tokens_premium: int
    output_tokens_mvp: int
    model: str
    uses_llm: bool = True
    chunked: bool = False         # True when implement auto-chunks
    estimated_chunks: int = 1     # number of LLM calls for this stage

    @property
    def total_premium(self) -> int:
        return self.input_tokens + self.output_tokens_premium

    @property
    def total_mvp(self) -> int:
        return self.input_tokens + self.output_tokens_mvp


@dataclass
class RunEstimate:
    provider: str
    stages: list[StageEstimate] = field(default_factory=list)
    config_max_tokens: int = 16384
    cache_hits: list[str] = field(default_factory=list)

    @property
    def total_input_tokens(self) -> int:
        return sum(s.input_tokens for s in self.stages if s.uses_llm)

    def total_output_tokens(self, tier: TierName) -> int:
        if tier == TierName.PREMIUM:
            return sum(s.output_tokens_premium for s in self.stages if s.uses_llm)
        return sum(s.output_tokens_mvp for s in self.stages if s.uses_llm)

    def cost_usd(self, tier: TierName) -> float:
        """Estimated cost in USD for the given tier."""
        total_in = self.total_input_tokens
        total_out = self.total_output_tokens(tier)
        # Use the first LLM stage's model for pricing (they're usually the same)
        model = next((s.model for s in self.stages if s.uses_llm), "")
        pricing = _resolve_pricing(model)
        return (total_in / 1_000_000 * pricing["input"]
                + total_out / 1_000_000 * pricing["output"])


@dataclass
class Tier:
    name: TierName
    max_tokens_per_stage: dict[str, int]
    estimated_cost_usd: float


# ── Helpers ────────────────────────────────────────────────────────────────

def _resolve_pricing(model: str) -> dict[str, float]:
    """Prefix-match model to pricing table, like llm_provider does for limits."""
    best_key, best_len = None, 0
    for key in _PRICING:
        if model.startswith(key) and len(key) > best_len:
            best_key, best_len = key, len(key)
    if best_key:
        return _PRICING[best_key]
    logger.warning("No pricing data for model '%s' — using $0 estimate", model)
    return {"input": 0.0, "output": 0.0}


def _estimate_tokens(text: str) -> int:
    """Fast heuristic: ~1 token per 4 characters for English text.

    This avoids importing tiktoken (which pulls ~100 MB of data) and is
    accurate within ~15% for typical English/code content.
    """
    return max(1, len(text) // 4)


def _read_if_exists(path: Path) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8", errors="replace")
    return ""


# ── Core estimation ────────────────────────────────────────────────────────

def estimate_run(project_dir: str | Path | None = None) -> RunEstimate:
    """Scan project inputs and estimate token usage per stage.

    Returns a RunEstimate with per-stage breakdowns for both Premium
    and MVP tiers.  Does NOT call any LLM — purely local heuristics.
    """
    config_path = str(get_config_path())
    with open(config_path) as f:
        config = yaml.safe_load(f)

    llm_cfg = config["llm"]
    provider = llm_cfg["provider"]
    budget = llm_cfg.get("max_tokens", 16384)

    state = get_state_dir()
    prompts = get_prompts_dir()

    # Read all intake artifacts
    requirements = _read_if_exists(state / "inputs" / "REQUIREMENTS.md")
    constraints = _read_if_exists(state / "inputs" / "CONSTRAINTS.md")
    non_goals = _read_if_exists(state / "inputs" / "NON_GOALS.md")
    acceptance = _read_if_exists(state / "inputs" / "ACCEPTANCE_CRITERIA.md")

    estimate = RunEstimate(provider=provider, config_max_tokens=budget)

    # Check for cached responses
    cache_dir = state / "cache" / "llm"
    # (Cache detection is best-effort; we note it but still estimate full cost)

    # ── Design stage ────────────────────────────────────────────────────
    design_model = _resolve_model(llm_cfg, "design")
    design_template = _read_if_exists(prompts / "design.txt")
    design_prompt = design_template + requirements + constraints + non_goals
    design_input_tokens = _estimate_tokens(design_prompt)

    design_hard_limit = get_model_limit(provider, design_model)
    design_mvp_budget = min(budget, design_hard_limit)

    estimate.stages.append(StageEstimate(
        stage="design",
        input_tokens=design_input_tokens,
        output_tokens_premium=min(
            int(design_input_tokens * _OUTPUT_MULTIPLIERS["design"]),
            design_hard_limit,  # Premium uses model's full capacity
        ),
        output_tokens_mvp=min(
            int(design_input_tokens * _OUTPUT_MULTIPLIERS["design"]
                * _TIER_STAGE_CAPS[TierName.MVP]["design"]),
            int(design_mvp_budget * _TIER_STAGE_CAPS[TierName.MVP]["design"]),
        ),
        model=design_model,
    ))

    # ── Implement stage ─────────────────────────────────────────────────
    # At estimation time we don't have the architecture yet, so we
    # estimate it as the design output + inputs.
    impl_model = _resolve_model(llm_cfg, "implement")
    impl_template = _read_if_exists(prompts / "implement.txt")
    estimated_arch_size = estimate.stages[0].output_tokens_premium * 4  # chars
    impl_prompt = impl_template + estimated_arch_size * "x" + requirements + constraints
    impl_input_tokens = _estimate_tokens(impl_prompt)

    impl_hard_limit = get_model_limit(provider, impl_model)
    impl_mvp_budget = min(budget, impl_hard_limit)

    # Detect if chunking will trigger (output > 85% of budget)
    raw_premium_output = int(impl_input_tokens * _OUTPUT_MULTIPLIERS["implement"])
    will_chunk = raw_premium_output > int(impl_hard_limit * 0.85)

    if will_chunk:
        # Chunked: estimate ~3-5 components, each gets its own call.
        # Input tokens multiply (each chunk re-sends the architecture),
        # but output per chunk is bounded by the model limit.
        est_chunks = max(2, raw_premium_output // impl_hard_limit + 1)
        chunk_input = impl_input_tokens * est_chunks  # arch resent each time
        # Plus one planning call (small)
        plan_overhead = _estimate_tokens(
            _read_if_exists(prompts / "implement_plan.txt") + estimated_arch_size * "x"
        )
        total_input = chunk_input + plan_overhead
        # Output: each chunk can produce up to hard_limit tokens
        total_premium_output = impl_hard_limit * est_chunks
        total_mvp_output = int(
            impl_mvp_budget
            * _TIER_STAGE_CAPS[TierName.MVP]["implement"]
            * est_chunks
        )
        logger.info(
            "Implement stage will chunk: ~%d components, est. %d premium output tokens",
            est_chunks, total_premium_output,
        )
    else:
        est_chunks = 1
        total_input = impl_input_tokens
        total_premium_output = min(raw_premium_output, impl_hard_limit)
        total_mvp_output = min(
            int(impl_input_tokens * _OUTPUT_MULTIPLIERS["implement"]
                * _TIER_STAGE_CAPS[TierName.MVP]["implement"]),
            int(impl_mvp_budget * _TIER_STAGE_CAPS[TierName.MVP]["implement"]),
        )

    estimate.stages.append(StageEstimate(
        stage="implement",
        input_tokens=total_input,
        output_tokens_premium=total_premium_output,
        output_tokens_mvp=total_mvp_output,
        model=impl_model,
        chunked=will_chunk,
        estimated_chunks=est_chunks,
    ))

    # ── Verify stage ────────────────────────────────────────────────────
    verify_model = _resolve_model(llm_cfg, "verify")
    verify_template = _read_if_exists(prompts / "verify.txt")
    # Evidence is unknown at estimation time; use a rough placeholder
    estimated_evidence = 2000  # chars — typical for a few check results
    verify_prompt = verify_template + "x" * estimated_evidence + acceptance + requirements
    verify_input_tokens = _estimate_tokens(verify_prompt)

    verify_hard_limit = get_model_limit(provider, verify_model)
    verify_mvp_budget = min(budget, verify_hard_limit)

    estimate.stages.append(StageEstimate(
        stage="verify",
        input_tokens=verify_input_tokens,
        output_tokens_premium=min(
            int(verify_input_tokens * _OUTPUT_MULTIPLIERS["verify"]),
            verify_hard_limit,  # Premium uses model's full capacity
        ),
        output_tokens_mvp=min(
            int(verify_input_tokens * _OUTPUT_MULTIPLIERS["verify"]
                * _TIER_STAGE_CAPS[TierName.MVP]["verify"]),
            int(verify_mvp_budget * _TIER_STAGE_CAPS[TierName.MVP]["verify"]),
        ),
        model=verify_model,
    ))

    # Non-LLM stages for completeness
    for stage_name in ("bootstrap", "extract", "test"):
        estimate.stages.append(StageEstimate(
            stage=stage_name,
            input_tokens=0,
            output_tokens_premium=0,
            output_tokens_mvp=0,
            model="",
            uses_llm=False,
        ))

    return estimate


def build_tiers(estimate: RunEstimate) -> dict[TierName, Tier]:
    """Compute concrete Tier objects with per-stage max_tokens and cost.

    - **Premium** uses the model's full hard limit — the user is asking
      for the best possible output and accepts the cost.
    - **MVP** uses the config budget (``max_tokens`` from config.yml)
      scaled down by per-stage fractions — cheap and lean.
    """
    tiers = {}
    for tier_name in TierName:
        per_stage: dict[str, int] = {}
        for se in estimate.stages:
            if not se.uses_llm:
                continue
            hard_limit = get_model_limit(estimate.provider, se.model)

            if tier_name == TierName.PREMIUM:
                # Premium: use the model's actual capacity
                base_budget = hard_limit
            else:
                # MVP: use the conservative config budget
                base_budget = min(estimate.config_max_tokens, hard_limit)

            cap_fraction = _TIER_STAGE_CAPS[tier_name].get(se.stage, 1.0)
            per_stage[se.stage] = max(1024, int(base_budget * cap_fraction))

        tiers[tier_name] = Tier(
            name=tier_name,
            max_tokens_per_stage=per_stage,
            estimated_cost_usd=estimate.cost_usd(tier_name),
        )
    return tiers


def _resolve_model(llm_cfg: dict, stage: str) -> str:
    """Resolve the model name for a given stage from config."""
    provider = llm_cfg["provider"]
    if "models" in llm_cfg and stage in llm_cfg["models"]:
        return llm_cfg["models"][stage]
    return llm_cfg[provider]["model"]


# ── Presentation ───────────────────────────────────────────────────────────

def format_estimate(estimate: RunEstimate) -> str:
    """Return a human-readable cost comparison table."""
    tiers = build_tiers(estimate)
    premium = tiers[TierName.PREMIUM]
    mvp = tiers[TierName.MVP]
    desc_p = _TIER_DESCRIPTIONS[TierName.PREMIUM]
    desc_m = _TIER_DESCRIPTIONS[TierName.MVP]

    lines = [
        "",
        "=" * 68,
        "  PIPELINE COST ESTIMATE",
        "=" * 68,
        "",
    ]

    # Per-stage breakdown
    lines.append(f"  {'Stage':<14} {'Model':<30} {'Input Tokens':>13}")
    lines.append(f"  {'-'*14} {'-'*30} {'-'*13}")
    for se in estimate.stages:
        if se.uses_llm:
            chunk_note = f"  ({se.estimated_chunks} chunks)" if se.chunked else ""
            lines.append(
                f"  {se.stage:<14} {se.model:<30} {se.input_tokens:>13,}{chunk_note}"
            )
    lines.append("")

    # Tier comparison
    lines.append("-" * 68)
    lines.append(f"  OPTION A: {desc_p['label']}")
    lines.append(f"  {desc_p['summary']}")
    lines.append("")
    lines.append(f"    Estimated output tokens:  {estimate.total_output_tokens(TierName.PREMIUM):>10,}")
    lines.append(f"    Estimated total tokens:   {estimate.total_input_tokens + estimate.total_output_tokens(TierName.PREMIUM):>10,}")
    lines.append(f"    Estimated cost:           ${premium.estimated_cost_usd:>9.4f}")
    lines.append("")
    lines.append("    Includes:")
    for item in desc_p["includes"]:
        lines.append(f"      + {item}")

    lines.append("")
    lines.append("-" * 68)
    lines.append(f"  OPTION B: {desc_m['label']}")
    lines.append(f"  {desc_m['summary']}")
    lines.append("")
    lines.append(f"    Estimated output tokens:  {estimate.total_output_tokens(TierName.MVP):>10,}")
    lines.append(f"    Estimated total tokens:   {estimate.total_input_tokens + estimate.total_output_tokens(TierName.MVP):>10,}")
    lines.append(f"    Estimated cost:           ${mvp.estimated_cost_usd:>9.4f}")
    lines.append("")
    lines.append("    Includes:")
    for item in desc_m["includes"]:
        lines.append(f"      + {item}")
    lines.append("")
    lines.append("    Trade-offs vs Premium:")
    for item in desc_m.get("trade_offs", []):
        lines.append(f"      - {item}")

    lines.append("")
    lines.append("-" * 68)
    savings = premium.estimated_cost_usd - mvp.estimated_cost_usd
    if premium.estimated_cost_usd > 0:
        pct = (savings / premium.estimated_cost_usd) * 100
        lines.append(f"  MVP saves ~${savings:.4f} ({pct:.0f}%) vs Premium")
    lines.append("=" * 68)
    lines.append("")

    return "\n".join(lines)


def prompt_tier_selection(estimate: RunEstimate) -> Tier:
    """Print the estimate and prompt the user to choose a tier.

    Returns the selected Tier with concrete per-stage max_tokens.
    Falls back to MVP if input is unrecognized.
    """
    tiers = build_tiers(estimate)

    print(format_estimate(estimate))

    while True:
        choice = input("  Select tier [A=Premium / B=MVP / Q=Quit]: ").strip().upper()
        if choice in ("A", "PREMIUM", "P"):
            logger.info("User selected PREMIUM tier")
            return tiers[TierName.PREMIUM]
        elif choice in ("B", "MVP", "M"):
            logger.info("User selected MVP tier")
            return tiers[TierName.MVP]
        elif choice in ("Q", "QUIT", "EXIT"):
            raise SystemExit("Pipeline cancelled by user.")
        else:
            print("  Please enter A, B, or Q.")
