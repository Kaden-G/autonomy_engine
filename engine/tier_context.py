"""Tier context — store and retrieve the user-selected build tier.

This module acts as a singleton store for the active tier, making it
accessible to any task that needs to adjust its behavior based on the
selected tier (e.g. the design prompt, implement chunking strategy).

Usage::

    from engine.tier_context import set_tier, get_tier, get_tier_guidance
    set_tier("mvp")                  # called once by the pipeline runner
    guidance = get_tier_guidance()   # returns LLM-facing scope instructions
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_selected_tier: str | None = None


def set_tier(tier_name: str) -> None:
    """Store the selected tier name (``"premium"`` or ``"mvp"``)."""
    global _selected_tier
    _selected_tier = tier_name.lower()
    logger.info("Active tier set to: %s", _selected_tier)


def get_tier() -> str | None:
    """Return the active tier name, or ``None`` if not yet selected."""
    return _selected_tier


def is_mvp() -> bool:
    """Return ``True`` if the active tier is MVP."""
    return _selected_tier == "mvp"


def reset() -> None:
    """Clear the tier context (for testing)."""
    global _selected_tier
    _selected_tier = None


# ── LLM-facing guidance text ────────────────────────────────────────────────

_MVP_DESIGN_GUIDANCE = """\
## Scope Constraint: MVP Tier

You are designing for an **MVP (Minimum Viable Product)** build. This means:

1. **Fewer components** — aim for 3-5 core modules, not 8-12. Combine related
   concerns into single files where reasonable.
2. **Happy-path only** — skip advanced error handling, retry logic, and edge
   cases. Basic try/catch is fine; custom error hierarchies are not.
3. **No ancillary infrastructure** — omit Docker, CI/CD, monitoring, logging
   frameworks, and deployment configs. Focus on the application code.
4. **Simplest viable technology** — prefer built-in/standard-library solutions
   over third-party libraries when the difference is small. For example, use
   ``localStorage`` instead of IndexedDB+Dexie if the data model is simple.
5. **Flat or minimal directory structure** — avoid deep nesting. A flat
   ``src/`` with a few files is better than ``src/features/*/components/``.
6. **Skip optional features** — if the spec lists features as "nice to have"
   or there are features that are clearly secondary to the core value
   proposition, omit them entirely. Focus on the 2-3 features that make the
   product useful.
7. **Target ~15-30 total files** — this is a hard guideline. If your design
   would require more, simplify the architecture.

The implementation budget is constrained, so a simpler design that can be
fully implemented is far more valuable than an ambitious design that gets
truncated mid-file.
"""

_MVP_IMPLEMENT_GUIDANCE = """\
## Scope Constraint: MVP Tier

This is an **MVP build**. Your implementation budget is limited. Prioritize:

1. **Complete, compilable files** over comprehensive features. A working app
   with 3 features beats a broken app with 8 features.
2. **Fewer, larger files** — combine related logic instead of splitting across
   many small modules. Avoid creating files with only 5-10 lines.
3. **Skip tests, docs, and config files** — focus purely on application code.
   The pipeline handles testing separately.
4. **Inline simple utilities** — don't create separate utility/helper files
   for functions used only once.
5. **Use defaults** — hardcode reasonable defaults instead of building
   configuration systems.
"""

_PREMIUM_GUIDANCE = ""  # Premium gets no constraint — full scope is intended.


def get_design_guidance() -> str:
    """Return tier-appropriate scope guidance for the design prompt."""
    if is_mvp():
        return _MVP_DESIGN_GUIDANCE
    return _PREMIUM_GUIDANCE


def get_implement_guidance() -> str:
    """Return tier-appropriate scope guidance for the implement prompt."""
    if is_mvp():
        return _MVP_IMPLEMENT_GUIDANCE
    return _PREMIUM_GUIDANCE
