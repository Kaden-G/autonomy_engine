"""Curated env allowlist for the pipeline subprocess (P1-1).

The Streamlit dashboard spawns the pipeline as a child process.  Without
an explicit ``env=`` argument, ``subprocess.Popen`` inherits the full
parent ``os.environ`` — which, on Streamlit Cloud, has had API keys
injected globally by :mod:`dashboard.secrets_bridge`.  That means every
subprocess spawned by Streamlit can read those keys, not just the
pipeline.

:func:`build_subprocess_env` filters ``os.environ`` down to a
hand-picked allowlist and raises :class:`MissingCredentialsError` when
no LLM provider key is available — preventing a doomed-launch that
would only surface as a cryptic exit code after the pipeline tried its
first API call.

Prior art for curated env dicts:
  * :attr:`engine.sandbox.Sandbox.env` — copies ``os.environ`` and layers
    PATH / VIRTUAL_ENV adjustments for the test sandbox.
  * :func:`engine.sandbox._install_deps` — copy + overlay ``PIP_CACHE_DIR``.

Maps to: OWASP ASVS V14.1 (Build config) · NIST SP 800-53 AC-6 (Least Privilege).
"""

from __future__ import annotations

import os
from typing import Mapping

# Curated allowlist — only these keys may transit from the Streamlit parent
# process to the pipeline subprocess.  Keep this list tight; every entry is
# an explicit acknowledgement that the subprocess needs this value.
ALLOWLIST: tuple[str, ...] = (
    # ── Runtime essentials ───────────────────────────────────────────────
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "TMPDIR",
    # ── Python ───────────────────────────────────────────────────────────
    "PYTHONPATH",
    "PYTHONUNBUFFERED",
    "VIRTUAL_ENV",
    # ── Network (corporate proxies + custom CA bundles) ──────────────────
    # Lowercase variants are what `requests`/`httpx` actually read on POSIX;
    # uppercase forms are the conventional shell spelling.  Forward both.
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "SSL_CERT_FILE",
    "REQUESTS_CA_BUNDLE",
    # ── LLM provider keys ────────────────────────────────────────────────
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    # ── Engine control ───────────────────────────────────────────────────
    # See engine/log_config.py, engine/tracer.py, graph/nodes.py for readers.
    "AE_CONFIG_PATH",
    "AE_ENV",
    "AE_ACTOR",
    "AE_LOG_FORMAT",
    "AE_LOG_LEVEL",
    "AE_TRACE_KEY_DIR",
    "DEMO_MAX_RUNS",
    # ── Actor resolution chain ───────────────────────────────────────────
    # Consumed by engine/decision_gates.py when AE_ACTOR is unset.
    "USER",
    "LOGNAME",
    "USERNAME",
)

# At least one of these must be non-empty in the curated env, or the
# pipeline will crash on its first LLM call.  Fail fast instead.
REQUIRE_AT_LEAST_ONE: tuple[str, ...] = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
)


class MissingCredentialsError(RuntimeError):
    """Raised when no LLM provider key is available in the curated env."""


def build_subprocess_env(
    source: Mapping[str, str] | None = None,
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build a curated env dict for the pipeline subprocess.

    Parameters
    ----------
    source:
        Mapping to filter.  Defaults to ``os.environ``.  Passing an
        explicit dict is the supported test pattern — no ``monkeypatch``
        gymnastics required.
    extra:
        Optional overrides merged last.  Use this when the launcher
        needs to force a specific value (e.g. pinning ``AE_ACTOR`` to
        the session user) regardless of what the parent process had.

    Returns
    -------
    dict[str, str]
        A new dict containing only keys in :data:`ALLOWLIST` that had a
        non-empty value in ``source`` (or that ``extra`` supplied).

    Raises
    ------
    MissingCredentialsError
        If, after merging, none of :data:`REQUIRE_AT_LEAST_ONE` are
        present with a non-empty value.  Prevents launching a pipeline
        that will die on its first provider call.
    """
    src: Mapping[str, str] = source if source is not None else os.environ

    env: dict[str, str] = {}
    for key in ALLOWLIST:
        value = src.get(key)
        if value:  # drops None, empty string, and "0-length whitespace" stays because it's truthy
            env[key] = value

    if extra:
        for key, value in extra.items():
            if value:
                env[key] = value

    if not any(env.get(key) for key in REQUIRE_AT_LEAST_ONE):
        required = ", ".join(REQUIRE_AT_LEAST_ONE)
        raise MissingCredentialsError(
            f"Pipeline subprocess cannot start: none of [{required}] are set. "
            "Provide an API key via .env (local) or Streamlit Cloud secrets."
        )

    return env
