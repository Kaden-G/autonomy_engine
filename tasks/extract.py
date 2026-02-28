"""Extract task — parse IMPLEMENTATION.md and write files to a standalone project folder."""

import re
from pathlib import Path

import yaml
from prefect import task

from engine.context import ENGINE_ROOT, get_state_dir
from engine.state_loader import load_state_file, save_state_file
from engine.tracer import trace

# Patterns for identifying filenames before code blocks
_BOLD_FILENAME = re.compile(r'\*\*([a-zA-Z0-9_./\-]+\.[a-zA-Z0-9]+)\*\*')
_HEADER_FILENAME = re.compile(r'^#{1,4}\s+`?([a-zA-Z0-9_./\-]+\.[a-zA-Z0-9]+)`?\s*$', re.MULTILINE)
_FENCE_OPEN = re.compile(r'^```[a-zA-Z]*\s*$', re.MULTILINE)
_FENCE_CLOSE = re.compile(r'^```\s*$', re.MULTILINE)


def _slugify(name: str) -> str:
    """Lowercase, replace spaces/underscores with hyphens, strip non-alphanumeric."""
    slug = name.lower().strip()
    slug = re.sub(r'[\s_]+', '-', slug)
    slug = re.sub(r'[^a-z0-9\-]', '', slug)
    return slug


def _parse_code_blocks(markdown: str) -> dict[str, str]:
    """Extract filename -> content pairs from markdown.

    Scans for bold filenames (**path/to/file.ext**) and header filenames
    (### file.ext), then captures the next fenced code block after each match.
    """
    files: dict[str, str] = {}

    # Collect all filename indicators with their positions
    indicators: list[tuple[int, str]] = []
    for m in _BOLD_FILENAME.finditer(markdown):
        indicators.append((m.end(), m.group(1)))
    for m in _HEADER_FILENAME.finditer(markdown):
        indicators.append((m.end(), m.group(1)))

    # Sort by position in the document
    indicators.sort(key=lambda x: x[0])

    for pos, filename in indicators:
        # Find the next fence opening after this indicator
        fence_open = _FENCE_OPEN.search(markdown, pos)
        if fence_open is None:
            continue

        # Make sure there isn't another filename indicator between this one
        # and the fence (which would mean this fence belongs to that later indicator)
        next_indicator_pos = None
        for other_pos, _ in indicators:
            if other_pos > pos:
                next_indicator_pos = other_pos
                break
        if next_indicator_pos is not None and fence_open.start() > next_indicator_pos:
            continue

        # Find the closing fence
        fence_close = _FENCE_CLOSE.search(markdown, fence_open.end() + 1)
        if fence_close is None:
            continue

        content = markdown[fence_open.end() + 1 : fence_close.start()]
        # Strip single trailing newline if present
        if content.endswith('\n'):
            content = content[:-1]

        files[filename] = content

    return files


def _build_manifest(extracted_files: dict[str, str], output_dir: Path) -> str:
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
    """Parse IMPLEMENTATION.md, extract code blocks, write to project folder."""
    # Load implementation markdown
    implementation = load_state_file("implementations/IMPLEMENTATION.md")

    # Load project spec to get the project name
    spec_raw = load_state_file("inputs/project_spec.yml")
    spec = yaml.safe_load(spec_raw)
    project_name = spec["project"]["name"]
    slug = _slugify(project_name)

    # Output directory is a sibling of the engine root
    output_dir = ENGINE_ROOT.parent / slug

    # Parse code blocks
    extracted = _parse_code_blocks(implementation)
    if not extracted:
        raise RuntimeError("No code blocks with filenames found in IMPLEMENTATION.md")

    # Write each file
    for filepath, content in extracted.items():
        dest = output_dir / filepath
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content + "\n")

    # Generate and save manifest
    manifest = _build_manifest(extracted, output_dir)
    save_state_file("build/MANIFEST.md", manifest)

    trace(
        task="extract",
        inputs=["implementations/IMPLEMENTATION.md", "inputs/project_spec.yml"],
        outputs=["build/MANIFEST.md"] + [f"<external>:{f}" for f in sorted(extracted)],
    )
