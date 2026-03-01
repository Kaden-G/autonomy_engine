"""Tests for intake.intake — from-file non-interactive mode and review flag."""

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

import engine.context
import engine.tracer as tracer
from engine.tracer import GENESIS_HASH


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path):
    """Point engine context at a temp dir and reset tracer module state."""
    engine.context.init(tmp_path)
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "inputs").mkdir(parents=True, exist_ok=True)
    tracer._run_id = None
    tracer._prev_hash = GENESIS_HASH
    tracer._seq = 0
    yield
    tracer._run_id = None
    tracer._prev_hash = GENESIS_HASH
    tracer._seq = 0


def _write_valid_spec(path: Path) -> Path:
    """Write a minimal valid project spec YAML and return its path."""
    spec = {
        "project": {
            "name": "Test Project",
            "description": "A test project for validation",
            "domain": "software",
        },
        "requirements": {
            "functional": ["Must do something"],
        },
        "constraints": {},
        "non_goals": [],
        "acceptance_criteria": ["It works"],
        "outputs": {
            "expected_artifacts": ["app.py"],
        },
    }
    spec_file = path / "project_spec.yml"
    spec_file.write_text(yaml.dump(spec, default_flow_style=False))
    return spec_file


# ── from-file: non-interactive by default ────────────────────────────────


class TestFromFileNonInteractive:
    def test_from_file_writes_without_prompts(self, tmp_path):
        """from-file should validate and write directly, no interactive prompts."""
        spec_file = _write_valid_spec(tmp_path)

        from intake.intake import main

        with patch(
            "sys.argv", ["intake", "--project-dir", str(tmp_path), "from-file", str(spec_file)]
        ):
            main()

        # Verify files were written
        inputs_dir = tmp_path / "state" / "inputs"
        assert (inputs_dir / "project_spec.yml").exists()
        assert (inputs_dir / "REQUIREMENTS.md").exists()
        assert (inputs_dir / "CONSTRAINTS.md").exists()
        assert (inputs_dir / "NON_GOALS.md").exists()
        assert (inputs_dir / "ACCEPTANCE_CRITERIA.md").exists()

    def test_from_file_does_not_call_input(self, tmp_path):
        """from-file without --review must never call input()."""
        spec_file = _write_valid_spec(tmp_path)

        from intake.intake import main

        with patch(
            "sys.argv", ["intake", "--project-dir", str(tmp_path), "from-file", str(spec_file)]
        ):
            with patch("builtins.input", side_effect=RuntimeError("should not prompt")):
                main()  # must not raise

    def test_from_file_invalid_spec_exits(self, tmp_path):
        """from-file with an invalid spec should exit with code 1."""
        bad_spec = tmp_path / "bad.yml"
        bad_spec.write_text("project:\n  name: x\n")

        from intake.intake import main

        with patch(
            "sys.argv", ["intake", "--project-dir", str(tmp_path), "from-file", str(bad_spec)]
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

    def test_from_file_preserves_spec_content(self, tmp_path):
        """from-file should write a spec that round-trips correctly."""
        spec_file = _write_valid_spec(tmp_path)

        from intake.intake import main

        with patch(
            "sys.argv", ["intake", "--project-dir", str(tmp_path), "from-file", str(spec_file)]
        ):
            main()

        written = tmp_path / "state" / "inputs" / "project_spec.yml"
        data = yaml.safe_load(written.read_text())
        assert data["project"]["name"] == "Test Project"
        assert data["requirements"]["functional"] == ["Must do something"]


# ── from-file --review: interactive mode ──────────────────────────────────


class TestFromFileReview:
    def test_review_flag_triggers_interactive_loop(self, tmp_path):
        """from-file --review should call _review_loop."""
        spec_file = _write_valid_spec(tmp_path)

        from intake.intake import main

        with patch(
            "sys.argv",
            ["intake", "--project-dir", str(tmp_path), "from-file", str(spec_file), "--review"],
        ):
            # Simulate user confirming immediately with 'c'
            with patch("builtins.input", return_value="c"):
                main()

        # Files should still be written after confirmation
        inputs_dir = tmp_path / "state" / "inputs"
        assert (inputs_dir / "project_spec.yml").exists()

    def test_review_abort_exits(self, tmp_path):
        """from-file --review with 'q' abort should exit 1."""
        spec_file = _write_valid_spec(tmp_path)

        from intake.intake import main

        with patch(
            "sys.argv",
            ["intake", "--project-dir", str(tmp_path), "from-file", str(spec_file), "--review"],
        ):
            with patch("builtins.input", return_value="q"):
                with pytest.raises(SystemExit) as exc_info:
                    main()
                assert exc_info.value.code == 1
