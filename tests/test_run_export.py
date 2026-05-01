"""Tests for the run-bundle zip exporter.

Regression coverage for the bundle that the dashboard hands to users via the
"Download project bundle" button. The exporter walks two locations — the
extracted project tree (sibling of project_dir, populated by tasks.extract)
and state/build/ (which only ever holds MANIFEST.md). A previous version
walked only state/build/, so bundles shipped the manifest with no code
files: this module locks the wiring so that bug can't return.

Each test isolates its own project_dir under tmp_path so the sibling-dir
convention doesn't bleed between tests sharing pytest's session root.
"""

from __future__ import annotations

import io
import zipfile

import yaml

from dashboard.run_export import build_run_zip


def _make_project(tmp_path, name: str = "Demo App"):
    """Create an isolated project_dir with a project spec; return (project_dir, slug)."""
    project_dir = tmp_path / "engine"
    project_dir.mkdir()
    inputs = project_dir / "state" / "inputs"
    inputs.mkdir(parents=True)
    (inputs / "project_spec.yml").write_text(
        yaml.dump({"project": {"name": name, "description": "x", "domain": "software"}})
    )
    # Slug is the public rule from tasks.extract.slugify; for these names it's trivial.
    slug = name.lower().replace(" ", "-")
    return project_dir, slug


def _zip_names(blob: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        return zf.namelist()


def test_bundle_includes_extracted_code_files(tmp_path):
    project_dir, slug = _make_project(tmp_path)

    # Mirror what tasks.extract does: write code as a sibling of project_dir.
    code_dir = project_dir.parent / slug
    code_dir.mkdir()
    (code_dir / "main.py").write_text("print('hi')\n")
    (code_dir / "requirements.txt").write_text("requests\n")

    blob = build_run_zip(project_dir, run_id="run-123")
    names = _zip_names(blob)

    assert "code/main.py" in names
    assert "code/requirements.txt" in names


def test_bundle_includes_build_manifest_under_code(tmp_path):
    project_dir, _ = _make_project(tmp_path)

    # The build manifest lives under state/build/, not the extracted tree.
    build_dir = project_dir / "state" / "build"
    build_dir.mkdir()
    (build_dir / "MANIFEST.md").write_text("# files\n- main.py\n")

    blob = build_run_zip(project_dir, run_id="run-123")
    names = _zip_names(blob)

    assert "code/MANIFEST.md" in names


def test_bundle_handles_missing_extracted_dir(tmp_path):
    """A bundle for a run that never reached extract should still build cleanly."""
    project_dir, _ = _make_project(tmp_path)
    # Note: no extracted-code dir created.

    blob = build_run_zip(project_dir, run_id="run-123")
    names = _zip_names(blob)

    assert "README.md" in names
    assert not any(n.startswith("code/") for n in names)


def test_bundle_handles_missing_spec(tmp_path):
    """No project_spec.yml means no extracted-dir resolution; bundle still builds."""
    project_dir = tmp_path / "engine"
    project_dir.mkdir()
    (project_dir / "state").mkdir()

    blob = build_run_zip(project_dir, run_id="run-123")
    names = _zip_names(blob)

    assert "README.md" in names
