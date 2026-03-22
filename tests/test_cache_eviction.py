"""Tests for cache eviction — LLM response cache and sandbox venv cache.

Exercises the TTL-based eviction logic without hitting real APIs or creating
real virtualenvs.  We create synthetic cache entries with controlled timestamps
and verify that only stale entries are removed.
"""

import json
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

import engine.context
import engine.tracer as tracer
from engine.cache import (
    DEFAULT_LLM_CACHE_TTL_DAYS,
    cache_save,
    evict_stale_llm_cache,
)
from engine.sandbox import (
    DEFAULT_VENV_CACHE_TTL_DAYS,
    _get_venv_cache_dir,
    evict_stale_venv_cache,
)
from engine.tracer import GENESIS_HASH


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path):
    """Point engine context at a temp dir and reset tracer state."""
    engine.context.init(tmp_path)
    (tmp_path / "state").mkdir()
    tracer._run_id = None
    tracer._prev_hash = GENESIS_HASH
    tracer._seq = 0
    yield
    tracer._run_id = None
    tracer._prev_hash = GENESIS_HASH
    tracer._seq = 0


# ── LLM cache eviction ──────────────────────────────────────────────────────


def _write_llm_cache_entry(tmp_path, key: str, age_days: float) -> Path:
    """Write a synthetic LLM cache entry with a controlled created_at timestamp."""
    cache_dir = tmp_path / "state" / "cache" / "llm"
    cache_dir.mkdir(parents=True, exist_ok=True)

    created = datetime.now(timezone.utc) - timedelta(days=age_days)
    path = cache_dir / f"{key}.json"
    path.write_text(
        json.dumps(
            {
                "response": f"response for {key}",
                "created_at": created.isoformat(),
                "stage": "design",
                "model": "test-model",
            }
        )
    )
    return path


class TestEvictStaleLlmCache:
    def test_deletes_old_entries(self, tmp_path):
        """Entries older than the TTL are deleted."""
        old = _write_llm_cache_entry(tmp_path, "old_entry", age_days=31)
        fresh = _write_llm_cache_entry(tmp_path, "fresh_entry", age_days=5)

        deleted = evict_stale_llm_cache(ttl_days=30)

        assert deleted == 1
        assert not old.exists()
        assert fresh.exists()

    def test_preserves_entries_within_ttl(self, tmp_path):
        """Entries within the TTL are not touched."""
        _write_llm_cache_entry(tmp_path, "entry_a", age_days=10)
        _write_llm_cache_entry(tmp_path, "entry_b", age_days=20)

        deleted = evict_stale_llm_cache(ttl_days=30)

        assert deleted == 0

    def test_custom_ttl(self, tmp_path):
        """Custom TTL overrides the default."""
        _write_llm_cache_entry(tmp_path, "recent", age_days=3)
        _write_llm_cache_entry(tmp_path, "slightly_old", age_days=8)

        deleted = evict_stale_llm_cache(ttl_days=7)

        assert deleted == 1

    def test_empty_cache_dir_returns_zero(self, tmp_path):
        """No cache directory → 0 deletions, no crash."""
        deleted = evict_stale_llm_cache(ttl_days=30)
        assert deleted == 0

    def test_malformed_json_treated_as_old(self, tmp_path):
        """Malformed cache entries are treated as infinitely old and evicted."""
        cache_dir = tmp_path / "state" / "cache" / "llm"
        cache_dir.mkdir(parents=True, exist_ok=True)
        bad = cache_dir / "bad_entry.json"
        bad.write_text("not valid json!!!")

        deleted = evict_stale_llm_cache(ttl_days=30)

        assert deleted == 1
        assert not bad.exists()

    def test_missing_created_at_uses_mtime(self, tmp_path):
        """Entries without created_at fall back to filesystem mtime."""
        cache_dir = tmp_path / "state" / "cache" / "llm"
        cache_dir.mkdir(parents=True, exist_ok=True)
        path = cache_dir / "no_timestamp.json"
        path.write_text(json.dumps({"response": "old response", "stage": "test", "model": "m"}))
        # Set mtime to 60 days ago
        old_time = time.time() - (60 * 86400)
        os.utime(path, (old_time, old_time))

        deleted = evict_stale_llm_cache(ttl_days=30)

        assert deleted == 1
        assert not path.exists()

    def test_uses_cache_save_entries(self, tmp_path):
        """Entries written by cache_save() have correct timestamps for eviction."""
        cache_save("test_key_abc", "response text", "design", "test-model")

        # Fresh entry should survive eviction
        deleted = evict_stale_llm_cache(ttl_days=30)
        assert deleted == 0

    def test_default_ttl_value(self):
        """Default TTL is 30 days."""
        assert DEFAULT_LLM_CACHE_TTL_DAYS == 30


# ── Venv cache eviction ─────────────────────────────────────────────────────


def _create_fake_venv(cache_dir: Path, name: str, age_days: float) -> Path:
    """Create a fake cached venv directory with a controlled mtime."""
    venv = cache_dir / name
    venv.mkdir(parents=True)
    # Put a sentinel file inside so it's not empty
    (venv / "bin").mkdir()
    (venv / "bin" / "python").write_text("#!/usr/bin/env python3")

    # Set mtime to simulate age
    old_time = time.time() - (age_days * 86400)
    os.utime(venv, (old_time, old_time))
    return venv


class TestEvictStaleVenvCache:
    def test_deletes_old_venvs(self, tmp_path):
        """Cached venvs older than the TTL are deleted."""
        cache_dir = _get_venv_cache_dir()
        old = _create_fake_venv(cache_dir, "old_venv_abc", age_days=10)
        fresh = _create_fake_venv(cache_dir, "fresh_venv_xyz", age_days=2)

        deleted = evict_stale_venv_cache(ttl_days=7)

        assert deleted == 1
        assert not old.exists()
        assert fresh.exists()

    def test_preserves_recent_venvs(self, tmp_path):
        """Venvs within the TTL are not touched."""
        cache_dir = _get_venv_cache_dir()
        _create_fake_venv(cache_dir, "venv_a", age_days=1)
        _create_fake_venv(cache_dir, "venv_b", age_days=3)

        deleted = evict_stale_venv_cache(ttl_days=7)

        assert deleted == 0

    def test_custom_ttl(self, tmp_path):
        """Custom TTL overrides the default."""
        cache_dir = _get_venv_cache_dir()
        _create_fake_venv(cache_dir, "recent", age_days=2)
        _create_fake_venv(cache_dir, "oldish", age_days=4)

        deleted = evict_stale_venv_cache(ttl_days=3)

        assert deleted == 1

    def test_empty_cache_dir_returns_zero(self, tmp_path):
        """Empty venv cache → 0 deletions, no crash."""
        _get_venv_cache_dir()  # Ensure dir exists but is empty
        deleted = evict_stale_venv_cache(ttl_days=7)
        assert deleted == 0

    def test_ignores_non_directory_files(self, tmp_path):
        """Regular files in the venv cache dir are not deleted or counted."""
        cache_dir = _get_venv_cache_dir()
        stray_file = cache_dir / "stray.txt"
        stray_file.write_text("not a venv")
        old_time = time.time() - (30 * 86400)
        os.utime(stray_file, (old_time, old_time))

        deleted = evict_stale_venv_cache(ttl_days=7)

        assert deleted == 0
        assert stray_file.exists()

    def test_default_ttl_value(self):
        """Default TTL is 7 days."""
        assert DEFAULT_VENV_CACHE_TTL_DAYS == 7
