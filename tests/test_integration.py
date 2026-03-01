"""End-to-end integration test: extract → evidence → verify prompt formatting.

Exercises the core contract path with minimal mocking (only the LLM provider).
All filesystem, tracing, and evidence operations run against real temp directories.
"""

import json

import pytest
import yaml

import engine.context
import engine.tracer as tracer
from engine.evidence import format_evidence_for_llm, run_check, save_evidence
from engine.state_loader import load_state_file
from engine.tracer import GENESIS_HASH, init_run, verify_trace_integrity
from tasks.extract import extract_project


@pytest.fixture(autouse=True)
def _isolated_project(tmp_path):
    """Set up a complete isolated project directory with all required artifacts."""
    project_dir = tmp_path / "test_project"
    project_dir.mkdir()
    engine.context.init(project_dir)

    state_dir = project_dir / "state"
    for subdir in ("inputs", "designs", "implementations", "tests", "build", "runs"):
        (state_dir / subdir).mkdir(parents=True, exist_ok=True)

    # Write a minimal project spec
    spec = {
        "project": {
            "name": "Integration Test App",
            "description": "An app for testing the end-to-end pipeline",
            "domain": "software",
        },
        "requirements": {"functional": ["Must print hello"]},
        "constraints": {"tech_stack": ["python"]},
        "non_goals": [],
        "acceptance_criteria": ["Program prints hello to stdout"],
        "outputs": {"expected_artifacts": ["app.py"]},
    }
    (state_dir / "inputs" / "project_spec.yml").write_text(
        yaml.dump(spec, default_flow_style=False)
    )

    # Write rendered intake artifacts (as the renderer would produce)
    (state_dir / "inputs" / "REQUIREMENTS.md").write_text("# Requirements\n\n1. Must print hello\n")
    (state_dir / "inputs" / "CONSTRAINTS.md").write_text("# Constraints\n\n- python\n")
    (state_dir / "inputs" / "NON_GOALS.md").write_text("# Non-Goals\n\nNone specified.\n")
    (state_dir / "inputs" / "ACCEPTANCE_CRITERIA.md").write_text(
        "# Acceptance Criteria\n\n1. Program prints hello to stdout\n"
    )

    # Write a FILE_MANIFEST.json (as the implement task would produce)
    manifest = {
        "files": [
            {"path": "app.py", "content": "print('hello')"},
            {"path": "README.md", "content": "# Integration Test App"},
        ]
    }
    (state_dir / "implementations" / "FILE_MANIFEST.json").write_text(json.dumps(manifest))

    # Reset tracer
    tracer._run_id = None
    tracer._prev_hash = GENESIS_HASH
    tracer._seq = 0

    yield tmp_path, project_dir

    tracer._run_id = None
    tracer._prev_hash = GENESIS_HASH
    tracer._seq = 0


class TestExtractEvidenceVerifyPipeline:
    """Integration: extract files → run checks → format evidence → verify prompt."""

    def test_full_pipeline(self, _isolated_project):
        tmp_path, project_dir = _isolated_project
        run_id = init_run()

        # ── Step 1: Extract ──────────────────────────────────────────────
        # This calls the real extract task (no mocking)
        extract_project.fn()

        # Verify files were extracted to the sibling directory
        output_dir = tmp_path / "integration-test-app"
        assert output_dir.is_dir(), f"Expected extraction dir at {output_dir}"
        assert (output_dir / "app.py").exists()
        assert (output_dir / "README.md").exists()
        assert "print('hello')" in (output_dir / "app.py").read_text()

        # Verify build manifest was saved
        manifest_md = load_state_file("build/MANIFEST.md")
        assert "app.py" in manifest_md
        assert "README.md" in manifest_md

        # ── Step 2: Evidence capture ─────────────────────────────────────
        # Run a real check against the extracted project (no sandbox)
        record = run_check(
            name="smoke_test",
            command="python app.py",
            cwd=output_dir,
        )
        assert record["exit_code"] == 0
        assert "hello" in record["stdout"]
        assert len(record["stdout_hash"]) == 64
        assert record["argv"] == ["python", "app.py"]

        save_evidence(record)

        # ── Step 3: Evidence formatting ──────────────────────────────────
        from engine.evidence import load_all_evidence

        evidence = load_all_evidence()
        assert len(evidence) == 1
        assert evidence[0]["name"] == "smoke_test"

        evidence_text = format_evidence_for_llm(evidence)
        assert "smoke_test" in evidence_text
        assert "PASSED" in evidence_text
        assert "hello" in evidence_text

        # ── Step 4: Verify prompt assembly ───────────────────────────────
        # Build the verify prompt the same way verify_system() would
        acceptance = load_state_file("inputs/ACCEPTANCE_CRITERIA.md")
        requirements = load_state_file("inputs/REQUIREMENTS.md")

        from engine.context import get_prompts_dir

        prompt_template = (get_prompts_dir() / "verify.txt").read_text()
        prompt = prompt_template.format(
            evidence=evidence_text,
            acceptance_criteria=acceptance,
            requirements=requirements,
        )

        # The assembled prompt should contain all three pieces
        assert "hello" in prompt  # from evidence
        assert "prints hello to stdout" in prompt  # from acceptance criteria
        assert "Must print hello" in prompt  # from requirements

        # ── Step 5: Trace integrity ──────────────────────────────────────
        # Verify the trace chain is intact after extract wrote its entry
        ok, errors = verify_trace_integrity(run_id)
        assert ok, f"Trace integrity failed: {errors}"
