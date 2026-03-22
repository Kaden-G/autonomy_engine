"""AI model interface — supports Claude and OpenAI behind a single, switchable API.

This module lets the rest of the engine call an AI model without knowing (or caring)
whether it's talking to Claude or OpenAI.  The active provider is chosen from
``config.yml``, and each pipeline stage can optionally use a different model
(e.g., a cheaper model for verification, a more capable one for implementation).

Also tracks token usage (the "meter" for AI API costs) across all calls, so the
engine can report actual costs at the end of a run.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod

from dotenv import load_dotenv
import yaml

load_dotenv()

from engine.context import get_config_path

logger = logging.getLogger(__name__)

# ── Retry configuration ───────────────────────────────────────────────────────
# Exponential backoff for transient API failures (rate limits, server errors,
# timeouts).  These defaults are deliberately conservative — a single pipeline
# run makes only a handful of LLM calls, so a few seconds of backoff is cheap
# insurance against a wasted run.
#
# Why NOT use a library like tenacity?  This is the only retry site in the
# codebase, and the logic is simple enough that pulling in a dependency would
# add more cognitive overhead than it saves.  If retry needs grow, revisit.

DEFAULT_MAX_RETRIES = 3  # Total attempts = 1 original + 3 retries
DEFAULT_BACKOFF_BASE = 2.0  # Seconds: 2s → 4s → 8s between retries

# ── Model output-token hard limits ──────────────────────────────────────────
# These are the *API-enforced* maximums.  When a model isn't listed here we
# fall back to a conservative default so new/unknown models never blow up.
#
# Prefix matching is supported: "claude-sonnet-4" matches any dated variant
# like "claude-sonnet-4-20250514".  More-specific entries win.

_CLAUDE_MODEL_LIMITS: dict[str, int] = {
    "claude-opus-4": 32_000,
    "claude-sonnet-4": 64_000,
    "claude-haiku-4": 16_000,
    # Legacy models
    "claude-3-5-sonnet": 8_192,
    "claude-3-5-haiku": 8_192,
    "claude-3-opus": 4_096,
}

_OPENAI_MODEL_LIMITS: dict[str, int] = {
    "gpt-4o": 16_384,
    "gpt-4o-mini": 16_384,
    "gpt-4-turbo": 4_096,
    "o1": 100_000,
    "o3-mini": 100_000,
}

_PROVIDER_LIMITS: dict[str, dict[str, int]] = {
    "claude": _CLAUDE_MODEL_LIMITS,
    "openai": _OPENAI_MODEL_LIMITS,
}

_SAFE_DEFAULT = 4_096  # conservative fallback for unknown models

# ── Per-stage token overrides (set by cost estimator / tier selection) ────
# When populated, get_provider() uses these instead of config max_tokens.
# Stored in threading.local() so concurrent pipeline runs with different
# tier selections don't interfere.
import threading

_local = threading.local()


def _get_stage_token_overrides() -> dict[str, int]:
    """Return the current thread's stage token overrides (lazily initialized)."""
    overrides = getattr(_local, "stage_token_overrides", None)
    if overrides is None:
        _local.stage_token_overrides = {}
        return _local.stage_token_overrides
    return overrides


def set_stage_token_overrides(overrides: dict[str, int]) -> None:
    """Install per-stage max_tokens limits chosen by the user at tier selection.

    Called by the pipeline runner after the user picks a tier.
    """
    current = _get_stage_token_overrides()
    current.clear()
    current.update(overrides)
    logger.info("Stage token overrides installed: %s", overrides)


def get_model_limit(provider: str, model: str) -> int:
    """Return the API-enforced max output tokens for *model*.

    Uses longest-prefix matching so dated model IDs (e.g.
    ``claude-sonnet-4-20250514``) resolve to their family limit.
    """
    limits = _PROVIDER_LIMITS.get(provider, {})

    # Exact match first, then progressively shorter prefixes
    best_key, best_len = None, 0
    for key in limits:
        if model.startswith(key) and len(key) > best_len:
            best_key, best_len = key, len(key)

    if best_key is not None:
        return limits[best_key]

    logger.warning(
        "No known token limit for %s model '%s' — using safe default of %d",
        provider,
        model,
        _SAFE_DEFAULT,
    )
    return _SAFE_DEFAULT


def resolve_max_tokens(
    provider: str,
    model: str,
    requested: int,
) -> int:
    """Return the effective max_tokens: min(requested, model hard limit).

    Logs a warning whenever clamping occurs so operators can see it
    without having to debug a 400 from the API.
    """
    hard_limit = get_model_limit(provider, model)

    if requested > hard_limit:
        logger.warning(
            "Requested max_tokens (%d) exceeds %s limit for '%s' (%d) — "
            "clamping to %d.  Update config.yml if this is unexpected.",
            requested,
            provider,
            model,
            hard_limit,
            hard_limit,
        )
        return hard_limit

    return requested


class LLMProvider(ABC):
    provider: str  # "claude" or "openai"
    model: str
    max_tokens: int
    was_truncated: bool = False  # True when last generate() hit max_tokens
    last_usage: dict | None = None  # Token usage from the most recent generate()

    # Retry settings — can be overridden per instance or via config.
    max_retries: int = DEFAULT_MAX_RETRIES
    backoff_base: float = DEFAULT_BACKOFF_BASE

    # Cumulative usage across all generate() calls on this provider instance.
    # Useful for multi-call stages like chunked implementation.
    _total_input_tokens: int = 0
    _total_output_tokens: int = 0
    _call_count: int = 0

    @property
    def total_usage(self) -> dict:
        """Cumulative token usage across all generate() calls."""
        return {
            "input_tokens": self._total_input_tokens,
            "output_tokens": self._total_output_tokens,
            "llm_calls": self._call_count,
        }

    def _record_usage(self, input_tokens: int, output_tokens: int) -> None:
        """Update cumulative counters after an API call."""
        self.last_usage = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }
        self._total_input_tokens += input_tokens
        self._total_output_tokens += output_tokens
        self._call_count += 1

    def _is_retryable(self, exc: Exception) -> bool:
        """Determine whether an API exception is transient and worth retrying.

        Each provider subclass overrides this to check for provider-specific
        exception types (rate limits, server errors, timeouts).  The base
        implementation returns False — unknown exceptions are never retried.
        """
        return False

    def _call_with_retry(self, api_fn, *args, **kwargs):
        """Execute *api_fn* with exponential backoff on transient failures.

        Only retries exceptions where ``self._is_retryable(exc)`` returns True.
        All other exceptions propagate immediately — we don't want to mask
        auth errors, invalid requests, or SDK bugs behind retry loops.

        The backoff schedule is: backoff_base^1, backoff_base^2, backoff_base^3
        (default 2s, 4s, 8s).  Total worst-case delay before giving up is 14s,
        which is negligible compared to a typical LLM response time.
        """
        last_exc: Exception | None = None
        for attempt in range(1 + self.max_retries):
            try:
                return api_fn(*args, **kwargs)
            except Exception as exc:
                if not self._is_retryable(exc):
                    raise  # Not transient — fail fast
                last_exc = exc
                if attempt < self.max_retries:
                    delay = self.backoff_base ** (attempt + 1)
                    logger.warning(
                        "Transient API error (attempt %d/%d): %s — retrying in %.1fs",
                        attempt + 1,
                        1 + self.max_retries,
                        exc,
                        delay,
                    )
                    time.sleep(delay)
                else:
                    logger.error(
                        "API call failed after %d attempts. Last error: %s",
                        1 + self.max_retries,
                        exc,
                    )
        raise last_exc  # type: ignore[misc]  # we know last_exc is set

    @abstractmethod
    def generate(self, prompt: str, system: str | None = None) -> str:
        """Send a prompt to the LLM and return the text response.

        After calling, check ``self.was_truncated`` to detect whether
        the response was cut short by the max_tokens limit.

        ``self.last_usage`` contains actual token counts from the API::

            {"input_tokens": int, "output_tokens": int}

        ``self.total_usage`` contains cumulative counts across all calls.

        Transient errors (rate limits, server errors, timeouts) are
        automatically retried with exponential backoff.  See
        ``_call_with_retry`` for details.
        """


class ClaudeProvider(LLMProvider):
    provider = "claude"

    def __init__(self, model: str = "claude-sonnet-4-20250514", max_tokens: int = 16384):
        import anthropic

        self.client = anthropic.Anthropic()
        self.model = model
        self.max_tokens = resolve_max_tokens("claude", model, max_tokens)
        # Store module reference for _is_retryable (avoids re-importing)
        self._anthropic = anthropic

    def _is_retryable(self, exc: Exception) -> bool:
        """Retry on Anthropic rate limits (429), server errors (5xx), and timeouts.

        The Anthropic SDK raises typed exceptions for each HTTP status family:
        - RateLimitError (429) — back off and retry
        - InternalServerError (500) — transient server issue
        - APITimeoutError — request timed out, may succeed on retry
        - APIConnectionError — network glitch, may resolve on retry

        We do NOT retry APIStatusError (generic) or AuthenticationError (401)
        because those indicate permanent problems the user must fix.
        """
        return isinstance(
            exc,
            (
                self._anthropic.RateLimitError,
                self._anthropic.InternalServerError,
                self._anthropic.APITimeoutError,
                self._anthropic.APIConnectionError,
            ),
        )

    def _do_generate(self, prompt: str, system: str | None = None) -> str:
        """Raw API call — separated from generate() so _call_with_retry can wrap it."""
        kwargs: dict = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        # Use streaming to avoid the 10-minute timeout limit on large responses
        text_parts: list[str] = []
        with self.client.messages.stream(**kwargs) as stream:
            for text in stream.text_stream:
                text_parts.append(text)
            final_message = stream.get_final_message()

        # Capture actual token usage from API response
        usage = final_message.usage
        self._record_usage(usage.input_tokens, usage.output_tokens)
        logger.info(
            "Token usage: %d input, %d output (budget: %d)",
            usage.input_tokens,
            usage.output_tokens,
            self.max_tokens,
        )

        self.was_truncated = final_message.stop_reason == "max_tokens"
        if self.was_truncated:
            logger.warning(
                "Response truncated — hit max_tokens (%d). Output may be incomplete.",
                self.max_tokens,
            )
        return "".join(text_parts)

    def generate(self, prompt: str, system: str | None = None) -> str:
        return self._call_with_retry(self._do_generate, prompt, system)


class OpenAIProvider(LLMProvider):
    provider = "openai"

    def __init__(self, model: str = "gpt-4o", max_tokens: int = 16384):
        import openai

        self.client = openai.OpenAI()
        self.model = model
        self.max_tokens = resolve_max_tokens("openai", model, max_tokens)
        # Store module reference for _is_retryable (avoids re-importing)
        self._openai = openai

    def _is_retryable(self, exc: Exception) -> bool:
        """Retry on OpenAI rate limits (429), server errors (5xx), and timeouts.

        The OpenAI SDK raises typed exceptions mirroring HTTP status families:
        - RateLimitError (429) — back off and retry
        - InternalServerError (500) — transient server issue
        - APITimeoutError — request timed out, may succeed on retry
        - APIConnectionError — network glitch, may resolve on retry

        We do NOT retry AuthenticationError (401) or BadRequestError (400)
        because those indicate permanent problems the user must fix.
        """
        return isinstance(
            exc,
            (
                self._openai.RateLimitError,
                self._openai.InternalServerError,
                self._openai.APITimeoutError,
                self._openai.APIConnectionError,
            ),
        )

    def _do_generate(self, prompt: str, system: str | None = None) -> str:
        """Raw API call — separated from generate() so _call_with_retry can wrap it."""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        response = self.client.chat.completions.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=messages,
        )
        choice = response.choices[0]

        # Capture actual token usage from API response
        if response.usage:
            self._record_usage(
                response.usage.prompt_tokens,
                response.usage.completion_tokens,
            )
            logger.info(
                "Token usage: %d input, %d output (budget: %d)",
                response.usage.prompt_tokens,
                response.usage.completion_tokens,
                self.max_tokens,
            )

        self.was_truncated = choice.finish_reason == "length"
        if self.was_truncated:
            logger.warning(
                "Response truncated — hit max_tokens (%d). Output may be incomplete.",
                self.max_tokens,
            )
        return choice.message.content

    def generate(self, prompt: str, system: str | None = None) -> str:
        return self._call_with_retry(self._do_generate, prompt, system)


def get_provider(
    config_path: str | None = None,
    stage: str | None = None,
    max_tokens_override: int | None = None,
) -> LLMProvider:
    """Factory: return the LLM provider specified in config.

    If *stage* is given and ``llm.models.<stage>`` exists in the config,
    that model name is used instead of the provider default.

    If *max_tokens_override* is given it takes precedence over the
    config value — used by the tier/cost system to enforce per-stage
    budgets chosen by the user.
    """
    if config_path is None:
        config_path = str(get_config_path())
    with open(config_path) as f:
        config = yaml.safe_load(f)

    llm = config["llm"]
    provider_name = llm["provider"]
    # Priority: explicit override > stage tier budget > config value
    if max_tokens_override:
        max_tokens = max_tokens_override
    elif stage and stage in _get_stage_token_overrides():
        max_tokens = _get_stage_token_overrides()[stage]
        logger.info("Using tier budget for stage '%s': %d tokens", stage, max_tokens)
    else:
        max_tokens = llm.get("max_tokens", 16384)

    # Resolve model: stage override → provider default
    default_model = llm[provider_name]["model"]
    if stage and "models" in llm and stage in llm["models"]:
        model = llm["models"][stage]
    else:
        model = default_model

    # Retry settings from config (optional — sensible defaults apply)
    retry_cfg = llm.get("retry", {})
    retry_max = retry_cfg.get("max_retries", DEFAULT_MAX_RETRIES)
    retry_backoff = retry_cfg.get("backoff_base", DEFAULT_BACKOFF_BASE)

    if provider_name == "claude":
        p = ClaudeProvider(model=model, max_tokens=max_tokens)
    elif provider_name == "openai":
        p = OpenAIProvider(model=model, max_tokens=max_tokens)
    else:
        raise ValueError(f"Unknown LLM provider: {provider_name}")

    p.max_retries = retry_max
    p.backoff_base = retry_backoff
    return p
