"""AI response cache — skip the API call if we've seen this exact request before.

When the pipeline runs the same prompt with the same model and parameters, the
cached response is reused instead of making another (paid) API call.  Cache keys
are built from the stage name, prompt content, model, and generation settings.
Cache entries are immutable — the first response wins and is never overwritten.

Eviction:
    Caches grow forever unless periodically cleaned.  ``evict_stale_cache()``
    deletes entries older than a configurable TTL (default 30 days).  Call it
    at pipeline startup for "lazy" eviction — no background daemon needed.
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from engine.context import get_state_dir

logger = logging.getLogger(__name__)

# Default TTL for LLM response cache entries (in days).
# Override via config.yml: cache.llm_ttl_days
DEFAULT_LLM_CACHE_TTL_DAYS = 30


def hash_content(text: str) -> str:
    """Return SHA-256 hex digest of a string."""
    return hashlib.sha256(text.encode()).hexdigest()


def hash_params(model: str, max_tokens: int) -> str:
    """Return SHA-256 hex digest of model + max_tokens."""
    return hashlib.sha256(f"{model}:{max_tokens}".encode()).hexdigest()


def build_cache_key(
    stage: str,
    template_hash: str,
    envelope_hash: str,
    model: str,
    params_hash: str,
) -> str:
    """Build a deterministic cache key from stage, template, envelope, model, and params."""
    raw = f"{stage}:{template_hash}:{envelope_hash}:{model}:{params_hash}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _cache_dir() -> Path:
    return get_state_dir() / "cache" / "llm"


def cache_lookup(cache_key: str) -> str | None:
    """Return cached LLM response for *cache_key*, or None on miss."""
    path = _cache_dir() / f"{cache_key}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return data["response"]


def cache_save(cache_key: str, response: str, stage: str, model: str) -> None:
    """Persist an LLM response to cache. Skips silently if already cached (immutable)."""
    cache_dir = _cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{cache_key}.json"
    if path.exists():
        return  # Immutable — do not overwrite
    path.write_text(
        json.dumps(
            {
                "response": response,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "stage": stage,
                "model": model,
            },
            indent=2,
        )
    )


# ── Eviction ──────────────────────────────────────────────────────────────────


def evict_stale_llm_cache(ttl_days: int = DEFAULT_LLM_CACHE_TTL_DAYS) -> int:
    """Delete LLM cache entries older than *ttl_days*.

    Uses the ``created_at`` timestamp stored inside each JSON entry (not
    filesystem mtime) because that's the authoritative creation time.
    Falls back to filesystem mtime if the JSON is malformed or missing
    the field — better to evict based on imperfect data than to never
    evict at all.

    Returns the number of entries deleted.

    Why TTL instead of LRU?
        LRU would require tracking access times on every cache_lookup,
        adding writes to what is currently a read-only path.  TTL is
        simpler, predictable, and sufficient — LLM cache entries
        become stale as models and prompts evolve, so age is a good
        proxy for usefulness.
    """
    cache_dir = _cache_dir()
    if not cache_dir.exists():
        return 0

    now = datetime.now(timezone.utc)
    deleted = 0

    for path in cache_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text())
            created_str = data.get("created_at")
            if created_str:
                # Parse ISO timestamp — fromisoformat handles the UTC offset
                created = datetime.fromisoformat(created_str)
            else:
                # No timestamp in JSON — fall back to file mtime
                created = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except (json.JSONDecodeError, KeyError, ValueError, OSError):
            # Malformed entry — treat as infinitely old so it gets cleaned up
            created = datetime.min.replace(tzinfo=timezone.utc)

        age_days = (now - created).total_seconds() / 86400
        if age_days > ttl_days:
            try:
                path.unlink()
                deleted += 1
            except OSError:
                pass  # Best-effort — skip files we can't delete

    if deleted:
        logger.info("LLM cache eviction: removed %d entries older than %d days", deleted, ttl_days)
    return deleted
