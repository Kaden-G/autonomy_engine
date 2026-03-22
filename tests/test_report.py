"""Tests for engine.report — audit bundle exporter."""

import json
import tarfile

import pytest

import engine.context
import engine.tracer as tracer
from engine.report import _run_dir, build_artifact_manifest, create_bundle
from engine.tracer import GENESIS_HASH


@pytest.fixture(autouse=True)
def _isolated_context(tmp_path):
    """Point engine context at a temp dir and reset tracer state."""
    engine.context.init(tmp_path)
    (tmp_path / "state").mkdir()
    tracer._run_id = None
    tracer._prev_hash = GENESIS_HASH
    tracer._seq = 0
    yield
    tracer._run_id = None
    tracer._prev_hash = GENESIS_HASH
    tracer._seq = 0


def _setup_run(tmp_path, run_id="abc123"):
    """Create a minimal run directory with a valid trace."""
    run_dir = tmp_path / "state" / "runs" / run_id
    run_dir.mkdir(parents=True)
    return run_dir


# ── _run_dir ──────────────────────────────────────────────────────────────────


class TestRunDir:
    def test_returns_path_when_exists(self, tmp_path):
        rd = _setup_run(tmp_path)
        result = _run_dir("abc123")
        assert result == rd

    def test_raises_for_missing_run(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="Run directory not found"):
            _run_dir("nonexistent")


# ── build_artifact_manifest ──────────────────────────────────────────────────


class TestBuildArtifactManifest:
    def test_collects_hashes_from_trace(self, tmp_path):
        rd = _setup_run(tmp_path)
        entries = [
            {
                "seq": 0,
                "task": "bootstrap",
                "inputs": {"inputs/spec.yml": "aaa111"},
                "outputs": {"designs/DESIGN.md": "bbb222"},
                "prev_hash": GENESIS_HASH,
                "entry_hash": "fake",
            },
            {
                "seq": 1,
                "task": "implement",
                "inputs": {"designs/DESIGN.md": "bbb222"},
                "outputs": {"implementations/IMPL.md": "ccc333"},
                "prev_hash": "fake",
                "entry_hash": "fake2",
            },
        ]
        (rd / "trace.jsonl").write_text("\n".join(json.dumps(e) for e in entries) + "\n")

        manifest = build_artifact_manifest("abc123")
        assert manifest["run_id"] == "abc123"
        assert manifest["artifacts"]["inputs/spec.yml"] == "aaa111"
        assert manifest["artifacts"]["designs/DESIGN.md"] == "bbb222"
        assert manifest["artifacts"]["implementations/IMPL.md"] == "ccc333"

    def test_empty_trace_returns_empty_artifacts(self, tmp_path):
        rd = _setup_run(tmp_path)
        (rd / "trace.jsonl").write_text("")

        manifest = build_artifact_manifest("abc123")
        assert manifest["artifacts"] == {}

    def test_no_trace_file_returns_empty_artifacts(self, tmp_path):
        _setup_run(tmp_path)
        manifest = build_artifact_manifest("abc123")
        assert manifest["artifacts"] == {}


# ── create_bundle ────────────────────────────────────────────────────────────


class TestCreateBundle:
    def _write_trace(self, run_dir):
        """Write a single valid trace entry so integrity passes.

        Must also write the HMAC key to the run dir so that
        verify_trace_integrity() can load it during bundle creation.
        """
        import secrets

        tracer._run_id = run_dir.name
        tracer._prev_hash = GENESIS_HASH
        tracer._seq = 0
        # Generate and persist an HMAC key — mirrors what init_run() does
        tracer._hmac_key = secrets.token_bytes(32)
        (run_dir / ".trace_key").write_bytes(tracer._hmac_key)
        tracer.trace(
            task="bootstrap",
            inputs=["inputs/spec.yml"],
            outputs=["designs/DESIGN.md"],
        )

    def test_bundle_contains_trace(self, tmp_path):
        rd = _setup_run(tmp_path)
        self._write_trace(rd)

        out = tmp_path / "bundle.tar.gz"
        result = create_bundle("abc123", out)
        assert result == out

        with tarfile.open(out, "r:gz") as tar:
            names = tar.getnames()
            assert "abc123/trace.jsonl" in names

    def test_bundle_contains_config_snapshot(self, tmp_path):
        rd = _setup_run(tmp_path)
        self._write_trace(rd)
        (rd / "config_snapshot.yml").write_text("llm:\n  provider: claude\n")

        out = tmp_path / "bundle.tar.gz"
        create_bundle("abc123", out)

        with tarfile.open(out, "r:gz") as tar:
            assert "abc123/config_snapshot.yml" in tar.getnames()

    def test_bundle_contains_evidence(self, tmp_path):
        rd = _setup_run(tmp_path)
        self._write_trace(rd)
        ev_dir = rd / "evidence"
        ev_dir.mkdir()
        (ev_dir / "smoke_test.json").write_text('{"name": "smoke_test"}')

        out = tmp_path / "bundle.tar.gz"
        create_bundle("abc123", out)

        with tarfile.open(out, "r:gz") as tar:
            assert "abc123/evidence/smoke_test.json" in tar.getnames()

    def test_bundle_contains_decisions(self, tmp_path):
        rd = _setup_run(tmp_path)
        self._write_trace(rd)
        dec_dir = rd / "decisions"
        dec_dir.mkdir()
        (dec_dir / "design_gate.json").write_text('{"decision": "approve"}')

        out = tmp_path / "bundle.tar.gz"
        create_bundle("abc123", out)

        with tarfile.open(out, "r:gz") as tar:
            assert "abc123/decisions/design_gate.json" in tar.getnames()

    def test_bundle_contains_artifact_manifest(self, tmp_path):
        rd = _setup_run(tmp_path)
        self._write_trace(rd)

        out = tmp_path / "bundle.tar.gz"
        create_bundle("abc123", out)

        with tarfile.open(out, "r:gz") as tar:
            assert "abc123/artifact_manifest.json" in tar.getnames()
            member = tar.extractfile("abc123/artifact_manifest.json")
            manifest = json.loads(member.read())
            assert manifest["run_id"] == "abc123"

    def test_bundle_contains_integrity_result(self, tmp_path):
        rd = _setup_run(tmp_path)
        self._write_trace(rd)

        out = tmp_path / "bundle.tar.gz"
        create_bundle("abc123", out)

        with tarfile.open(out, "r:gz") as tar:
            member = tar.extractfile("abc123/integrity.json")
            integrity = json.loads(member.read())
            assert integrity["run_id"] == "abc123"
            assert integrity["is_valid"] is True
            assert integrity["errors"] == []

    def test_bundle_missing_run_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            create_bundle("nonexistent", tmp_path / "out.tar.gz")

    def test_bundle_failed_integrity_still_creates(self, tmp_path):
        rd = _setup_run(tmp_path)
        # Write a trace with a broken chain (bad entry_hash)
        (rd / "trace.jsonl").write_text(
            json.dumps(
                {
                    "seq": 0,
                    "task": "bootstrap",
                    "inputs": {},
                    "outputs": {},
                    "prev_hash": GENESIS_HASH,
                    "entry_hash": "wrong_hash",
                }
            )
            + "\n"
        )

        out = tmp_path / "bundle.tar.gz"
        result = create_bundle("abc123", out)
        assert result.exists()

        with tarfile.open(out, "r:gz") as tar:
            member = tar.extractfile("abc123/integrity.json")
            integrity = json.loads(member.read())
            assert integrity["is_valid"] is False
            assert len(integrity["errors"]) > 0

    def test_bundle_no_evidence_or_decisions(self, tmp_path):
        """Bundle creation succeeds even with no evidence/ or decisions/ dirs."""
        rd = _setup_run(tmp_path)
        self._write_trace(rd)

        out = tmp_path / "bundle.tar.gz"
        result = create_bundle("abc123", out)
        assert result.exists()

        with tarfile.open(out, "r:gz") as tar:
            names = tar.getnames()
            assert "abc123/trace.jsonl" in names
            assert "abc123/artifact_manifest.json" in names
            assert "abc123/integrity.json" in names

    def test_default_output_path(self, tmp_path):
        rd = _setup_run(tmp_path)
        self._write_trace(rd)

        import os

        old_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            result = create_bundle("abc123")
            assert result.name == "abc123_audit.tar.gz"
            assert result.exists()
        finally:
            os.chdir(old_cwd)
