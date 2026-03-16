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
## MANDATORY Scope Constraint: MVP Tier

⚠️  **HARD LIMITS — your design WILL BE REJECTED if it exceeds these:**
- **Maximum 5 components/modules.** Not 6, not 8, not 10. Five or fewer.
- **Maximum 40 total files** across all components combined.
- **No component should contain more than 10 files.**

You are designing for an **MVP (Minimum Viable Product)** build. These
constraints are enforced by an automated circuit breaker that will halt the
build if your design is too large. Design small or the build fails.

**Rules:**

1. **3-5 components MAXIMUM** — aggressively merge related concerns. Combine
   types, utils, and config into the modules that use them. A "Database" module
   and an "API" module should be ONE module if they share types.
2. **Happy-path only** — skip error handling beyond basic try/catch. No custom
   error hierarchies, retry logic, or graceful degradation.
3. **No infrastructure** — zero Docker, CI/CD, monitoring, logging frameworks,
   or deployment configs. Application code only.
4. **Simplest viable technology** — ``localStorage`` over IndexedDB, built-in
   ``fetch`` over axios, CSS modules over styled-components.
5. **Flat structure** — ``src/`` with files directly in it. No
   ``src/features/auth/components/forms/`` nesting.
6. **Ruthlessly cut features** — implement only the 2-3 features that define
   the core value proposition. Everything else is cut, not deferred.
7. **Combine aggressively** — one React component file can contain multiple
   small components. One module file can export multiple related functions.
   Prefer 5 files of 200 lines over 20 files of 50 lines.

**Before finalizing, count your components and estimated files. If you have
more than 5 components or more than 40 files, simplify until you are under
both limits.**
"""

_MVP_IMPLEMENT_GUIDANCE = """\
## MANDATORY Scope Constraint: MVP Tier

⚠️  **HARD LIMIT: produce NO MORE THAN 10 files in this chunk.** The build
will be rejected by an automated circuit breaker if the total project
exceeds 40 files or 750 KB. Every extra file counts against the budget.

**Rules:**

1. **Complete, compilable files** — a working app with 3 features beats a
   broken app with 8. If you run low on budget, finish fewer files well.
2. **Fewer, LARGER files** — combine related logic. Put multiple React
   components in one file. Merge types/interfaces into the module that uses
   them. Never create a file with fewer than 20 lines.
3. **Zero tests, docs, or config** — no test files, no README, no .env.example,
   no tsconfig.json unless strictly required for compilation. The pipeline
   handles testing separately.
4. **Inline everything used once** — no utils.ts, no helpers.ts, no constants.ts
   unless multiple files import from them.
5. **Hardcode defaults** — no config systems, no environment variable parsing,
   no settings files.
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
