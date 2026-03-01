"""Tests for engine.cache — deterministic LLM response caching."""

from unittest.mock import MagicMock, patch

import pytest

import engine.context
import engine.tracer as tracer
from engine.cache import build_cache_key, cache_lookup, cache_save
from engine.tracer import GENESIS_HASH, init_run


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path):
    """Point engine context at a temp dir and reset tracer module state."""
    engine.context.init(tmp_path)
    (tmp_path / "state").mkdir()
    tracer._run_id = None
    tracer._prev_hash = GENESIS_HASH
    tracer._seq = 0
    yield
    tracer._run_id = None
    tracer._prev_hash = GENESIS_HASH
    tracer._seq = 0


class TestCacheHit:
    def test_cache_hit_returns_response(self):
        key = build_cache_key("design", "aaa", "bbb", "test-model", "ccc")
        cache_save(key, "cached response", "design", "test-model")
        assert cache_lookup(key) == "cached response"


class TestCacheMiss:
    def test_cache_miss_returns_none(self):
        assert cache_lookup("nonexistent_key_abc123") is None


class TestChangedEnvelope:
    def test_changed_envelope_causes_miss(self):
        key1 = build_cache_key("design", "tmpl", "envelope_v1", "model", "params")
        cache_save(key1, "response v1", "design", "model")

        key2 = build_cache_key("design", "tmpl", "envelope_v2", "model", "params")
        assert cache_lookup(key2) is None


class TestCacheImmutability:
    def test_first_write_wins(self):
        key = build_cache_key("design", "t", "e", "m", "p")
        cache_save(key, "first response", "design", "m")
        cache_save(key, "second response", "design", "m")
        assert cache_lookup(key) == "first response"


class TestDesignTaskUsesCache:
    def test_generate_called_once_on_two_runs(self, tmp_path):
        """Call design_system twice with identical inputs — generate called once."""
        init_run()

        # Set up state files
        state = tmp_path / "state"
        inputs = state / "inputs"
        inputs.mkdir(parents=True, exist_ok=True)
        (inputs / "REQUIREMENTS.md").write_text("Build a widget")
        (inputs / "CONSTRAINTS.md").write_text("Python only")
        (inputs / "NON_GOALS.md").write_text("No GUI")
        (state / "designs").mkdir(parents=True, exist_ok=True)

        # Set up prompt template
        templates = tmp_path / "templates" / "prompts"
        templates.mkdir(parents=True, exist_ok=True)
        (templates / "design.txt").write_text(
            "{requirements}\n{constraints}\n{non_goals}\n{extra_context}"
        )

        mock_provider = MagicMock()
        mock_provider.model = "test-model"
        mock_provider.provider = "claude"
        mock_provider.max_tokens = 16384
        mock_provider.generate.return_value = "Generated architecture"

        with patch("tasks.design.get_provider", return_value=mock_provider):
            from tasks.design import design_system

            design_system.fn()

            # Second call — should hit cache
            # Need a new run for a fresh trace chain
            tracer._prev_hash = GENESIS_HASH
            tracer._seq = 0
            init_run()
            design_system.fn()

        assert mock_provider.generate.call_count == 1
