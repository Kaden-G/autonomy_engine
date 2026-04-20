"""Secrets bridge — makes Streamlit Cloud secrets available to the engine.

Problem:
    The engine reads API keys from ``os.environ`` (via ``python-dotenv``).
    Streamlit Cloud stores secrets in ``st.secrets``, which is a separate
    namespace.  The pipeline runner spawns as a *subprocess*, so it inherits
    ``os.environ`` — not ``st.secrets``.

Solution:
    Call ``inject_secrets()`` once at app startup.  It copies any recognised
    API keys from ``st.secrets`` into ``os.environ`` *only if* they aren't
    already set (environment variables always win, so local ``.env`` files
    still work during development).

Security notes:
    - Keys live in process memory only; they are never written to disk or
      logged.  The subprocess inherits them via the standard fork/exec
      environment — same trust boundary as ``python-dotenv``.
    - On Streamlit Cloud, secrets are encrypted at rest and injected at
      container startup.  This bridge simply moves them into the environ
      namespace that ``anthropic.Anthropic()`` and ``openai.OpenAI()``
      already expect.

RISK: Session-scoped env vars are visible to *all* subprocesses spawned
by this Streamlit instance.  In a shared-hosting scenario (multiple users
on one Streamlit Cloud app), this is acceptable because each visitor gets
their own container.  In a self-hosted multi-tenant setup, use a proper
secrets manager (AWS Secrets Manager, Vault) instead.  (POAM: not
applicable for single-tenant Streamlit Cloud deployment.)
"""

import logging
import os

import streamlit as st

logger = logging.getLogger(__name__)

# Keys we recognise and will bridge from st.secrets → os.environ.
# Add new keys here as providers are added (e.g. GOOGLE_API_KEY).
_BRIDGED_KEYS = [
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
]


def inject_secrets() -> list[str]:
    """Copy API keys from Streamlit secrets into os.environ.

    Returns a list of key names that were injected (for logging — values
    are never included in the return or log output).
    """
    injected: list[str] = []

    for key in _BRIDGED_KEYS:
        # Environment variable wins — this preserves local .env behaviour.
        if os.environ.get(key):
            continue

        # st.secrets raises KeyError if the key doesn't exist; .get()
        # returns None on missing keys depending on Streamlit version.
        value = st.secrets.get(key) if hasattr(st.secrets, "get") else None
        if value is None:
            try:
                value = st.secrets[key]
            except (KeyError, FileNotFoundError):
                # FileNotFoundError: no secrets.toml and not on Cloud.
                value = None

        if value:
            os.environ[key] = str(value)
            injected.append(key)

    if injected:
        logger.info(
            "Secrets bridge: injected %d key(s) from st.secrets → os.environ: %s",
            len(injected),
            ", ".join(injected),
        )

    return injected
