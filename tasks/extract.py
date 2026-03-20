"""Extract task — load FILE_MANIFEST.json and write files to a standalone project folder.

Includes a file-count circuit breaker that halts extraction when the
manifest contains an unreasonable number of files, which is a strong
signal that the design/implement stages overscoped.
"""

import json
import logging
import re
from pathlib import Path

import yaml
from pydantic import ValidationError
from prefect import task

from engine.context import get_project_dir
from engine.state_loader import load_state_file, save_state_file
from engine.tier_context import is_mvp
from engine.tracer import trace
from tasks.manifest_schema import FileManifest

logger = logging.getLogger(__name__)

# ── Circuit breaker thresholds ───────────────────────────────────────────────
# If the manifest exceeds these limits, extraction is halted.
# The MVP limits are deliberately tight — a working MVP should be lean.

_MAX_FILES_MVP = 80
_MAX_FILES_PREMIUM = 250
_MAX_TOTAL_BYTES_MVP = 750_000        # ~750 KB of source code
_MAX_TOTAL_BYTES_PREMIUM = 5_000_000  # ~5 MB


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


class ExtractionCircuitBreaker(RuntimeError):
    """Raised when the manifest exceeds safe extraction limits.

    Attributes:
        file_count: Number of files in the manifest.
        total_bytes: Total content size in bytes.
        limit_files: The file count limit that was exceeded.
        limit_bytes: The byte size limit that was exceeded.
    """

    def __init__(
        self,
        message: str,
        file_count: int,
        total_bytes: int,
        limit_files: int,
        limit_bytes: int,
    ):
        super().__init__(message)
        self.file_count = file_count
        self.total_bytes = total_bytes
        self.limit_files = limit_files
        self.limit_bytes = limit_bytes


def _check_extraction_limits(manifest: FileManifest) -> None:
    """Halt extraction if the manifest exceeds safe limits.

    Raises :class:`ExtractionCircuitBreaker` with a descriptive message
    explaining what was exceeded and suggesting remediation.
    """
    mvp = is_mvp()
    max_files = _MAX_FILES_MVP if mvp else _MAX_FILES_PREMIUM
    max_bytes = _MAX_TOTAL_BYTES_MVP if mvp else _MAX_TOTAL_BYTES_PREMIUM
    tier_label = "MVP" if mvp else "Premium"

    file_count = len(manifest.files)
    total_bytes = sum(len(entry.content.encode("utf-8")) for entry in manifest.files)

    violations: list[str] = []
    if file_count > max_files:
        violations.append(
            f"File count ({file_count}) exceeds {tier_label} limit of {max_files}"
        )
    if total_bytes > max_bytes:
        violations.append(
            f"Total size ({total_bytes:,} bytes) exceeds {tier_label} "
            f"limit of {max_bytes:,} bytes"
        )

    if violations:
        msg = (
            f"Extraction circuit breaker tripped ({tier_label} tier):\n"
            + "\n".join(f"  - {v}" for v in violations)
            + "\n\nThis usually means the design stage overscoped the architecture. "
            "Consider:\n"
            "  1. Re-running with MVP tier to get tier-aware scope constraints\n"
            "  2. Reducing features in the project spec\n"
            "  3. Increasing the limits in tasks/extract.py if this project "
            "genuinely needs more files"
        )
        logger.error("Circuit breaker: %s", msg)
        raise ExtractionCircuitBreaker(
            msg,
            file_count=file_count,
            total_bytes=total_bytes,
            limit_files=max_files,
            limit_bytes=max_bytes,
        )

    logger.info(
        "Extraction limits check passed (%s tier): %d files, %s bytes "
        "(limits: %d files, %s bytes).",
        tier_label, file_count, f"{total_bytes:,}",
        max_files, f"{max_bytes:,}",
    )


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


_DEV_TOOLS = ["ruff", "mypy"]


def _sanitize_requirements(project_dir: Path) -> None:
    """Fix common LLM mistakes in generated requirements.txt.

    1. Relax exact pins (==X.Y.Z) → compatible release (~=X.Y) so that
       hallucinated patch versions (e.g. cryptography==41.0.8) don't break pip.
    2. Inject dev tools (ruff, mypy) if not already present.
    """
    req_path = project_dir / "requirements.txt"
    if not req_path.exists():
        return

    lines = req_path.read_text().splitlines()
    sanitized: list[str] = []
    present_packages: set[str] = set()

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            sanitized.append(raw_line)
            continue

        # Extract package name (before any version specifier)
        pkg_match = re.match(r"^([A-Za-z0-9_.-]+)", line)
        if pkg_match:
            present_packages.add(pkg_match.group(1).lower())

        # Relax exact pins: cryptography==41.0.8 → cryptography~=41.0
        # This uses "compatible release" — allows 41.0.x but not 42.x
        pin_match = re.match(
            r"^([A-Za-z0-9_.-]+)==(\d+)\.(\d+)\.(\d+)(.*)", line
        )
        if pin_match:
            pkg, major, minor, _patch, extras = pin_match.groups()
            sanitized.append(f"{pkg}~={major}.{minor}{extras}")
            logger.info("Relaxed pin: %s → %s~=%s.%s", line, pkg, major, minor)
        else:
            sanitized.append(raw_line)

    # Inject dev tools if missing
    for tool in _DEV_TOOLS:
        if tool.lower() not in present_packages:
            sanitized.append(tool)
            logger.info("Injected dev tool: %s", tool)

    req_path.write_text("\n".join(sanitized) + "\n")


@task(name="extract")
def extract_project() -> None:
    """Load FILE_MANIFEST.json, validate schema, write files to project folder."""
    # Load manifest JSON
    raw_json = load_state_file("implementations/FILE_MANIFEST.json")
    manifest = _load_and_validate_manifest(raw_json)

    # Circuit breaker — halt if manifest is unreasonably large
    _check_extraction_limits(manifest)

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

    # Post-extraction: sanitize requirements.txt (relax exact pins, add dev tools)
    _sanitize_requirements(output_dir)

    # Generate and save manifest
    build_manifest = _build_manifest(written_paths, output_dir)
    save_state_file("build/MANIFEST.md", build_manifest)

    trace(
        task="extract",
        inputs=["implementations/FILE_MANIFEST.json", "inputs/project_spec.yml"],
        outputs=["build/MANIFEST.md"] + [f"<external>:{f}" for f in sorted(written_paths)],
        external_base=output_dir,
    )
