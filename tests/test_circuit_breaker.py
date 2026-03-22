"""Tests for the extraction circuit breaker in tasks.extract."""

import pytest

import engine.tier_context as tier_ctx
from tasks.extract import (
    ExtractionCircuitBreaker,
    _MAX_FILES_MVP,
    _MAX_FILES_PREMIUM,
    _MAX_TOTAL_BYTES_MVP,
    _check_extraction_limits,
)
from tasks.manifest_schema import FileEntry, FileManifest


@pytest.fixture(autouse=True)
def _clean_tier():
    """Reset tier state before and after each test."""
    tier_ctx.reset()
    yield
    tier_ctx.reset()


def _make_manifest(n_files: int, content_size: int = 50) -> FileManifest:
    """Build a FileManifest with *n_files* files of *content_size* bytes each."""
    files = [FileEntry(path=f"src/file_{i}.ts", content="x" * content_size) for i in range(n_files)]
    return FileManifest(files=files)


# ── MVP tier limits ──────────────────────────────────────────────────────────


class TestMVPLimits:
    def test_under_limit_passes(self):
        tier_ctx.set_tier("mvp")
        manifest = _make_manifest(30, content_size=100)
        _check_extraction_limits(manifest)  # should not raise

    def test_at_limit_passes(self):
        tier_ctx.set_tier("mvp")
        manifest = _make_manifest(_MAX_FILES_MVP, content_size=50)
        _check_extraction_limits(manifest)  # should not raise

    def test_exceeds_file_count_raises(self):
        tier_ctx.set_tier("mvp")
        manifest = _make_manifest(_MAX_FILES_MVP + 1, content_size=50)
        with pytest.raises(ExtractionCircuitBreaker, match="File count"):
            _check_extraction_limits(manifest)

    def test_exceeds_byte_limit_raises(self):
        tier_ctx.set_tier("mvp")
        # 10 files, each huge
        per_file = (_MAX_TOTAL_BYTES_MVP // 10) + 1000
        manifest = _make_manifest(10, content_size=per_file)
        with pytest.raises(ExtractionCircuitBreaker, match="Total size"):
            _check_extraction_limits(manifest)

    def test_exception_has_attributes(self):
        tier_ctx.set_tier("mvp")
        manifest = _make_manifest(_MAX_FILES_MVP + 5, content_size=50)
        with pytest.raises(ExtractionCircuitBreaker) as exc_info:
            _check_extraction_limits(manifest)
        exc = exc_info.value
        assert exc.file_count == _MAX_FILES_MVP + 5
        assert exc.limit_files == _MAX_FILES_MVP

    def test_mentions_mvp_in_message(self):
        tier_ctx.set_tier("mvp")
        manifest = _make_manifest(_MAX_FILES_MVP + 1)
        with pytest.raises(ExtractionCircuitBreaker, match="MVP"):
            _check_extraction_limits(manifest)


# ── Premium tier limits ──────────────────────────────────────────────────────


class TestPremiumLimits:
    def test_under_limit_passes(self):
        tier_ctx.set_tier("premium")
        manifest = _make_manifest(100, content_size=100)
        _check_extraction_limits(manifest)  # should not raise

    def test_exceeds_file_count_raises(self):
        tier_ctx.set_tier("premium")
        manifest = _make_manifest(_MAX_FILES_PREMIUM + 1, content_size=50)
        with pytest.raises(ExtractionCircuitBreaker, match="File count"):
            _check_extraction_limits(manifest)

    def test_premium_allows_more_than_mvp(self):
        tier_ctx.set_tier("premium")
        # This would fail under MVP but should pass under Premium
        manifest = _make_manifest(_MAX_FILES_MVP + 10, content_size=50)
        _check_extraction_limits(manifest)  # should not raise

    def test_mentions_premium_in_message(self):
        tier_ctx.set_tier("premium")
        manifest = _make_manifest(_MAX_FILES_PREMIUM + 1)
        with pytest.raises(ExtractionCircuitBreaker, match="Premium"):
            _check_extraction_limits(manifest)


# ── No tier set (defaults to premium behavior) ──────────────────────────────


class TestNoTier:
    def test_no_tier_uses_premium_limits(self):
        # When no tier is set, is_mvp() returns False → premium limits
        manifest = _make_manifest(_MAX_FILES_MVP + 10, content_size=50)
        _check_extraction_limits(manifest)  # should not raise (premium limit)

    def test_no_tier_still_enforces_premium_max(self):
        manifest = _make_manifest(_MAX_FILES_PREMIUM + 1, content_size=50)
        with pytest.raises(ExtractionCircuitBreaker):
            _check_extraction_limits(manifest)


# ── Both violations ──────────────────────────────────────────────────────────


class TestBothViolations:
    def test_both_file_and_byte_violations_reported(self):
        tier_ctx.set_tier("mvp")
        per_file = (_MAX_TOTAL_BYTES_MVP // 10) + 1000
        manifest = _make_manifest(_MAX_FILES_MVP + 10, content_size=per_file)
        with pytest.raises(ExtractionCircuitBreaker) as exc_info:
            _check_extraction_limits(manifest)
        msg = str(exc_info.value)
        assert "File count" in msg
        assert "Total size" in msg
