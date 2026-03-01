"""Tests for stage-based model resolution in get_provider()."""

from unittest.mock import patch

import yaml

from engine.llm_provider import get_provider


def _write_config(tmp_path, config: dict) -> str:
    path = tmp_path / "config.yml"
    path.write_text(yaml.dump(config))
    return str(path)


# ── Claude provider ──────────────────────────────────────────────────────────


@patch("engine.llm_provider.ClaudeProvider.__init__", return_value=None)
def test_claude_no_stage_returns_default(mock_init, tmp_path):
    cfg = _write_config(
        tmp_path,
        {
            "llm": {
                "provider": "claude",
                "claude": {"model": "claude-sonnet-4-20250514"},
            },
        },
    )
    get_provider(config_path=cfg)
    mock_init.assert_called_once_with(model="claude-sonnet-4-20250514", max_tokens=16384)


@patch("engine.llm_provider.ClaudeProvider.__init__", return_value=None)
def test_claude_stage_override(mock_init, tmp_path):
    cfg = _write_config(
        tmp_path,
        {
            "llm": {
                "provider": "claude",
                "claude": {"model": "claude-sonnet-4-20250514"},
                "models": {"design": "claude-haiku-4-5-20251001"},
            },
        },
    )
    get_provider(config_path=cfg, stage="design")
    mock_init.assert_called_once_with(model="claude-haiku-4-5-20251001", max_tokens=16384)


@patch("engine.llm_provider.ClaudeProvider.__init__", return_value=None)
def test_claude_unknown_stage_falls_back(mock_init, tmp_path):
    cfg = _write_config(
        tmp_path,
        {
            "llm": {
                "provider": "claude",
                "claude": {"model": "claude-sonnet-4-20250514"},
                "models": {"design": "claude-haiku-4-5-20251001"},
            },
        },
    )
    get_provider(config_path=cfg, stage="nonexistent")
    mock_init.assert_called_once_with(model="claude-sonnet-4-20250514", max_tokens=16384)


@patch("engine.llm_provider.ClaudeProvider.__init__", return_value=None)
def test_claude_no_models_section(mock_init, tmp_path):
    cfg = _write_config(
        tmp_path,
        {
            "llm": {
                "provider": "claude",
                "claude": {"model": "claude-sonnet-4-20250514"},
            },
        },
    )
    get_provider(config_path=cfg, stage="design")
    mock_init.assert_called_once_with(model="claude-sonnet-4-20250514", max_tokens=16384)


# ── OpenAI provider ──────────────────────────────────────────────────────────


@patch("engine.llm_provider.OpenAIProvider.__init__", return_value=None)
def test_openai_no_stage_returns_default(mock_init, tmp_path):
    cfg = _write_config(
        tmp_path,
        {
            "llm": {
                "provider": "openai",
                "openai": {"model": "gpt-4o"},
            },
        },
    )
    get_provider(config_path=cfg)
    mock_init.assert_called_once_with(model="gpt-4o", max_tokens=16384)


@patch("engine.llm_provider.OpenAIProvider.__init__", return_value=None)
def test_openai_stage_override(mock_init, tmp_path):
    cfg = _write_config(
        tmp_path,
        {
            "llm": {
                "provider": "openai",
                "openai": {"model": "gpt-4o"},
                "models": {"implement": "gpt-4o-mini"},
            },
        },
    )
    get_provider(config_path=cfg, stage="implement")
    mock_init.assert_called_once_with(model="gpt-4o-mini", max_tokens=16384)


@patch("engine.llm_provider.OpenAIProvider.__init__", return_value=None)
def test_openai_unknown_stage_falls_back(mock_init, tmp_path):
    cfg = _write_config(
        tmp_path,
        {
            "llm": {
                "provider": "openai",
                "openai": {"model": "gpt-4o"},
                "models": {"implement": "gpt-4o-mini"},
            },
        },
    )
    get_provider(config_path=cfg, stage="nonexistent")
    mock_init.assert_called_once_with(model="gpt-4o", max_tokens=16384)


# ── max_tokens override ──────────────────────────────────────────────────────


@patch("engine.llm_provider.ClaudeProvider.__init__", return_value=None)
def test_max_tokens_from_config(mock_init, tmp_path):
    cfg = _write_config(
        tmp_path,
        {
            "llm": {
                "provider": "claude",
                "max_tokens": 8192,
                "claude": {"model": "claude-sonnet-4-20250514"},
            },
        },
    )
    get_provider(config_path=cfg)
    mock_init.assert_called_once_with(model="claude-sonnet-4-20250514", max_tokens=8192)
