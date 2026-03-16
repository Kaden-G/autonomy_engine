"""Tests for engine.tier_context — tier-aware scope guidance."""

import pytest

from engine.tier_context import (
    get_design_guidance,
    get_implement_guidance,
    get_tier,
    is_mvp,
    reset,
    set_tier,
)


@pytest.fixture(autouse=True)
def _clean_tier():
    """Reset tier state before and after each test."""
    reset()
    yield
    reset()


# ── Basic state management ───────────────────────────────────────────────────


class TestTierState:
    def test_default_is_none(self):
        assert get_tier() is None

    def test_set_and_get_premium(self):
        set_tier("premium")
        assert get_tier() == "premium"

    def test_set_and_get_mvp(self):
        set_tier("mvp")
        assert get_tier() == "mvp"

    def test_case_insensitive(self):
        set_tier("MVP")
        assert get_tier() == "mvp"
        assert is_mvp()

    def test_is_mvp_true(self):
        set_tier("mvp")
        assert is_mvp() is True

    def test_is_mvp_false_for_premium(self):
        set_tier("premium")
        assert is_mvp() is False

    def test_is_mvp_false_when_unset(self):
        assert is_mvp() is False

    def test_reset_clears_tier(self):
        set_tier("mvp")
        reset()
        assert get_tier() is None


# ── Design guidance ──────────────────────────────────────────────────────────


class TestDesignGuidance:
    def test_mvp_returns_scope_constraints(self):
        set_tier("mvp")
        guidance = get_design_guidance()
        assert "MVP" in guidance
        assert "Fewer components" in guidance
        assert "15-30 total files" in guidance

    def test_premium_returns_empty(self):
        set_tier("premium")
        guidance = get_design_guidance()
        assert guidance == ""

    def test_unset_returns_empty(self):
        guidance = get_design_guidance()
        assert guidance == ""

    def test_mvp_mentions_happy_path(self):
        set_tier("mvp")
        guidance = get_design_guidance()
        assert "Happy-path" in guidance

    def test_mvp_mentions_no_docker(self):
        set_tier("mvp")
        guidance = get_design_guidance()
        assert "Docker" in guidance


# ── Implement guidance ───────────────────────────────────────────────────────


class TestImplementGuidance:
    def test_mvp_returns_scope_constraints(self):
        set_tier("mvp")
        guidance = get_implement_guidance()
        assert "MVP" in guidance
        assert "compilable" in guidance.lower() or "complete" in guidance.lower()

    def test_premium_returns_empty(self):
        set_tier("premium")
        guidance = get_implement_guidance()
        assert guidance == ""

    def test_unset_returns_empty(self):
        guidance = get_implement_guidance()
        assert guidance == ""

    def test_mvp_prioritizes_fewer_files(self):
        set_tier("mvp")
        guidance = get_implement_guidance()
        assert "Fewer" in guidance

    def test_mvp_skips_tests(self):
        set_tier("mvp")
        guidance = get_implement_guidance()
        assert "test" in guidance.lower()
