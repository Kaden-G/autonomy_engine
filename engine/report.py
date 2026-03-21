"""Audit bundle exporter — package an entire run into a shareable archive.

After a pipeline run completes, this module bundles everything an auditor or
stakeholder would need to review the run: the full audit log, configuration
snapshot, test evidence, human decisions, artifact manifest, and an integrity
verification result.  The output is a compressed .tar.gz file.

Usage::

    python -m engine.report --run-id <id> [--out path] [--project-dir dir]
"""

import argparse
import io
import json
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import engine.context
from engine.tracer import verify_trace_integrity


# ── Helpers ──────────────────────────────────────────────────────────────────


def _run_dir(run_id: str) -> Path:
    """Resolve and validate the run directory exists."""
    d = engine.context.get_state_dir() / "runs" / run_id
    if not d.is_dir():
        raise FileNotFoundError(f"Run directory not found: {d}")
    return d


def _add_json_to_tar(tar: tarfile.TarFile, arcname: str, obj: object) -> None:
    """Write a JSON-serializable object directly into a tar archive."""
    data = json.dumps(obj, indent=2).encode()
    info = tarfile.TarInfo(name=arcname)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


# ── Manifest builder ────────────────────────────────────────────────────────


def build_artifact_manifest(run_id: str) -> dict:
    """Collect all input/output hashes from trace entries.

    Reads what was *claimed* in the trace (not re-hashing disk), producing
    a manifest of every artifact referenced during the run.
    """
    rd = _run_dir(run_id)
    trace_path = rd / "trace.jsonl"

    artifacts: dict[str, str | None] = {}

    if trace_path.exists():
        for line in trace_path.read_text().strip().splitlines():
            entry = json.loads(line)
            for mapping in (entry.get("inputs") or {}, entry.get("outputs") or {}):
                if isinstance(mapping, dict):
                    artifacts.update(mapping)

    return {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "artifacts": artifacts,
    }


# ── Bundle creator ──────────────────────────────────────────────────────────


def create_bundle(run_id: str, out_path: Path | str | None = None) -> Path:
    """Produce a .tar.gz audit bundle for the given run.

    Contents::

        <run_id>/
            trace.jsonl
            config_snapshot.yml      (if present)
            evidence/*.json
            decisions/*.json
            artifact_manifest.json   (rebuilt from trace entries)
            integrity.json           (verify_trace_integrity result)
    """
    rd = _run_dir(run_id)

    if out_path is None:
        out_path = Path(f"{run_id}_audit.tar.gz")
    else:
        out_path = Path(out_path)

    with tarfile.open(out_path, "w:gz") as tar:
        # trace.jsonl
        trace_path = rd / "trace.jsonl"
        if trace_path.exists():
            tar.add(trace_path, arcname=f"{run_id}/trace.jsonl")

        # config_snapshot.yml
        config_snap = rd / "config_snapshot.yml"
        if config_snap.exists():
            tar.add(config_snap, arcname=f"{run_id}/config_snapshot.yml")

        # evidence/*.json
        evidence_dir = rd / "evidence"
        if evidence_dir.is_dir():
            for f in sorted(evidence_dir.glob("*.json")):
                tar.add(f, arcname=f"{run_id}/evidence/{f.name}")

        # decisions/*.json
        decisions_dir = rd / "decisions"
        if decisions_dir.is_dir():
            for f in sorted(decisions_dir.glob("*.json")):
                tar.add(f, arcname=f"{run_id}/decisions/{f.name}")

        # artifact_manifest.json (rebuilt from trace)
        manifest = build_artifact_manifest(run_id)
        _add_json_to_tar(tar, f"{run_id}/artifact_manifest.json", manifest)

        # integrity.json
        is_valid, errors = verify_trace_integrity(run_id)
        integrity = {
            "run_id": run_id,
            "is_valid": is_valid,
            "errors": errors,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        _add_json_to_tar(tar, f"{run_id}/integrity.json", integrity)

    return out_path


# ── CLI entry point ─────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export an audit bundle for a completed engine run."
    )
    parser.add_argument("--run-id", required=True, help="Run ID to export")
    parser.add_argument("--out", default=None, help="Output .tar.gz path")
    parser.add_argument(
        "--project-dir",
        default=None,
        help="Project directory (defaults to engine root)",
    )
    args = parser.parse_args()

    engine.context.init(args.project_dir)
    bundle = create_bundle(args.run_id, args.out)
    print(f"Audit bundle written to {bundle}")


if __name__ == "__main__":
    main()
