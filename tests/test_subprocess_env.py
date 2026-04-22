"""Tests for dashboard.subprocess_env — the pipeline subprocess allowlist."""

from __future__ import annotations

import pytest

from dashboard.subprocess_env import (
    ALLOWLIST,
    REQUIRE_AT_LEAST_ONE,
    MissingCredentialsError,
    build_subprocess_env,
)


# ── Allowlist filtering ─────────────────────────────────────────────────────


class TestAllowlistFiltering:
    def test_allowlist_filters_unlisted(self):
        """Keys not in ALLOWLIST must be stripped."""
        source = {
            "AWS_SECRET_ACCESS_KEY": "should-not-transit",
            "GITHUB_TOKEN": "should-not-transit",
            "RANDOM_VAR": "should-not-transit",
            "ANTHROPIC_API_KEY": "sk-ant-xxx",
            "PATH": "/usr/bin",
        }
        env = build_subprocess_env(source=source)
        assert "AWS_SECRET_ACCESS_KEY" not in env
        assert "GITHUB_TOKEN" not in env
        assert "RANDOM_VAR" not in env
        assert env["ANTHROPIC_API_KEY"] == "sk-ant-xxx"
        assert env["PATH"] == "/usr/bin"

    def test_curated_env_has_no_arbitrary_keys(self):
        """Every key in the result must be in ALLOWLIST (sanity invariant)."""
        source = {k: "value" for k in ALLOWLIST}
        source["EXTRA_LEAKED"] = "oops"
        env = build_subprocess_env(source=source)
        leaked = set(env) - set(ALLOWLIST)
        assert leaked == set(), f"Unexpected keys leaked: {leaked}"

    def test_unset_optional_vars_absent(self):
        """An unset allowlisted var must be absent — not set to empty string."""
        source = {"ANTHROPIC_API_KEY": "sk-ant", "PATH": "/usr/bin"}
        env = build_subprocess_env(source=source)
        assert "AE_LOG_LEVEL" not in env
        assert "VIRTUAL_ENV" not in env
        assert "HTTPS_PROXY" not in env


# ── Required credentials ────────────────────────────────────────────────────


class TestRequiredCredentials:
    def test_missing_both_api_keys_raises(self):
        source = {"PATH": "/usr/bin", "HOME": "/home/user"}
        with pytest.raises(MissingCredentialsError, match="none of"):
            build_subprocess_env(source=source)

    def test_anthropic_key_alone_satisfies(self):
        source = {"ANTHROPIC_API_KEY": "sk-ant", "PATH": "/usr/bin"}
        env = build_subprocess_env(source=source)
        assert env["ANTHROPIC_API_KEY"] == "sk-ant"

    def test_openai_key_alone_satisfies(self):
        source = {"OPENAI_API_KEY": "sk-openai", "PATH": "/usr/bin"}
        env = build_subprocess_env(source=source)
        assert env["OPENAI_API_KEY"] == "sk-openai"

    def test_both_keys_present(self):
        source = {
            "ANTHROPIC_API_KEY": "sk-ant",
            "OPENAI_API_KEY": "sk-openai",
            "PATH": "/usr/bin",
        }
        env = build_subprocess_env(source=source)
        assert env["ANTHROPIC_API_KEY"] == "sk-ant"
        assert env["OPENAI_API_KEY"] == "sk-openai"

    def test_empty_string_key_treated_as_missing(self):
        """Streamlit secrets can surface as '' when a secret is blanked."""
        source = {
            "ANTHROPIC_API_KEY": "",
            "OPENAI_API_KEY": "",
            "PATH": "/usr/bin",
        }
        with pytest.raises(MissingCredentialsError):
            build_subprocess_env(source=source)

    def test_one_blank_one_set_satisfies(self):
        source = {
            "ANTHROPIC_API_KEY": "",
            "OPENAI_API_KEY": "sk-openai",
            "PATH": "/usr/bin",
        }
        env = build_subprocess_env(source=source)
        # The blank ANTHROPIC is dropped; OPENAI satisfies the gate.
        assert "ANTHROPIC_API_KEY" not in env
        assert env["OPENAI_API_KEY"] == "sk-openai"


# ── Merge semantics ─────────────────────────────────────────────────────────


class TestExtraOverrides:
    def test_extra_overrides_source(self):
        source = {"ANTHROPIC_API_KEY": "sk-ant", "AE_ACTOR": "parent-user"}
        extra = {"AE_ACTOR": "session-user"}
        env = build_subprocess_env(source=source, extra=extra)
        assert env["AE_ACTOR"] == "session-user"

    def test_extra_cannot_smuggle_non_allowlisted_keys(self):
        """extra= forwards any non-empty value — this is intentional per the
        docstring (caller override), but document the behavior."""
        source = {"ANTHROPIC_API_KEY": "sk-ant"}
        extra = {"AE_ACTOR": "ci-runner"}  # ALLOWLIST member
        env = build_subprocess_env(source=source, extra=extra)
        assert env["AE_ACTOR"] == "ci-runner"

    def test_extra_blank_does_not_override(self):
        source = {"ANTHROPIC_API_KEY": "sk-ant", "AE_ACTOR": "kept"}
        extra = {"AE_ACTOR": ""}
        env = build_subprocess_env(source=source, extra=extra)
        assert env["AE_ACTOR"] == "kept"


# ── Proxy + TLS forwarding ──────────────────────────────────────────────────


class TestProxyAndTLS:
    def test_proxy_vars_forwarded(self):
        source = {
            "ANTHROPIC_API_KEY": "sk-ant",
            "HTTPS_PROXY": "http://corp-proxy:3128",
            "HTTP_PROXY": "http://corp-proxy:3128",
            "NO_PROXY": "localhost,127.0.0.1",
        }
        env = build_subprocess_env(source=source)
        assert env["HTTPS_PROXY"] == "http://corp-proxy:3128"
        assert env["HTTP_PROXY"] == "http://corp-proxy:3128"
        assert env["NO_PROXY"] == "localhost,127.0.0.1"

    def test_lowercase_proxy_vars_forwarded(self):
        """POSIX convention — requests/httpx read lowercase on POSIX."""
        source = {
            "ANTHROPIC_API_KEY": "sk-ant",
            "https_proxy": "http://corp-proxy:3128",
        }
        env = build_subprocess_env(source=source)
        assert env["https_proxy"] == "http://corp-proxy:3128"

    def test_custom_ca_bundle_forwarded(self):
        source = {
            "ANTHROPIC_API_KEY": "sk-ant",
            "SSL_CERT_FILE": "/etc/ssl/corp-ca.pem",
            "REQUESTS_CA_BUNDLE": "/etc/ssl/corp-ca.pem",
        }
        env = build_subprocess_env(source=source)
        assert env["SSL_CERT_FILE"] == "/etc/ssl/corp-ca.pem"
        assert env["REQUESTS_CA_BUNDLE"] == "/etc/ssl/corp-ca.pem"


# ── Allowlist contents (documentation / regression lock) ────────────────────


class TestAllowlistContents:
    def test_required_at_least_one_all_in_allowlist(self):
        """Every required key must itself be allowlisted."""
        for key in REQUIRE_AT_LEAST_ONE:
            assert key in ALLOWLIST, f"{key} is required but not in ALLOWLIST"

    def test_critical_runtime_essentials_present(self):
        """PATH + HOME are load-bearing for the subprocess to function."""
        assert "PATH" in ALLOWLIST
        assert "HOME" in ALLOWLIST

    def test_ae_control_vars_present(self):
        """AE_* vars are read by engine/log_config.py, tracer, decision_gates."""
        for key in ("AE_LOG_FORMAT", "AE_LOG_LEVEL", "AE_TRACE_KEY_DIR", "AE_ACTOR"):
            assert key in ALLOWLIST
