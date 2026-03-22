"""Model registry — single source of truth for model limits and pricing.

Loads ``models.yml`` at import time and provides prefix-matched lookups
for max output tokens and per-million-token pricing.  Both
``llm_provider`` and ``cost_estimator`` delegate to this module instead
of maintaining their own hardcoded tables.

Why a separate module instead of putting this in llm_provider?
    ``cost_estimator`` needs pricing data but should NOT import the full
    provider machinery (it never makes API calls).  A thin registry
    module keeps the dependency graph clean:

        model_registry  ←  llm_provider
                        ←  cost_estimator

Design decisions:
    - **Loaded once at import** — models.yml is small and rarely changes.
      Hot-reloading would add complexity for no practical benefit.
    - **Prefix matching** — dated model IDs like ``claude-sonnet-4-20250514``
      resolve to ``claude-sonnet-4`` automatically.  Longest prefix wins.
    - **Graceful fallback** — unknown models get a conservative default
      (4096 tokens, $0 pricing) with a logged warning, so new models
      don't crash the pipeline.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

import yaml

from engine.context import get_project_dir

logger = logging.getLogger(__name__)

# ── Registry type ────────────────────────────────────────────────────────

# Each entry: {"provider": str, "max_output_tokens": int,
#              "pricing": {"input": float, "output": float}}
ModelEntry = dict


# ── Loading ──────────────────────────────────────────────────────────────


def _find_models_yml() -> Path:
    """Locate ``models.yml`` — project-local copy wins, else engine default.

    This mirrors the pattern used by ``context.get_config_path()``: if the
    user's project directory contains a ``models.yml`` it takes precedence,
    letting per-project model overrides work without touching the engine
    install.
    """
    # Project-local override
    project = get_project_dir()
    local = project / "models.yml"
    if local.exists():
        return local

    # Engine-bundled default (lives next to engine/ directory)
    engine_root = Path(__file__).resolve().parent.parent
    default = engine_root / "models.yml"
    if default.exists():
        return default

    raise FileNotFoundError(
        "models.yml not found in project directory or engine root. "
        "The engine ships with a default — if it's missing, reinstall or "
        "copy models.yml to your project directory."
    )


@lru_cache(maxsize=1)
def _load_registry() -> tuple[dict[str, ModelEntry], ModelEntry]:
    """Parse ``models.yml`` and return ``(models_dict, defaults)``.

    Cached so repeated calls (from both llm_provider and cost_estimator
    in the same process) don't re-read the file.
    """
    path = _find_models_yml()
    with open(path) as f:
        raw = yaml.safe_load(f)

    models: dict[str, ModelEntry] = raw.get("models", {})
    defaults: ModelEntry = raw.get("defaults", {
        "max_output_tokens": 4096,
        "pricing": {"input": 0.0, "output": 0.0},
    })

    logger.debug("Loaded %d model entries from %s", len(models), path)
    return models, defaults


def reload_registry() -> None:
    """Force a fresh read of ``models.yml`` on next access.

    Useful in tests or after the user edits the file mid-session.
    """
    _load_registry.cache_clear()


# ── Prefix-matched lookups ───────────────────────────────────────────────


def _best_prefix_match(model: str, registry: dict[str, ModelEntry]) -> str | None:
    """Return the registry key that is the longest prefix of *model*, or None."""
    best_key: str | None = None
    best_len = 0
    for key in registry:
        if model.startswith(key) and len(key) > best_len:
            best_key, best_len = key, len(key)
    return best_key


def get_model_entry(model: str) -> ModelEntry:
    """Return the full registry entry for *model* (prefix-matched).

    Falls back to ``defaults`` if no prefix matches, logging a warning
    so operators notice when a new model isn't in the registry.
    """
    models, defaults = _load_registry()
    key = _best_prefix_match(model, models)
    if key is not None:
        return models[key]

    logger.warning(
        "Model '%s' not found in models.yml — using defaults "
        "(max_output_tokens=%d, pricing=$0).  Add it to models.yml to "
        "get accurate limits and cost estimates.",
        model,
        defaults.get("max_output_tokens", 4096),
    )
    return defaults


def get_model_limit(model: str) -> int:
    """Return the API-enforced max output tokens for *model*.

    This replaces the per-provider limit dicts that were hardcoded in
    ``llm_provider.py``.  The *provider* argument is no longer needed
    because ``models.yml`` stores limits per model, not per provider.
    """
    entry = get_model_entry(model)
    return entry.get("max_output_tokens", 4096)


def get_model_pricing(model: str) -> dict[str, float]:
    """Return ``{"input": float, "output": float}`` pricing per 1M tokens.

    This replaces the ``_PRICING`` dict that was hardcoded in
    ``cost_estimator.py``.
    """
    entry = get_model_entry(model)
    return entry.get("pricing", {"input": 0.0, "output": 0.0})
