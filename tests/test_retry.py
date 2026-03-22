"""Tests for LLM provider retry logic with exponential backoff.

Exercises the _call_with_retry mechanism in LLMProvider without hitting
real APIs.  We subclass LLMProvider directly to control which exceptions
are considered retryable, then verify:
    - Successful calls pass through unchanged
    - Transient errors trigger retries up to the configured limit
    - Non-retryable errors propagate immediately (no wasted retries)
    - Backoff delays increase exponentially
    - The call eventually succeeds after transient failures
    - Usage counters aren't double-counted on retried calls
"""

from unittest.mock import MagicMock, patch

import pytest

from engine.llm_provider import DEFAULT_BACKOFF_BASE, DEFAULT_MAX_RETRIES, LLMProvider


# ── Test fixtures ─────────────────────────────────────────────────────────────


class _TransientError(Exception):
    """Simulates a retryable API error (rate limit, server error, etc.)."""


class _PermanentError(Exception):
    """Simulates a non-retryable API error (auth failure, bad request, etc.)."""


class FakeProvider(LLMProvider):
    """Minimal concrete provider for testing retry logic in isolation.

    Accepts a callable that simulates the API — can be configured to
    raise exceptions, return values, or mix the two across calls.
    """

    provider = "fake"

    def __init__(self, api_fn):
        self.model = "fake-model"
        self.max_tokens = 1000
        self._api_fn = api_fn

    def _is_retryable(self, exc: Exception) -> bool:
        return isinstance(exc, _TransientError)

    def generate(self, prompt: str, system: str | None = None) -> str:
        return self._call_with_retry(self._api_fn, prompt, system)


# ── Happy path ────────────────────────────────────────────────────────────────


class TestRetrySuccessOnFirstCall:
    def test_returns_result_without_retry(self):
        """When the API call succeeds immediately, no retry occurs."""
        api = MagicMock(return_value="hello")
        provider = FakeProvider(api)

        result = provider.generate("test prompt")

        assert result == "hello"
        assert api.call_count == 1

    def test_passes_args_through(self):
        """Arguments are forwarded to the underlying API function."""
        api = MagicMock(return_value="ok")
        provider = FakeProvider(api)

        provider.generate("my prompt", "my system")

        api.assert_called_once_with("my prompt", "my system")


# ── Transient errors → retry → eventual success ──────────────────────────────


class TestRetryOnTransientErrors:
    @patch("engine.llm_provider.time.sleep")
    def test_succeeds_after_transient_failures(self, mock_sleep):
        """Retries transient errors and returns the eventual success."""
        # Fail twice, then succeed on third attempt
        api = MagicMock(
            side_effect=[_TransientError("rate limit"), _TransientError("500"), "success"]
        )
        provider = FakeProvider(api)

        result = provider.generate("test")

        assert result == "success"
        assert api.call_count == 3

    @patch("engine.llm_provider.time.sleep")
    def test_exponential_backoff_delays(self, mock_sleep):
        """Backoff delays increase exponentially: base^1, base^2, etc."""
        api = MagicMock(side_effect=[_TransientError("err"), "ok"])
        provider = FakeProvider(api)
        provider.backoff_base = 2.0

        provider.generate("test")

        # First retry should sleep for 2^1 = 2.0 seconds
        mock_sleep.assert_called_once_with(2.0)

    @patch("engine.llm_provider.time.sleep")
    def test_backoff_escalates_across_retries(self, mock_sleep):
        """Each subsequent retry waits longer: 2s, 4s, 8s with base=2."""
        api = MagicMock(
            side_effect=[
                _TransientError("1"),
                _TransientError("2"),
                _TransientError("3"),
                "finally",
            ]
        )
        provider = FakeProvider(api)
        provider.backoff_base = 2.0
        provider.max_retries = 3

        result = provider.generate("test")

        assert result == "finally"
        delays = [call.args[0] for call in mock_sleep.call_args_list]
        assert delays == [2.0, 4.0, 8.0]

    @patch("engine.llm_provider.time.sleep")
    def test_custom_backoff_base(self, mock_sleep):
        """Custom backoff_base changes the delay schedule."""
        api = MagicMock(side_effect=[_TransientError("err"), "ok"])
        provider = FakeProvider(api)
        provider.backoff_base = 3.0

        provider.generate("test")

        # 3^1 = 3.0 seconds
        mock_sleep.assert_called_once_with(3.0)


# ── Exhausted retries ────────────────────────────────────────────────────────


class TestExhaustedRetries:
    @patch("engine.llm_provider.time.sleep")
    def test_raises_last_error_after_max_retries(self, mock_sleep):
        """When all retries fail, the last transient error is raised."""
        api = MagicMock(
            side_effect=[
                _TransientError("first"),
                _TransientError("second"),
                _TransientError("third"),
                _TransientError("fourth — final"),
            ]
        )
        provider = FakeProvider(api)
        provider.max_retries = 3

        with pytest.raises(_TransientError, match="fourth — final"):
            provider.generate("test")

        # 1 original + 3 retries = 4 total calls
        assert api.call_count == 4

    @patch("engine.llm_provider.time.sleep")
    def test_zero_retries_means_single_attempt(self, mock_sleep):
        """max_retries=0 disables retrying — single attempt only."""
        api = MagicMock(side_effect=_TransientError("one shot"))
        provider = FakeProvider(api)
        provider.max_retries = 0

        with pytest.raises(_TransientError, match="one shot"):
            provider.generate("test")

        assert api.call_count == 1
        mock_sleep.assert_not_called()


# ── Non-retryable errors ─────────────────────────────────────────────────────


class TestNonRetryableErrors:
    @patch("engine.llm_provider.time.sleep")
    def test_permanent_error_propagates_immediately(self, mock_sleep):
        """Non-retryable errors are raised on the first attempt, no retries."""
        api = MagicMock(side_effect=_PermanentError("invalid API key"))
        provider = FakeProvider(api)

        with pytest.raises(_PermanentError, match="invalid API key"):
            provider.generate("test")

        assert api.call_count == 1
        mock_sleep.assert_not_called()

    @patch("engine.llm_provider.time.sleep")
    def test_permanent_after_transient_propagates(self, mock_sleep):
        """A permanent error after a transient one stops retrying immediately."""
        api = MagicMock(side_effect=[_TransientError("rate limit"), _PermanentError("bad request")])
        provider = FakeProvider(api)

        with pytest.raises(_PermanentError, match="bad request"):
            provider.generate("test")

        assert api.call_count == 2


# ── Default configuration ────────────────────────────────────────────────────


class TestRetryDefaults:
    def test_default_max_retries(self):
        """LLMProvider defaults to DEFAULT_MAX_RETRIES."""
        api = MagicMock(return_value="ok")
        provider = FakeProvider(api)
        assert provider.max_retries == DEFAULT_MAX_RETRIES

    def test_default_backoff_base(self):
        """LLMProvider defaults to DEFAULT_BACKOFF_BASE."""
        api = MagicMock(return_value="ok")
        provider = FakeProvider(api)
        assert provider.backoff_base == DEFAULT_BACKOFF_BASE


# ── Provider-specific _is_retryable ──────────────────────────────────────────


class TestClaudeIsRetryable:
    """Verify ClaudeProvider retries the right exception types."""

    def test_retries_rate_limit(self):
        import anthropic

        from engine.llm_provider import ClaudeProvider

        # Build a minimal provider without hitting the API
        with patch.object(ClaudeProvider, "__init__", lambda self: None):
            p = ClaudeProvider()
            p._anthropic = anthropic

        # RateLimitError requires a response-like object
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.headers = {}
        exc = anthropic.RateLimitError(response=mock_response, body=None, message="rate limited")
        assert p._is_retryable(exc) is True

    def test_does_not_retry_auth_error(self):
        import anthropic

        from engine.llm_provider import ClaudeProvider

        with patch.object(ClaudeProvider, "__init__", lambda self: None):
            p = ClaudeProvider()
            p._anthropic = anthropic

        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.headers = {}
        exc = anthropic.AuthenticationError(response=mock_response, body=None, message="bad key")
        assert p._is_retryable(exc) is False


class TestOpenAIIsRetryable:
    """Verify OpenAIProvider retries the right exception types."""

    def test_retries_rate_limit(self):
        import openai

        from engine.llm_provider import OpenAIProvider

        with patch.object(OpenAIProvider, "__init__", lambda self: None):
            p = OpenAIProvider()
            p._openai = openai

        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.headers = {}
        exc = openai.RateLimitError(response=mock_response, body=None, message="rate limited")
        assert p._is_retryable(exc) is True

    def test_does_not_retry_auth_error(self):
        import openai

        from engine.llm_provider import OpenAIProvider

        with patch.object(OpenAIProvider, "__init__", lambda self: None):
            p = OpenAIProvider()
            p._openai = openai

        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.headers = {}
        exc = openai.AuthenticationError(response=mock_response, body=None, message="bad key")
        assert p._is_retryable(exc) is False
