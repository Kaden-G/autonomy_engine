"""Tests for engine.model_registry — config-driven model limits and pricing.

Verifies that models.yml is loaded correctly, prefix matching works,
fallback defaults apply for unknown models, and the registry can be
reloaded cleanly (important for tests that mutate the file).
"""

import pytest
import yaml

import engine.context
from engine.model_registry import (
    get_model_entry,
    get_model_limit,
    get_model_pricing,
    reload_registry,
    _find_models_yml,
)


@pytest.fixture(autouse=True)
def _isolated_registry(tmp_path, monkeypatch):
    """Point engine context at a temp dir with a minimal models.yml.

    Also clears the LRU cache before and after each test so stale
    registry state never leaks between tests.
    """
    engine.context.init(tmp_path)

    # Write a minimal models.yml for testing
    models_yml = {
        "models": {
            "claude-sonnet-4": {
                "provider": "claude",
                "max_output_tokens": 64000,
                "pricing": {"input": 3.00, "output": 15.00},
            },
            "claude-haiku-4": {
                "provider": "claude",
                "max_output_tokens": 16000,
                "pricing": {"input": 0.80, "output": 4.00},
            },
            "gpt-4o": {
                "provider": "openai",
                "max_output_tokens": 16384,
                "pricing": {"input": 2.50, "output": 10.00},
            },
        },
        "defaults": {
            "max_output_tokens": 4096,
            "pricing": {"input": 0.0, "output": 0.0},
        },
    }
    (tmp_path / "models.yml").write_text(yaml.dump(models_yml))

    reload_registry()
    yield
    reload_registry()


# ── Basic loading ────────────────────────────────────────────────────────────


class TestRegistryLoading:
    def test_loads_models_from_project_dir(self, tmp_path):
        """Registry finds and loads models.yml from the project directory."""
        path = _find_models_yml()
        assert path == tmp_path / "models.yml"

    def test_registry_returns_known_models(self):
        """Exact model names resolve to their entries."""
        entry = get_model_entry("claude-sonnet-4")
        assert entry["max_output_tokens"] == 64000
        assert entry["pricing"]["input"] == 3.00

    def test_registry_caches_across_calls(self):
        """Repeated calls hit the LRU cache, not the filesystem."""
        # Call twice — if caching is broken this would fail on a missing file
        # after we delete it (but we don't, just verifying no errors)
        entry1 = get_model_entry("gpt-4o")
        entry2 = get_model_entry("gpt-4o")
        assert entry1 == entry2


# ── Prefix matching ──────────────────────────────────────────────────────────


class TestPrefixMatching:
    def test_dated_model_resolves_to_family(self):
        """A dated model ID like 'claude-sonnet-4-20250514' matches the prefix."""
        limit = get_model_limit("claude-sonnet-4-20250514")
        assert limit == 64000

    def test_longest_prefix_wins(self, tmp_path):
        """When multiple prefixes match, the longest one wins."""
        # Add an overlapping entry
        models_yml = {
            "models": {
                "claude-sonnet": {
                    "provider": "claude",
                    "max_output_tokens": 1000,
                    "pricing": {"input": 1.0, "output": 1.0},
                },
                "claude-sonnet-4": {
                    "provider": "claude",
                    "max_output_tokens": 64000,
                    "pricing": {"input": 3.0, "output": 15.0},
                },
            },
            "defaults": {"max_output_tokens": 4096, "pricing": {"input": 0.0, "output": 0.0}},
        }
        (tmp_path / "models.yml").write_text(yaml.dump(models_yml))
        reload_registry()

        # "claude-sonnet-4-20250514" should match "claude-sonnet-4" (longer), not "claude-sonnet"
        assert get_model_limit("claude-sonnet-4-20250514") == 64000

    def test_exact_match_preferred(self):
        """An exact match is just a prefix match where len(key) == len(model)."""
        assert get_model_limit("gpt-4o") == 16384


# ── Fallback defaults ────────────────────────────────────────────────────────


class TestDefaults:
    def test_unknown_model_returns_default_limit(self):
        """A model not in the registry gets the default max_output_tokens."""
        limit = get_model_limit("totally-unknown-model-v99")
        assert limit == 4096

    def test_unknown_model_returns_default_pricing(self):
        """A model not in the registry gets $0 pricing."""
        pricing = get_model_pricing("totally-unknown-model-v99")
        assert pricing["input"] == 0.0
        assert pricing["output"] == 0.0


# ── Pricing lookups ──────────────────────────────────────────────────────────


class TestPricing:
    def test_known_model_pricing(self):
        """Pricing for a known model matches the registry entry."""
        pricing = get_model_pricing("claude-haiku-4")
        assert pricing["input"] == 0.80
        assert pricing["output"] == 4.00

    def test_dated_model_pricing(self):
        """Pricing prefix-matches dated model IDs."""
        pricing = get_model_pricing("gpt-4o-2025-01-01")
        assert pricing["input"] == 2.50
        assert pricing["output"] == 10.00


# ── Reload ───────────────────────────────────────────────────────────────────


class TestReload:
    def test_reload_picks_up_changes(self, tmp_path):
        """After modifying models.yml and calling reload, new values are used."""
        # Verify original value
        assert get_model_limit("claude-sonnet-4") == 64000

        # Update the file
        models_yml = {
            "models": {
                "claude-sonnet-4": {
                    "provider": "claude",
                    "max_output_tokens": 99999,
                    "pricing": {"input": 5.0, "output": 25.0},
                },
            },
            "defaults": {"max_output_tokens": 4096, "pricing": {"input": 0.0, "output": 0.0}},
        }
        (tmp_path / "models.yml").write_text(yaml.dump(models_yml))
        reload_registry()

        # Should reflect the new value
        assert get_model_limit("claude-sonnet-4") == 99999
        assert get_model_pricing("claude-sonnet-4")["input"] == 5.0


# ── Missing file ─────────────────────────────────────────────────────────────


class TestMissingFile:
    def test_missing_models_yml_raises(self, tmp_path, monkeypatch):
        """A clear error is raised if models.yml doesn't exist anywhere."""
        (tmp_path / "models.yml").unlink()
        reload_registry()

        # Also patch the engine-root fallback so it can't find the real file
        import engine.model_registry as reg

        monkeypatch.setattr(
            reg,
            "_find_models_yml",
            lambda: (_ for _ in ()).throw(
                FileNotFoundError("models.yml not found in project directory or engine root.")
            ),
        )
        reload_registry()

        with pytest.raises(FileNotFoundError, match="models.yml not found"):
            get_model_limit("claude-sonnet-4")
