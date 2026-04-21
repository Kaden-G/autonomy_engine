"""Tests for production-readiness features: signal handling, env config, structured logging.

Most tests here verify operational hardening for the LEGACY Prefect flow
(`flows/autonomous_flow.py`). Under LangGraph (v2.0 default), that hardening
has been ported to `graph/nodes.py` and is exercised by
`tests/test_graph_pipeline.py`.

DEPRECATED (Prefect-specific test classes only): Retires 2026-05-21.
After that date, delete `TestGracefulShutdown` and `TestConfigLoading`.
`TestStructuredLogging` stays — it tests `engine.log_config`, which is
orchestrator-agnostic and STILL-ACTIVE. See `docs/prefect-sunset-audit.md`.

To run the Prefect-flow-specific classes during the sunset window:
    RUN_DEPRECATED_TESTS=1 pytest tests/test_production_readiness.py
"""

import json
import logging
import os
import signal

import pytest
import yaml

import engine.context

# Module-level skip removed intentionally — `TestStructuredLogging` below tests
# `engine.log_config` (STILL-ACTIVE) and must run in CI.  Only the Prefect-
# specific classes are gated behind RUN_DEPRECATED_TESTS.
_SKIP_PREFECT_TESTS = pytest.mark.skipif(
    os.getenv("RUN_DEPRECATED_TESTS") != "1",
    reason="Prefect flow tests retire with flows/ on 2026-05-21. "
    "Set RUN_DEPRECATED_TESTS=1 to run during the sunset window.",
)


# ── Graceful shutdown ────────────────────────────────────────────────────────


@_SKIP_PREFECT_TESTS
class TestGracefulShutdown:
    """Verify signal handlers are installed and behave correctly."""

    def test_shutdown_handler_sets_flag(self):
        """_shutdown_handler sets _SHUTTING_DOWN on first call."""
        from flows.autonomous_flow import _shutdown_handler
        import flows.autonomous_flow as flow_mod

        # Reset state
        flow_mod._SHUTTING_DOWN = False

        # First signal should trigger SystemExit (via sys.exit)
        with pytest.raises(SystemExit) as exc_info:
            _shutdown_handler(signal.SIGTERM, None)

        # Exit code = 128 + SIGTERM (15) = 143
        assert exc_info.value.code == 128 + signal.SIGTERM
        assert flow_mod._SHUTTING_DOWN is True

        # Reset
        flow_mod._SHUTTING_DOWN = False

    def test_second_signal_force_exits(self):
        """Second signal while already shutting down exits with code 1."""
        import flows.autonomous_flow as flow_mod

        flow_mod._SHUTTING_DOWN = True

        with pytest.raises(SystemExit) as exc_info:
            flow_mod._shutdown_handler(signal.SIGINT, None)

        assert exc_info.value.code == 1

        # Reset
        flow_mod._SHUTTING_DOWN = False

    def test_setup_installs_handlers(self):
        """_setup_signal_handlers installs our handler for SIGTERM and SIGINT."""
        from flows.autonomous_flow import _setup_signal_handlers, _shutdown_handler

        _setup_signal_handlers()

        assert signal.getsignal(signal.SIGTERM) is _shutdown_handler
        assert signal.getsignal(signal.SIGINT) is _shutdown_handler

        # Restore default handlers for other tests
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        signal.signal(signal.SIGINT, signal.SIG_DFL)


# ── Environment-aware config loading ─────────────────────────────────────────


@_SKIP_PREFECT_TESTS
class TestConfigLoading:
    """Verify environment-specific config resolution."""

    @pytest.fixture(autouse=True)
    def _setup_project(self, tmp_path, monkeypatch):
        """Create a temp project dir with config files."""
        engine.context.init(tmp_path)
        self.project_root = tmp_path

        # Default config
        default_cfg = {"llm": {"provider": "claude"}, "cache": {"llm_ttl_days": 30}}
        (tmp_path / "config.yml").write_text(yaml.dump(default_cfg))

        # Production config
        prod_cfg = {"llm": {"provider": "openai"}, "cache": {"llm_ttl_days": 90}}
        (tmp_path / "config.production.yml").write_text(yaml.dump(prod_cfg))

        # Clean env vars
        monkeypatch.delenv("AE_CONFIG_PATH", raising=False)
        monkeypatch.delenv("AE_ENV", raising=False)

    def test_default_config(self):
        """No env vars → loads config.yml."""
        from flows.autonomous_flow import _load_config

        cfg = _load_config(self.project_root)
        assert cfg["llm"]["provider"] == "claude"

    def test_ae_env_selects_config(self, monkeypatch):
        """AE_ENV=production → loads config.production.yml."""
        from flows.autonomous_flow import _load_config

        monkeypatch.setenv("AE_ENV", "production")
        cfg = _load_config(self.project_root)
        assert cfg["llm"]["provider"] == "openai"
        assert cfg["cache"]["llm_ttl_days"] == 90

    def test_ae_config_path_overrides_all(self, monkeypatch):
        """AE_CONFIG_PATH takes precedence over AE_ENV."""
        from flows.autonomous_flow import _load_config

        custom_cfg = {"llm": {"provider": "custom"}}
        (self.project_root / "my-config.yml").write_text(yaml.dump(custom_cfg))
        monkeypatch.setenv("AE_CONFIG_PATH", "my-config.yml")
        monkeypatch.setenv("AE_ENV", "production")  # should be ignored

        cfg = _load_config(self.project_root)
        assert cfg["llm"]["provider"] == "custom"

    def test_missing_env_config_raises(self, monkeypatch):
        """AE_ENV pointing to nonexistent file raises FileNotFoundError."""
        from flows.autonomous_flow import _load_config

        monkeypatch.setenv("AE_ENV", "staging")  # config.staging.yml doesn't exist
        with pytest.raises(FileNotFoundError, match="config.staging.yml"):
            _load_config(self.project_root)

    def test_missing_explicit_path_raises(self, monkeypatch):
        """AE_CONFIG_PATH pointing to nonexistent file raises FileNotFoundError."""
        from flows.autonomous_flow import _load_config

        monkeypatch.setenv("AE_CONFIG_PATH", "nonexistent.yml")
        with pytest.raises(FileNotFoundError, match="nonexistent.yml"):
            _load_config(self.project_root)

    def test_no_config_at_all_returns_empty(self, monkeypatch):
        """If config.yml doesn't exist and no env vars set, returns empty dict."""
        from flows.autonomous_flow import _load_config

        (self.project_root / "config.yml").unlink()
        cfg = _load_config(self.project_root)
        assert cfg == {}


# ── Structured logging ───────────────────────────────────────────────────────


class TestStructuredLogging:
    """Verify JSON and text logging modes."""

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        monkeypatch.delenv("AE_LOG_FORMAT", raising=False)
        monkeypatch.delenv("AE_LOG_LEVEL", raising=False)

    def test_text_mode_default(self, monkeypatch):
        """Default mode is text with human-readable output."""
        from engine.log_config import configure_logging

        configure_logging()
        root = logging.getLogger()
        assert len(root.handlers) == 1
        assert not isinstance(root.handlers[0].formatter, type(None))

    def test_json_mode(self, monkeypatch, capsys):
        """AE_LOG_FORMAT=json produces JSON-lines output."""
        from engine.log_config import configure_logging

        monkeypatch.setenv("AE_LOG_FORMAT", "json")
        configure_logging()

        test_logger = logging.getLogger("test.json_mode")
        test_logger.info("hello from test")

        captured = capsys.readouterr()
        # JSON goes to stderr
        line = captured.err.strip().split("\n")[-1]
        parsed = json.loads(line)
        assert parsed["level"] == "INFO"
        assert parsed["message"] == "hello from test"
        assert "timestamp" in parsed
        assert parsed["logger"] == "test.json_mode"

    def test_log_level_override(self, monkeypatch):
        """AE_LOG_LEVEL controls root logger level."""
        from engine.log_config import configure_logging

        monkeypatch.setenv("AE_LOG_LEVEL", "DEBUG")
        configure_logging()
        assert logging.getLogger().level == logging.DEBUG

    def test_idempotent_configure(self, monkeypatch):
        """Calling configure_logging twice doesn't create duplicate handlers."""
        from engine.log_config import configure_logging

        configure_logging()
        configure_logging()
        assert len(logging.getLogger().handlers) == 1
