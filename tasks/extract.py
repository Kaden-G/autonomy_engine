"""Extract task — load FILE_MANIFEST.json and write files to a standalone project folder."""

import json
import re
from pathlib import Path

import yaml
from pydantic import ValidationError
from prefect import task

from engine.context import get_project_dir
from engine.state_loader import load_state_file, save_state_file
from engine.tracer import trace
from tasks.manifest_schema import FileManifest


def _slugify(name: str) -> str:
    """Lowercase, replace spaces/underscores with hyphens, strip non-alphanumeric."""
    slug = name.lower().strip()
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"[^a-z0-9\-]", "", slug)
    return slug


def _load_and_validate_manifest(raw_json: str) -> FileManifest:
    """Parse raw JSON string and validate against FileManifest schema.

    Converts JSONDecodeError and ValidationError into RuntimeError.
    """
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"FILE_MANIFEST.json is not valid JSON: {exc}") from exc

    try:
        return FileManifest.model_validate(data)
    except ValidationError as exc:
        raise RuntimeError(f"FILE_MANIFEST.json failed schema validation: {exc}") from exc


def _safe_path(output_dir: Path, filepath: str) -> Path:
    """Resolve *filepath* under *output_dir*, rejecting anything that escapes it.

    Rejects:
    - Absolute paths (/tmp/x)
    - Parent traversal (../../x)
    - Empty or whitespace-only segments
    - Any resolved path not strictly under output_dir
    """
    if not filepath or not filepath.strip():
        raise ValueError("Empty file path")

    raw = Path(filepath)

    # Reject absolute paths
    if raw.is_absolute():
        raise ValueError(f"Absolute path not allowed: {filepath}")

    # Reject .. components
    if ".." in raw.parts:
        raise ValueError(f"Parent traversal not allowed: {filepath}")

    # Reject empty segments (e.g. "src//file.py" → ('src', '', 'file.py'))
    for part in raw.parts:
        if not part or not part.strip():
            raise ValueError(f"Empty path segment in: {filepath}")

    resolved = (output_dir / raw).resolve()

    # Final containment check — resolved path must be inside the output dir
    if not resolved.is_relative_to(output_dir.resolve()):
        raise ValueError(f"Path escapes output directory: {filepath} -> {resolved}")

    return resolved


def _build_manifest(extracted_files: list[str], output_dir: Path) -> str:
    """Generate a markdown manifest listing all extracted files."""
    lines = [
        "# Extraction Manifest",
        "",
        f"**Output directory:** `{output_dir}`",
        f"**Files extracted:** {len(extracted_files)}",
        "",
        "## Files",
        "",
    ]
    for filepath in sorted(extracted_files):
        lines.append(f"- `{filepath}`")
    lines.append("")
    return "\n".join(lines)


@task(name="extract")
def extract_project() -> None:
    """Load FILE_MANIFEST.json, validate schema, write files to project folder."""
    # Load manifest JSON
    raw_json = load_state_file("implementations/FILE_MANIFEST.json")
    manifest = _load_and_validate_manifest(raw_json)

    # Load project spec to get the project name
    spec_raw = load_state_file("inputs/project_spec.yml")
    spec = yaml.safe_load(spec_raw)
    project_name = spec["project"]["name"]
    slug = _slugify(project_name)

    # Output directory is a sibling of the active project directory
    output_dir = get_project_dir().parent / slug

    # Write each file (with path safety validation)
    written_paths: list[str] = []
    for entry in manifest.files:
        dest = _safe_path(output_dir, entry.path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(entry.content + "\n")
        written_paths.append(entry.path)

    # Generate and save manifest
    build_manifest = _build_manifest(written_paths, output_dir)
    save_state_file("build/MANIFEST.md", build_manifest)

    trace(
        task="extract",
        inputs=["implementations/FILE_MANIFEST.json", "inputs/project_spec.yml"],
        outputs=["build/MANIFEST.md"] + [f"<external>:{f}" for f in sorted(written_paths)],
        external_base=output_dir,
    )
