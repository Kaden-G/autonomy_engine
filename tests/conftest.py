"""Session-wide pytest fixtures.

The sole purpose of this file (for now) is to isolate HMAC key storage
so the test suite cannot pollute the developer's real
``~/.autonomy_engine/keys/`` directory. Without this, every test that
calls ``engine.tracer.init_run()`` or otherwise triggers
``_write_hmac_key`` leaks a 32-byte key into $HOME.

We set ``AE_TRACE_KEY_DIR`` once at session start to point at a
pytest-managed tmp dir, and tests that need to override it further can
still do so with ``monkeypatch.setenv`` (per-test overrides take
precedence).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def pytest_configure(config):
    """Before any test imports, redirect AE_TRACE_KEY_DIR to a tmp dir.

    Tests that set the env var themselves (via ``monkeypatch.setenv``)
    will still get their override — monkeypatch undoes itself at teardown
    back to whatever we set here.
    """
    if os.environ.get("AE_TRACE_KEY_DIR"):
        # User or CI set it explicitly — respect that.
        return
    session_keys_dir = Path(tempfile.mkdtemp(prefix="ae-test-keys-"))
    os.environ["AE_TRACE_KEY_DIR"] = str(session_keys_dir)
    config._ae_test_keys_dir = session_keys_dir  # stash for cleanup


def pytest_unconfigure(config):
    """Clean up the session key dir if we created it."""
    session_dir = getattr(config, "_ae_test_keys_dir", None)
    if session_dir is None:
        return
    import shutil

    shutil.rmtree(session_dir, ignore_errors=True)
