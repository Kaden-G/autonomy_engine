"""Tests for engine.tracer — append-only HMAC-authenticated hash chain."""

import json

import pytest

import engine.context
import engine.tracer as tracer
from engine.tracer import (
    GENESIS_HASH,
    _compute_entry_hmac,
    init_run,
    trace,
    verify_trace_integrity,
)


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    """Point engine context at a temp dir, redirect HMAC-key storage to
    tmp, and reset tracer module state.

    ``AE_TRACE_KEY_DIR`` is overridden so tests never touch the real
    ``~/.autonomy_engine/keys/`` directory.
    """
    engine.context.init(tmp_path)
    (tmp_path / "state").mkdir()
    monkeypatch.setenv("AE_TRACE_KEY_DIR", str(tmp_path / "keys"))
    # Reset tracer globals
    tracer._run_id = None
    tracer._prev_hash = GENESIS_HASH
    tracer._seq = 0
    tracer._hmac_key = b""
    yield
    tracer._run_id = None
    tracer._prev_hash = GENESIS_HASH
    tracer._seq = 0
    tracer._hmac_key = b""


def _read_entries(tmp_path) -> list[dict]:
    """Read all entries from the single run's trace.jsonl."""
    runs_dir = tmp_path / "state" / "runs"
    run_dirs = sorted(runs_dir.iterdir())
    trace_file = run_dirs[-1] / "trace.jsonl"
    return [json.loads(line) for line in trace_file.read_text().strip().splitlines()]


# ── init_run ─────────────────────────────────────────────────────────────────


class TestInitRun:
    def test_creates_run_directory(self, tmp_path):
        run_id = init_run()
        assert (tmp_path / "state" / "runs" / run_id).is_dir()

    def test_returns_12_char_hex_string(self, tmp_path):
        run_id = init_run()
        assert isinstance(run_id, str)
        assert len(run_id) == 12
        int(run_id, 16)  # must be valid hex

    def test_successive_runs_get_different_ids(self, tmp_path):
        r1 = init_run()
        r2 = init_run()
        assert r1 != r2

    def test_creates_hmac_key_file(self, tmp_path):
        run_id = init_run()
        # P0-3: key lives at <AE_TRACE_KEY_DIR>/<run_id>.key
        # (fixture redirects AE_TRACE_KEY_DIR to tmp_path/keys).
        key_path = tmp_path / "keys" / f"{run_id}.key"
        assert key_path.exists()
        assert len(key_path.read_bytes()) == 32  # 256-bit key

    def test_different_runs_get_different_keys(self, tmp_path):
        r1 = init_run()
        k1 = (tmp_path / "keys" / f"{r1}.key").read_bytes()
        r2 = init_run()
        k2 = (tmp_path / "keys" / f"{r2}.key").read_bytes()
        assert k1 != k2


# ── Append behavior ─────────────────────────────────────────────────────────


class TestTraceAppend:
    def test_first_entry_has_genesis_prev_hash(self, tmp_path):
        init_run()
        trace(task="bootstrap", inputs=[], outputs=[])
        entries = _read_entries(tmp_path)
        assert entries[0]["prev_hash"] == GENESIS_HASH

    def test_second_entry_chains_to_first(self, tmp_path):
        init_run()
        trace(task="step1", inputs=[], outputs=[])
        trace(task="step2", inputs=[], outputs=[])
        entries = _read_entries(tmp_path)
        assert entries[1]["prev_hash"] == entries[0]["entry_hash"]

    def test_three_entry_chain(self, tmp_path):
        init_run()
        for i in range(3):
            trace(task=f"step{i}", inputs=[], outputs=[])
        entries = _read_entries(tmp_path)
        assert entries[0]["prev_hash"] == GENESIS_HASH
        assert entries[1]["prev_hash"] == entries[0]["entry_hash"]
        assert entries[2]["prev_hash"] == entries[1]["entry_hash"]

    def test_file_is_jsonl_not_json_array(self, tmp_path):
        init_run()
        trace(task="a", inputs=[], outputs=[])
        trace(task="b", inputs=[], outputs=[])
        runs_dir = tmp_path / "state" / "runs"
        trace_file = sorted(runs_dir.iterdir())[-1] / "trace.jsonl"
        raw = trace_file.read_text()
        # JSONL — NOT a JSON array
        assert not raw.strip().startswith("[")
        lines = raw.strip().splitlines()
        assert len(lines) == 2
        for line in lines:
            json.loads(line)  # each line is valid JSON

    def test_seq_increments(self, tmp_path):
        init_run()
        for _ in range(3):
            trace(task="x", inputs=[], outputs=[])
        entries = _read_entries(tmp_path)
        assert [e["seq"] for e in entries] == [0, 1, 2]

    def test_append_does_not_rewrite_earlier_lines(self, tmp_path):
        init_run()
        trace(task="a", inputs=[], outputs=[])
        runs_dir = tmp_path / "state" / "runs"
        trace_file = sorted(runs_dir.iterdir())[-1] / "trace.jsonl"
        content_after_one = trace_file.read_text()
        trace(task="b", inputs=[], outputs=[])
        content_after_two = trace_file.read_text()
        assert content_after_two.startswith(content_after_one)

    def test_input_hashes_computed(self, tmp_path):
        init_run()
        state_dir = tmp_path / "state"
        (state_dir / "inputs").mkdir(parents=True, exist_ok=True)
        (state_dir / "inputs" / "req.md").write_text("hello")
        trace(task="t", inputs=["inputs/req.md"], outputs=[])
        entries = _read_entries(tmp_path)
        h = entries[0]["inputs"]["inputs/req.md"]
        assert h is not None
        assert len(h) == 64  # full SHA-256 hex

    def test_output_hashes_computed(self, tmp_path):
        init_run()
        state_dir = tmp_path / "state"
        (state_dir / "build").mkdir(parents=True, exist_ok=True)
        (state_dir / "build" / "out.md").write_text("result")
        trace(task="t", inputs=[], outputs=["build/out.md"])
        entries = _read_entries(tmp_path)
        h = entries[0]["outputs"]["build/out.md"]
        assert h is not None
        assert len(h) == 64

    def test_external_paths_hash_to_none_without_base(self, tmp_path):
        """External paths without external_base still hash to None."""
        init_run()
        trace(task="t", inputs=[], outputs=["<external>:app.py"])
        entries = _read_entries(tmp_path)
        assert entries[0]["outputs"]["<external>:app.py"] is None

    def test_external_paths_hashed_with_base(self, tmp_path):
        """External paths are hashed when external_base is provided."""
        init_run()
        ext_dir = tmp_path / "project"
        ext_dir.mkdir()
        (ext_dir / "app.py").write_text("print('hello')")
        trace(
            task="t",
            inputs=[],
            outputs=["<external>:app.py"],
            external_base=ext_dir,
        )
        entries = _read_entries(tmp_path)
        h = entries[0]["outputs"]["<external>:app.py"]
        assert h is not None
        assert len(h) == 64

    def test_external_missing_file_hashes_to_none_with_base(self, tmp_path):
        """External paths for missing files still return None even with base."""
        init_run()
        ext_dir = tmp_path / "project"
        ext_dir.mkdir()
        trace(
            task="t",
            inputs=[],
            outputs=["<external>:missing.py"],
            external_base=ext_dir,
        )
        entries = _read_entries(tmp_path)
        assert entries[0]["outputs"]["<external>:missing.py"] is None

    def test_missing_file_hashes_to_none(self, tmp_path):
        init_run()
        trace(task="t", inputs=["does/not/exist.md"], outputs=[])
        entries = _read_entries(tmp_path)
        assert entries[0]["inputs"]["does/not/exist.md"] is None

    def test_model_and_prompt_hash_included(self, tmp_path):
        init_run()
        trace(task="t", inputs=[], outputs=[], model="gpt-4", prompt_hash="abc123")
        entries = _read_entries(tmp_path)
        assert entries[0]["model"] == "gpt-4"
        assert entries[0]["prompt_hash"] == "abc123"

    def test_model_defaults_to_none(self, tmp_path):
        init_run()
        trace(task="t", inputs=[], outputs=[])
        entries = _read_entries(tmp_path)
        assert entries[0]["model"] is None
        assert entries[0]["prompt_hash"] is None

    def test_extra_field_included_when_provided(self, tmp_path):
        init_run()
        trace(
            task="t",
            inputs=[],
            outputs=[],
            extra={"gate": "arch", "selected": "A"},
        )
        entries = _read_entries(tmp_path)
        assert entries[0]["extra"] == {"gate": "arch", "selected": "A"}

    def test_extra_field_absent_when_not_provided(self, tmp_path):
        init_run()
        trace(task="t", inputs=[], outputs=[])
        entries = _read_entries(tmp_path)
        assert "extra" not in entries[0]


# ── Config snapshot ──────────────────────────────────────────────────────


class TestConfigSnapshot:
    def test_init_run_copies_config_to_run_dir(self, tmp_path):
        (tmp_path / "config.yml").write_text("llm:\n  provider: test\n")
        run_id = init_run()
        snapshot = tmp_path / "state" / "runs" / run_id / "config_snapshot.yml"
        assert snapshot.exists()
        assert "provider: test" in snapshot.read_text()

    def test_init_run_no_config_no_snapshot(self, tmp_path):
        """If config.yml doesn't exist, no snapshot is created."""
        run_id = init_run()
        snapshot = tmp_path / "state" / "runs" / run_id / "config_snapshot.yml"
        assert not snapshot.exists()


# ── Full SHA-256 prompt hashes ────────────────────────────────────────────


class TestFullPromptHash:
    def test_hash_prompt_returns_full_sha256(self):
        from engine.tracer import hash_prompt

        h = hash_prompt("test prompt")
        assert len(h) == 64
        int(h, 16)  # valid hex


# ── Integrity verification ───────────────────────────────────────────────────


class TestVerifyIntegrity:
    def test_valid_chain_passes(self, tmp_path):
        run_id = init_run()
        trace(task="a", inputs=[], outputs=[])
        trace(task="b", inputs=[], outputs=[])
        trace(task="c", inputs=[], outputs=[])
        ok, errors = verify_trace_integrity(run_id)
        assert ok is True
        assert errors == []

    def test_single_entry_verifies(self, tmp_path):
        run_id = init_run()
        trace(task="solo", inputs=[], outputs=[])
        ok, errors = verify_trace_integrity(run_id)
        assert ok is True
        assert errors == []

    def test_tampered_task_detected(self, tmp_path):
        run_id = init_run()
        trace(task="a", inputs=[], outputs=[])
        trace(task="b", inputs=[], outputs=[])
        # Tamper: change the task name without recomputing HMAC
        trace_file = tmp_path / "state" / "runs" / run_id / "trace.jsonl"
        lines = trace_file.read_text().strip().splitlines()
        entry = json.loads(lines[0])
        entry["task"] = "TAMPERED"
        lines[0] = json.dumps(entry, separators=(",", ":"))
        trace_file.write_text("\n".join(lines) + "\n")
        ok, errors = verify_trace_integrity(run_id)
        assert ok is False
        assert any("HMAC mismatch" in e for e in errors)

    def test_recomputed_chain_without_key_fails(self, tmp_path):
        """Even if an attacker recomputes all hashes, they can't forge valid HMACs
        without the key. This is the critical improvement over plain SHA-256."""
        import hashlib

        run_id = init_run()
        trace(task="a", inputs=[], outputs=[])
        trace(task="b", inputs=[], outputs=[])
        # Attack: modify entry AND try to recompute hash (with wrong key)
        trace_file = tmp_path / "state" / "runs" / run_id / "trace.jsonl"
        lines = trace_file.read_text().strip().splitlines()
        entry = json.loads(lines[0])
        entry.pop("entry_hash")  # Remove so attacker must recompute
        entry["task"] = "TAMPERED"
        # Attacker uses plain SHA-256 (no key) to compute new hash
        fake_hash = hashlib.sha256(
            json.dumps(entry, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        entry["entry_hash"] = fake_hash
        # Also update chain forward
        entry2 = json.loads(lines[1])
        entry2.pop("entry_hash")
        entry2["prev_hash"] = fake_hash
        fake_hash2 = hashlib.sha256(
            json.dumps(entry2, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        entry2["entry_hash"] = fake_hash2
        lines[0] = json.dumps(entry, separators=(",", ":"))
        lines[1] = json.dumps(entry2, separators=(",", ":"))
        trace_file.write_text("\n".join(lines) + "\n")
        ok, errors = verify_trace_integrity(run_id)
        assert ok is False, "Attacker-recomputed chain must NOT verify"

    def test_broken_prev_hash_chain_detected(self, tmp_path):
        run_id = init_run()
        trace(task="a", inputs=[], outputs=[])
        trace(task="b", inputs=[], outputs=[])
        # Break chain: change prev_hash on second entry
        trace_file = tmp_path / "state" / "runs" / run_id / "trace.jsonl"
        lines = trace_file.read_text().strip().splitlines()
        entry = json.loads(lines[1])
        entry["prev_hash"] = "f" * 64
        # Even recomputing with the right key won't save prev_hash mismatch
        lines[1] = json.dumps(entry, separators=(",", ":"))
        trace_file.write_text("\n".join(lines) + "\n")
        ok, errors = verify_trace_integrity(run_id)
        assert ok is False
        assert any("prev_hash mismatch" in e or "HMAC mismatch" in e for e in errors)

    def test_missing_trace_file(self, tmp_path):
        run_id = init_run()
        # No trace entries written — file does not exist
        ok, errors = verify_trace_integrity(run_id)
        assert ok is False
        assert any("not found" in e for e in errors)

    def test_empty_trace_file(self, tmp_path):
        run_id = init_run()
        trace_file = tmp_path / "state" / "runs" / run_id / "trace.jsonl"
        trace_file.write_text("")
        ok, errors = verify_trace_integrity(run_id)
        assert ok is False
        assert any("empty" in e for e in errors)

    def test_deleted_entry_breaks_chain(self, tmp_path):
        run_id = init_run()
        trace(task="a", inputs=[], outputs=[])
        trace(task="b", inputs=[], outputs=[])
        trace(task="c", inputs=[], outputs=[])
        # Delete middle entry
        trace_file = tmp_path / "state" / "runs" / run_id / "trace.jsonl"
        lines = trace_file.read_text().strip().splitlines()
        del lines[1]
        trace_file.write_text("\n".join(lines) + "\n")
        ok, errors = verify_trace_integrity(run_id)
        assert ok is False

    def test_missing_hmac_key_fails_verification(self, tmp_path):
        """If the HMAC key is deleted, verification fails with a clear message."""
        run_id = init_run()
        trace(task="a", inputs=[], outputs=[])
        # Delete the key at the new (P0-3) location.
        key_path = tmp_path / "keys" / f"{run_id}.key"
        key_path.unlink()
        ok, errors = verify_trace_integrity(run_id)
        assert ok is False
        assert any("HMAC key" in e for e in errors)

    def test_seq_gap_detected(self, tmp_path):
        """Seq numbers must be contiguous."""
        run_id = init_run()
        trace(task="a", inputs=[], outputs=[])
        trace(task="b", inputs=[], outputs=[])
        trace(task="c", inputs=[], outputs=[])
        # Delete middle entry — seq gap 0,2
        trace_file = tmp_path / "state" / "runs" / run_id / "trace.jsonl"
        lines = trace_file.read_text().strip().splitlines()
        del lines[1]
        trace_file.write_text("\n".join(lines) + "\n")
        ok, errors = verify_trace_integrity(run_id)
        assert ok is False
        assert any("seq mismatch" in e for e in errors)


# ── _compute_entry_hmac ──────────────────────────────────────────────────────


class TestComputeEntryHmac:
    def test_deterministic(self):
        key = b"test_key_32_bytes_______________"
        entry = {"task": "x", "seq": 0, "prev_hash": GENESIS_HASH}
        assert _compute_entry_hmac(entry, key) == _compute_entry_hmac(entry, key)

    def test_different_entries_different_hashes(self):
        key = b"test_key_32_bytes_______________"
        a = {"task": "x", "seq": 0}
        b = {"task": "y", "seq": 0}
        assert _compute_entry_hmac(a, key) != _compute_entry_hmac(b, key)

    def test_different_keys_different_hashes(self):
        entry = {"task": "x", "seq": 0}
        h1 = _compute_entry_hmac(entry, b"key_one_32_bytes________________")
        h2 = _compute_entry_hmac(entry, b"key_two_32_bytes________________")
        assert h1 != h2

    def test_returns_64_char_hex(self):
        h = _compute_entry_hmac({"a": 1}, b"key")
        assert len(h) == 64
        int(h, 16)


# ── P0-3: HMAC key relocation (baseline tier) ────────────────────────────────


class TestHmacKeyRelocation:
    """Key-storage layout + perms + env-var override + migration shim."""

    def test_trace_key_written_with_0600_perms(self, tmp_path):
        run_id = init_run()
        key_path = tmp_path / "keys" / f"{run_id}.key"
        assert key_path.exists()
        mode = key_path.stat().st_mode & 0o777
        # 0600 = owner read/write only. No group / other bits set.
        assert mode & 0o077 == 0, f"key file mode is {oct(mode)}, expected 0600-clean"

    def test_trace_key_dir_created_with_0700_perms(self, tmp_path):
        init_run()
        keys_dir = tmp_path / "keys"
        assert keys_dir.is_dir()
        mode = keys_dir.stat().st_mode & 0o777
        # 0700 = owner-only dir. No group / other bits.
        assert mode & 0o077 == 0, f"keys dir mode is {oct(mode)}, expected 0700-clean"

    def test_env_var_override_respected(self, tmp_path, monkeypatch):
        """AE_TRACE_KEY_DIR pointing at an arbitrary absolute path writes
        the key there, not at ~/.autonomy_engine/keys/."""
        custom = tmp_path / "elsewhere" / "ae-keys"
        monkeypatch.setenv("AE_TRACE_KEY_DIR", str(custom))
        run_id = init_run()
        assert (custom / f"{run_id}.key").exists()
        # The default location must NOT have been used.
        default = tmp_path.home() / ".autonomy_engine" / "keys" / f"{run_id}.key"
        # Soft-check: we don't want the test leaving artifacts in $HOME, so
        # assert the resolved dir matches what we set.
        assert custom.is_dir()
        assert default != (custom / f"{run_id}.key")

    def test_migration_shim_moves_old_key_and_writes_breadcrumb(self, tmp_path, monkeypatch):
        """A run created before P0-3 has its key at state/runs/<rid>/.trace_key.
        On first read via _load_hmac_key, the shim moves it to the new path
        and leaves a breadcrumb file."""
        # Manually simulate a legacy run: state dir + trace.jsonl + old key,
        # but NO new-location key.
        run_id = "legacy1234ab"
        run_dir = tmp_path / "state" / "runs" / run_id
        run_dir.mkdir(parents=True)
        legacy_key = run_dir / ".trace_key"
        legacy_key.write_bytes(b"x" * 32)
        legacy_key.chmod(0o600)

        # The fixture set AE_TRACE_KEY_DIR=tmp_path/keys, so the new
        # location is tmp_path/keys/<run_id>.key — does not exist yet.
        new_path = tmp_path / "keys" / f"{run_id}.key"
        assert not new_path.exists()

        # Trigger migration.
        loaded = tracer._load_hmac_key(run_id)
        assert loaded == b"x" * 32

        # Old key gone, new key present, breadcrumb in old dir.
        assert not legacy_key.exists()
        assert new_path.exists()
        breadcrumb = run_dir / ".trace_key_moved"
        assert breadcrumb.exists()
        txt = breadcrumb.read_text()
        assert "relocated to" in txt.lower()
        assert str(new_path) in txt

    def test_missing_key_dir_creates_with_proper_perms(self, tmp_path, monkeypatch):
        """If the override dir does not exist yet, init_run must create it
        with 0700 and the file with 0600 — same invariants as the default."""
        fresh = tmp_path / "never-created-before" / "keys"
        assert not fresh.exists()
        monkeypatch.setenv("AE_TRACE_KEY_DIR", str(fresh))
        run_id = init_run()
        assert fresh.is_dir()
        assert (fresh.stat().st_mode & 0o077) == 0
        key_path = fresh / f"{run_id}.key"
        assert key_path.exists()
        assert (key_path.stat().st_mode & 0o077) == 0

    def test_keyring_backend_path_parses(self, monkeypatch):
        """AE_TRACE_KEY_DIR=keyring:<service> is parsed into a
        ('keyring', <service>) tuple by the resolver, regardless of
        whether the keyring library is installed — the actual read/write
        is deferred to keyring and will raise at that point if absent."""
        monkeypatch.setenv("AE_TRACE_KEY_DIR", "keyring:autonomy-engine-test")
        loc = tracer._resolve_key_dir()
        assert loc == ("keyring", "autonomy-engine-test")

    def test_keyring_backend_missing_service_raises(self, monkeypatch):
        """`keyring:` with no service name is a configuration error."""
        monkeypatch.setenv("AE_TRACE_KEY_DIR", "keyring:")
        with pytest.raises(ValueError, match="service name"):
            tracer._resolve_key_dir()
