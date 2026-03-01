"""Render engine-readable artifacts from a validated ProjectSpec."""

from pathlib import Path

import yaml

from engine.context import get_state_dir
from intake.schema import ProjectSpec


def render_all(spec: ProjectSpec) -> list[str]:
    """Generate all state/inputs/ artifacts from the spec. Returns list of written paths."""
    state_dir = get_state_dir()
    inputs_dir = state_dir / "inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)

    written = []

    written.append(_write(inputs_dir / "REQUIREMENTS.md", _render_requirements(spec), state_dir))
    written.append(_write(inputs_dir / "CONSTRAINTS.md", _render_constraints(spec), state_dir))
    written.append(_write(inputs_dir / "NON_GOALS.md", _render_non_goals(spec), state_dir))
    written.append(
        _write(inputs_dir / "ACCEPTANCE_CRITERIA.md", _render_acceptance(spec), state_dir)
    )
    written.append(_write(inputs_dir / "project_spec.yml", _render_spec_yaml(spec), state_dir))

    return written


def _write(path: Path, content: str, state_dir: Path) -> str:
    path.write_text(content)
    return str(path.relative_to(state_dir))


def _render_requirements(spec: ProjectSpec) -> str:
    lines = [
        "# Requirements",
        "",
        "## Overview",
        f"{spec.project.description}",
        "",
        "## Functional Requirements",
    ]
    for i, req in enumerate(spec.requirements.functional, 1):
        lines.append(f"{i}. {req}")

    lines.append("")
    lines.append("## Non-Functional Requirements")
    if spec.requirements.non_functional:
        for i, req in enumerate(spec.requirements.non_functional, 1):
            lines.append(f"{i}. {req}")
    else:
        lines.append("None specified.")

    lines.append("")
    lines.append("## Dependencies")
    if spec.constraints.tech_stack:
        for dep in spec.constraints.tech_stack:
            lines.append(f"- {dep}")
    else:
        lines.append("None specified.")

    lines.append("")
    return "\n".join(lines)


def _render_constraints(spec: ProjectSpec) -> str:
    lines = [
        "# Constraints",
        "",
        "## Technical Constraints",
    ]
    if spec.constraints.tech_stack:
        for item in spec.constraints.tech_stack:
            lines.append(f"- {item}")
    else:
        lines.append("None specified.")

    lines.append("")
    lines.append("## Performance Constraints")
    lines.append(spec.constraints.performance or "None specified.")

    lines.append("")
    lines.append("## Security Constraints")
    lines.append(spec.constraints.security or "None specified.")

    lines.append("")
    return "\n".join(lines)


def _render_non_goals(spec: ProjectSpec) -> str:
    lines = [
        "# Non-Goals",
        "",
        "## Explicitly Out of Scope",
    ]
    if spec.non_goals:
        for item in spec.non_goals:
            lines.append(f"- {item}")
    else:
        lines.append("None specified.")

    lines.append("")
    lines.append("## Scope Boundary: Human Judgment")
    lines.append(
        "This engine automates structured build workflows. It does **not** replace human judgment for:"
    )
    lines.append("- Ethical decisions about what should be built")
    lines.append("- Final sign-off on production deployments")
    lines.append("- Security review of generated code")
    lines.append("- Architectural decisions with long-term organizational impact")
    lines.append("")
    lines.append(
        "The decision gate system exists precisely to enforce these boundaries. "
        "Any task that requires subjective judgment must route through a decision gate, "
        "not bypass it."
    )
    lines.append("")
    return "\n".join(lines)


def _render_acceptance(spec: ProjectSpec) -> str:
    lines = [
        "# Acceptance Criteria",
        "",
    ]
    for i, criterion in enumerate(spec.acceptance_criteria, 1):
        lines.append(f"{i}. {criterion}")

    lines.append("")
    return "\n".join(lines)


def _render_spec_yaml(spec: ProjectSpec) -> str:
    """Write the raw spec as YAML for machine-readability."""
    return yaml.dump(
        spec.model_dump(mode="json"),
        default_flow_style=False,
        sort_keys=False,
    )
