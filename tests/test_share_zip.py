"""Tests for `make share-zip` — secret-scan gating and exclusion list."""

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MAKEFILE = REPO_ROOT / "Makefile"
ZIPIGNORE = REPO_ROOT / ".zipignore"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="Makefile-based target requires bash; skip on Windows runners.",
)


@pytest.fixture
def tmp_repo(tmp_path):
    """Stage a tmp 'repo' with the Makefile and .zipignore copied in."""
    shutil.copy(MAKEFILE, tmp_path / "Makefile")
    shutil.copy(ZIPIGNORE, tmp_path / ".zipignore")
    return tmp_path


def _run_share_zip(cwd):
    return subprocess.run(
        ["make", "share-zip"],
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )


def test_share_zip_fails_when_env_contains_secret(tmp_repo):
    """A .env file with a credential pattern must abort the zip build."""
    (tmp_repo / ".env").write_text("ANTHROPIC_API_KEY=sk-ant-FAKE123\n")
    (tmp_repo / "src.py").write_text("x = 1\n")

    result = _run_share_zip(tmp_repo)

    assert result.returncode != 0, (
        f"share-zip should fail when .env contains a secret pattern.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    combined = result.stdout + result.stderr
    assert ".env" in combined, (
        f"Expected the .env path to be named in the failure output.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    # No zip should have been produced.
    assert not list(tmp_repo.glob("autonomy_engine_*.zip"))


def test_share_zip_succeeds_on_clean_repo(tmp_repo):
    """A repo with no secrets builds a zip and excludes secret-class paths."""
    (tmp_repo / "src.py").write_text("x = 1\n")
    (tmp_repo / "README.md").write_text("# clean repo\n")
    # Populate state/ to verify it's excluded from the zip.
    (tmp_repo / "state").mkdir()
    (tmp_repo / "state" / "run.json").write_text('{"hello": "world"}\n')

    result = _run_share_zip(tmp_repo)

    assert result.returncode == 0, (
        f"share-zip should succeed on a clean repo.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    zips = list(tmp_repo.glob("autonomy_engine_*.zip"))
    assert len(zips) == 1, f"Expected exactly one zip, found: {zips}"

    with zipfile.ZipFile(zips[0]) as zf:
        names = set(zf.namelist())

    # state/ must not appear (in any form).
    assert not any(n.startswith("state/") or n == "state/" for n in names), (
        f"state/ should be excluded from zip but found entries: "
        f"{[n for n in names if 'state' in n]}"
    )
    # .env must not appear (no .env was created here, but verify regardless).
    assert ".env" not in names
    # Sanity: real source file should be included.
    assert "src.py" in names
