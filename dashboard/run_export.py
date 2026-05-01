"""Run export — bundle a completed pipeline run as a downloadable .zip.

The dashboard's Run Outputs page exposes this as a download button so visitors
can take their generated project home and run it locally.  The bundle has two
parts, kept clearly separate:

    code/        the runnable project — sourced from the extracted project
                 tree (see tasks.extract.extracted_project_dir), plus the
                 build inventory at state/build/MANIFEST.md
    _receipts/   the build provenance — design contract, verification report,
                 audit trail — useful for review or reproducing the result

Run-specific files (trace, decisions) are pulled from state/runs/<run_id>/.
Everything else is project-level under state/.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from tasks.extract import extracted_project_dir

# Project-level artifacts copied into _receipts/. Path is relative to state/.
_RECEIPT_FILES = [
    "inputs/REQUIREMENTS.md",
    "inputs/CONSTRAINTS.md",
    "inputs/NON_GOALS.md",
    "inputs/ACCEPTANCE_CRITERIA.md",
    "inputs/project_spec.yml",
    "designs/ARCHITECTURE.md",
    "designs/DESIGN_CONTRACT.json",
    "implementations/FILE_MANIFEST.json",
    "tests/TEST_RESULTS.md",
    "tests/VERIFICATION.md",
]


def build_run_zip(project_dir: Path, run_id: str) -> bytes:
    """Return an in-memory zip bundling a run's code + receipts.

    Missing files are silently skipped — useful for failed or partial runs
    where the bundle should still let the user inspect what *did* land.
    """
    state_dir = project_dir / "state"
    build_dir = state_dir / "build"  # holds MANIFEST.md only — code lives elsewhere
    run_dir = state_dir / "runs" / run_id
    code_dir = extracted_project_dir(project_dir)  # the actual generated project tree

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # Top-level README explaining what's in the bundle.
        zf.writestr("README.md", _bundle_readme(project_dir, run_id))

        # Generated code → code/. The extractor writes the project tree as a
        # sibling of the engine root (see tasks.extract.extracted_project_dir),
        # not under state/build/, which only ever contains the build manifest.
        if code_dir is not None and code_dir.is_dir():
            for path in sorted(code_dir.rglob("*")):
                if path.is_file():
                    arcname = "code/" + str(path.relative_to(code_dir))
                    zf.write(path, arcname)

        # The build manifest lives in state/build/ alongside the receipts but
        # belongs *with* the code in the bundle so users see the inventory next
        # to the files it describes.
        build_manifest = build_dir / "MANIFEST.md"
        if build_manifest.is_file():
            zf.write(build_manifest, "code/MANIFEST.md")

        # Project-level receipts → _receipts/
        for rel in _RECEIPT_FILES:
            src = state_dir / rel
            if src.is_file():
                zf.write(src, f"_receipts/{Path(rel).name}")

        # Run-specific receipts → _receipts/
        trace = run_dir / "trace.jsonl"
        if trace.is_file():
            zf.write(trace, f"_receipts/trace_{run_id}.jsonl")

        decisions_dir = run_dir / "decisions"
        if decisions_dir.is_dir():
            for path in sorted(decisions_dir.glob("*.json")):
                zf.write(path, f"_receipts/decisions/{path.name}")

        config_snapshot = run_dir / "config_snapshot.yml"
        if config_snapshot.is_file():
            zf.write(config_snapshot, "_receipts/config_snapshot.yml")

    return buf.getvalue()


def _bundle_readme(project_dir: Path, run_id: str) -> str:
    """Render the top-level README that ships inside the zip."""
    state_dir = project_dir / "state"
    code_dir = extracted_project_dir(project_dir)
    code_files = sorted(code_dir.rglob("*")) if code_dir is not None and code_dir.is_dir() else []
    code_count = sum(1 for p in code_files if p.is_file())

    if code_count > 0:
        code_section = (
            f"`code/` contains the {code_count} file(s) the pipeline produced. "
            "Open the `code/MANIFEST.md` (if present) for a full inventory, "
            "then follow the project's own README for setup instructions."
        )
    else:
        code_section = (
            "`code/` is empty — this run did not reach the extraction stage, "
            "so there are no generated files. The receipts below still capture "
            "everything that *did* happen, which is useful for debugging."
        )

    manifest_lines = []
    for rel in _RECEIPT_FILES:
        if (state_dir / rel).is_file():
            manifest_lines.append(f"- `_receipts/{Path(rel).name}`")
    if (state_dir / "runs" / run_id / "trace.jsonl").is_file():
        manifest_lines.append(f"- `_receipts/trace_{run_id}.jsonl`")
    if (state_dir / "runs" / run_id / "config_snapshot.yml").is_file():
        manifest_lines.append("- `_receipts/config_snapshot.yml`")
    receipts_listing = "\n".join(manifest_lines) if manifest_lines else "_(no receipts available)_"

    return (
        f"# {project_dir.name} — autonomy-engine run {run_id}\n\n"
        f"This bundle was produced by the Autonomy Engine "
        f"(<https://github.com/Kaden-G/autonomy_engine>). It has two parts:\n\n"
        f"## `code/` — the generated project\n\n"
        f"{code_section}\n\n"
        f"## `_receipts/` — build provenance\n\n"
        f"Documents that explain *why* the code looks the way it does and *how* "
        f"the pipeline verified it:\n\n"
        f"{receipts_listing}\n\n"
        f"The audit trail (`trace_*.jsonl`) is HMAC-chained — every entry "
        f"references the previous one's digest, so any tampering is detectable. "
        f"Verify with `python -m engine.verify_trace --run-id {run_id}`.\n"
    )
