"""Usage tracker — "how much did that run actually cost vs. what we estimated?"

After a pipeline run completes, this module reads the audit log to find out
how many AI tokens were actually consumed at each stage, then compares that
to the pre-run cost estimate.  This gives operators visibility into:

    - Actual cost per stage (design, implement, verify)
    - Total cost vs. the projection shown before the run started
    - Whether caching saved any API calls

Usage:
    from engine.usage_tracker import build_usage_report
    report = build_usage_report(run_id, estimate)
    print(report.summary())
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from engine.context import get_state_dir
from engine.model_registry import get_model_pricing

logger = logging.getLogger(__name__)

# ── LLM stages (non-LLM stages like bootstrap/extract/test are excluded) ────
_LLM_STAGES = {"design", "implement", "verify"}


@dataclass
class StageUsage:
    """Actual token usage for one pipeline stage."""

    stage: str
    input_tokens: int = 0
    output_tokens: int = 0
    llm_calls: int = 0
    cache_hit: bool = False
    model: str = ""

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class UsageReport:
    """Full pipeline usage report — actual vs estimated."""

    run_id: str
    tier: str = ""
    stages: list[StageUsage] = field(default_factory=list)

    # Projections (filled from estimate if available)
    projected_input_tokens: int = 0
    projected_output_tokens: int = 0
    projected_cost_usd: float = 0.0

    @property
    def actual_input_tokens(self) -> int:
        return sum(s.input_tokens for s in self.stages)

    @property
    def actual_output_tokens(self) -> int:
        return sum(s.output_tokens for s in self.stages)

    @property
    def actual_total_tokens(self) -> int:
        return self.actual_input_tokens + self.actual_output_tokens

    @property
    def projected_total_tokens(self) -> int:
        return self.projected_input_tokens + self.projected_output_tokens

    @property
    def total_llm_calls(self) -> int:
        return sum(s.llm_calls for s in self.stages)

    def actual_cost_usd(self, pricing: dict[str, float] | None = None) -> float:
        """Compute actual cost in USD.

        *pricing* is ``{"input": <per_1M>, "output": <per_1M>}``.
        If not provided, uses the model from the first stage to look up pricing.
        """
        if pricing is None:
            pricing = _resolve_pricing_for_model(
                next((s.model for s in self.stages if s.model), "")
            )
        return (
            self.actual_input_tokens / 1_000_000 * pricing["input"]
            + self.actual_output_tokens / 1_000_000 * pricing["output"]
        )

    def savings_pct(self) -> float | None:
        """Percentage of projected tokens actually used (lower = more savings)."""
        if self.projected_total_tokens == 0:
            return None
        return (self.actual_total_tokens / self.projected_total_tokens) * 100

    def to_dict(self) -> dict:
        pricing = _resolve_pricing_for_model(next((s.model for s in self.stages if s.model), ""))
        return {
            "run_id": self.run_id,
            "tier": self.tier,
            "actual": {
                "input_tokens": self.actual_input_tokens,
                "output_tokens": self.actual_output_tokens,
                "total_tokens": self.actual_total_tokens,
                "cost_usd": round(self.actual_cost_usd(pricing), 6),
                "llm_calls": self.total_llm_calls,
            },
            "projected": {
                "input_tokens": self.projected_input_tokens,
                "output_tokens": self.projected_output_tokens,
                "total_tokens": self.projected_total_tokens,
                "cost_usd": round(self.projected_cost_usd, 6),
            },
            "stages": [
                {
                    "stage": s.stage,
                    "input_tokens": s.input_tokens,
                    "output_tokens": s.output_tokens,
                    "total_tokens": s.total_tokens,
                    "llm_calls": s.llm_calls,
                    "cache_hit": s.cache_hit,
                    "model": s.model,
                }
                for s in self.stages
            ],
        }

    def summary(self) -> str:
        """Human-readable one-line summary."""
        actual_cost = self.actual_cost_usd()
        pct = self.savings_pct()
        pct_str = f" ({pct:.0f}% of projection)" if pct is not None else ""
        return (
            f"Run {self.run_id} [{self.tier}]: "
            f"{self.actual_total_tokens:,} tokens "
            f"(in:{self.actual_input_tokens:,} + out:{self.actual_output_tokens:,}), "
            f"${actual_cost:.4f}{pct_str}, "
            f"{self.total_llm_calls} LLM call(s)"
        )


# ── Pricing ──────────────────────────────────────────────────────────────────
# Delegates to engine.model_registry so pricing stays in sync with models.yml
# (cost_estimator uses the same path — single source of truth).


def _resolve_pricing_for_model(model: str) -> dict[str, float]:
    """Look up per-1M-token pricing from the shared models.yml registry."""
    return get_model_pricing(model) if model else {"input": 0.0, "output": 0.0}


# ── Core functions ────────────────────────────────────────────────────────────


def load_trace_entries(run_id: str) -> list[dict]:
    """Load all trace entries from a run's trace.jsonl."""
    path = get_state_dir() / "runs" / run_id / "trace.jsonl"
    if not path.exists():
        return []
    entries = []
    for line in path.read_text().strip().splitlines():
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def extract_usage_from_traces(entries: list[dict]) -> list[StageUsage]:
    """Extract actual token usage from trace entries."""
    stages: list[StageUsage] = []
    for entry in entries:
        task = entry.get("task", "")
        if task not in _LLM_STAGES:
            continue

        extra = entry.get("extra", {})
        usage = extra.get("usage", {})

        stages.append(
            StageUsage(
                stage=task,
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                llm_calls=usage.get("llm_calls", 0),
                cache_hit=extra.get("cache_hit", False),
                model=entry.get("model", ""),
            )
        )

    return stages


def build_usage_report(
    run_id: str,
    estimate: object | None = None,
    tier_name: str = "",
) -> UsageReport:
    """Build a full usage report for a completed run.

    *estimate* is an optional ``RunEstimate`` from ``engine.cost_estimator``.
    If provided, projected values are populated for comparison.
    """
    entries = load_trace_entries(run_id)
    stages = extract_usage_from_traces(entries)

    report = UsageReport(run_id=run_id, tier=tier_name, stages=stages)

    # Populate projections from estimate if available
    if estimate is not None:
        try:
            from engine.cost_estimator import TierName

            tn = TierName(tier_name) if tier_name else TierName.MVP
            report.projected_input_tokens = estimate.total_input_tokens
            report.projected_output_tokens = estimate.total_output_tokens(tn)
            report.projected_cost_usd = estimate.cost_usd(tn)
        except Exception as exc:
            logger.warning("Could not populate projections: %s", exc)

    return report


def save_usage_report(run_id: str, report: UsageReport) -> Path:
    """Save usage report JSON alongside the run's trace."""
    out = get_state_dir() / "runs" / run_id / "usage_report.json"
    out.write_text(json.dumps(report.to_dict(), indent=2))
    logger.info("Usage report saved: %s", out)
    return out


def load_usage_report(run_id: str) -> dict | None:
    """Load a previously saved usage report, or None if not found."""
    path = get_state_dir() / "runs" / run_id / "usage_report.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
