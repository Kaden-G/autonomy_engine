"""Deterministic LLM response cache — reuse prior outputs when inputs haven't changed."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from engine.context import get_state_dir


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
