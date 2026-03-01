"""Intake CLI — collect, validate, normalize project specs before engine execution."""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import yaml
from pydantic import ValidationError

from engine.context import ENGINE_ROOT, get_prompts_dir, get_state_dir, init as init_context
from intake.renderer import render_all
from intake.schema import (
    Constraints,
    Domain,
    Outputs,
    ProjectInfo,
    ProjectSpec,
    Requirements,
)

# ── Section registry ──────────────────────────────────────────────────────────
# Each section has: display name, collect function, fields it populates.
# This drives both the initial walk-through and the edit loop.

SECTIONS = [
    ("project", "Project Info"),
    ("functional", "Functional Requirements"),
    ("non_functional", "Non-Functional Requirements"),
    ("constraints", "Constraints"),
    ("non_goals", "Non-Goals"),
    ("acceptance", "Acceptance Criteria"),
    ("outputs", "Expected Outputs"),
]


# ── Prompt helpers ────────────────────────────────────────────────────────────


def _prompt(label: str, required: bool = True, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    suffix += " (required)" if required and not default else ""
    while True:
        value = input(f"  {label}{suffix}: ").strip()
        if not value and default:
            return default
        if not value and required:
            print("    ^ This field is required.")
            continue
        return value


def _prompt_list(label: str, min_items: int = 0) -> list[str]:
    print(f"  {label}")
    print("    Paste or type items (one per line, or semicolon-separated).")
    print("    Empty line to finish.")
    items = []
    while True:
        value = input("    - ").strip()
        if not value:
            if len(items) >= min_items:
                break
            print(f"    ^ At least {min_items} item(s) required.")
            continue
        parts = [p.strip() for p in value.split(";") if p.strip()]
        items.extend(parts)
    # Show what was collected and confirm
    if items:
        print(f"    Collected {len(items)} item(s):")
        for item in items:
            print(f"      * {item}")
        if not _prompt_yn("    Keep these items", default=True):
            print("    Discarded. Re-enter items:")
            return _prompt_list(label, min_items)
    return items


def _prompt_choice(label: str, choices: list[str], default: str = "") -> str:
    options = " / ".join(choices)
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"  {label} ({options}){suffix}: ").strip().lower()
        if not value and default:
            return default
        if value in [c.lower() for c in choices]:
            return value
        print(f"    ^ Choose one of: {options}")


def _prompt_yn(label: str, default: bool = False) -> bool:
    d = "Y/n" if default else "y/N"
    value = input(f"  {label} [{d}]: ").strip().lower()
    if not value:
        return default
    return value in ("y", "yes")


# ── Section collectors ────────────────────────────────────────────────────────
# Each returns a dict of field values for that section.


def _collect_project(current: dict) -> dict:
    print("\n>> Project Info")
    name = _prompt("Name", default=current.get("name", ""))
    description = _prompt("Description (what does it do)", default=current.get("description", ""))
    print("    Domain determines validation rules (e.g., infra requires security constraints).")
    print("      software = apps, APIs, CLIs  |  data = pipelines, ETL, analytics")
    print("      ml = models, training, inference  |  infra = cloud, networking, IaC")
    domain = _prompt_choice(
        "Domain", ["software", "data", "ml", "infra"], current.get("domain", "software")
    )
    return {"name": name, "description": description, "domain": domain}


def _collect_functional(current: dict) -> dict:
    print("\n>> Functional Requirements")
    _show_current_list(current.get("functional", []))
    items = _prompt_list("List each requirement", min_items=1)
    return {"functional": items}


def _collect_non_functional(current: dict) -> dict:
    print("\n>> Non-Functional Requirements (optional)")
    _show_current_list(current.get("non_functional", []))
    items = _prompt_list("List each requirement")
    return {"non_functional": items}


def _collect_constraints(current: dict) -> dict:
    print("\n>> Constraints")
    _show_current_list(current.get("tech_stack", []), label="Current tech stack")
    tech_stack = _prompt_list("Tech stack (languages, frameworks, platforms)")
    performance = (
        _prompt(
            "Performance constraints", required=False, default=current.get("performance", "") or ""
        )
        or None
    )
    domain = current.get("_domain", "software")
    security = (
        _prompt(
            "Security constraints",
            required=(domain == "infra"),
            default=current.get("security", "") or "",
        )
        or None
    )
    return {"tech_stack": tech_stack, "performance": performance, "security": security}


def _collect_non_goals(current: dict) -> dict:
    print("\n>> Non-Goals (what this project will NOT do)")
    _show_current_list(current.get("non_goals", []))
    items = _prompt_list("List each non-goal")
    return {"non_goals": items}


def _collect_acceptance(current: dict) -> dict:
    print("\n>> Acceptance Criteria")
    _show_current_list(current.get("acceptance", []))
    items = _prompt_list("List each testable criterion", min_items=1)
    return {"acceptance": items}


def _collect_outputs(current: dict) -> dict:
    print("\n>> Expected Outputs")
    _show_current_list(current.get("artifacts", []))
    artifacts = _prompt_list("Expected artifacts", min_items=1)
    return {"artifacts": artifacts}


def _show_current_list(items: list[str], label: str = "Current items") -> None:
    if items:
        print(f"  {label} ({len(items)}):")
        for item in items:
            print(f"    * {item}")
        print("  Enter new items to REPLACE these, or empty line to keep them.")


COLLECTORS = {
    "project": _collect_project,
    "functional": _collect_functional,
    "non_functional": _collect_non_functional,
    "constraints": _collect_constraints,
    "non_goals": _collect_non_goals,
    "acceptance": _collect_acceptance,
    "outputs": _collect_outputs,
}


# ── Build spec from flat data dict ───────────────────────────────────────────


def _build_spec(data: dict) -> ProjectSpec:
    return ProjectSpec(
        project=ProjectInfo(
            name=data["name"],
            description=data["description"],
            domain=Domain(data["domain"]),
        ),
        requirements=Requirements(
            functional=data["functional"],
            non_functional=data.get("non_functional", []),
        ),
        constraints=Constraints(
            tech_stack=data.get("tech_stack", []),
            performance=data.get("performance"),
            security=data.get("security"),
        ),
        non_goals=data.get("non_goals", []),
        acceptance_criteria=data["acceptance"],
        outputs=Outputs(
            expected_artifacts=data["artifacts"],
        ),
    )


def _data_from_spec(spec: ProjectSpec) -> dict:
    """Extract flat data dict from an existing ProjectSpec (for edit mode)."""
    return {
        "name": spec.project.name,
        "description": spec.project.description,
        "domain": spec.project.domain.value,
        "functional": list(spec.requirements.functional),
        "non_functional": list(spec.requirements.non_functional),
        "tech_stack": list(spec.constraints.tech_stack),
        "performance": spec.constraints.performance,
        "security": spec.constraints.security,
        "non_goals": list(spec.non_goals),
        "acceptance": list(spec.acceptance_criteria),
        "artifacts": list(spec.outputs.expected_artifacts),
    }


# ── Summary + edit loop ──────────────────────────────────────────────────────


def _print_summary(data: dict) -> None:
    print("\n=== Project Spec Summary ===\n")
    print(f"  [1] Project:       {data.get('name', '')} ({data.get('domain', '')})")
    desc = data.get("description", "")
    if len(desc) > 80:
        desc = desc[:77] + "..."
    print(f"      Description:   {desc}")
    print(f"  [2] Functional:    {len(data.get('functional', []))} items")
    for item in data.get("functional", []):
        print(f"        - {item}")
    print(f"  [3] Non-Functional:{len(data.get('non_functional', []))} items")
    for item in data.get("non_functional", []):
        print(f"        - {item}")
    print(
        f"  [4] Constraints:   {len(data.get('tech_stack', []))} tech stack"
        f" | perf: {'yes' if data.get('performance') else 'none'}"
        f" | security: {'yes' if data.get('security') else 'none'}"
    )
    print(f"  [5] Non-Goals:     {len(data.get('non_goals', []))} items")
    for item in data.get("non_goals", []):
        print(f"        - {item}")
    print(f"  [6] Acceptance:    {len(data.get('acceptance', []))} criteria")
    for item in data.get("acceptance", []):
        print(f"        - {item}")
    print(f"  [7] Outputs:       {', '.join(data.get('artifacts', []))}")
    print()


def _review_loop(data: dict) -> dict | None:
    """Show summary, let user edit sections by number, confirm, or abort."""
    section_keys = [key for key, _ in SECTIONS]
    max_section = len(SECTIONS)

    while True:
        _print_summary(data)
        print(f"  Enter a number (1-{max_section}) to edit that section")
        print("  'c' to confirm and write  |  'q' to abort")
        choice = input("\n  > ").strip().lower()

        if choice == "q":
            return None
        if choice == "c":
            return data
        if choice in [str(i) for i in range(1, max_section + 1)]:
            idx = int(choice) - 1
            section_key = section_keys[idx]
            collector = COLLECTORS[section_key]
            # Pass domain to constraints collector
            data["_domain"] = data.get("domain", "software")
            updates = collector(data)
            data.update(updates)
            data.pop("_domain", None)
        else:
            print(f"    ^ Enter 1-{max_section}, 'c', or 'q'")


# ── Project scaffolding ──────────────────────────────────────────────────────


def _scaffold_project_dir(target: Path) -> None:
    """Create the scaffolded project directory structure at *target*.

    Copies config.yml and templates/ from the engine root so the user can
    customize them per-project.
    """
    target.mkdir(parents=True, exist_ok=True)

    # Copy config.yml
    src_config = ENGINE_ROOT / "config.yml"
    dst_config = target / "config.yml"
    if not dst_config.exists() and src_config.exists():
        shutil.copy2(src_config, dst_config)

    # Copy templates/ tree
    src_templates = ENGINE_ROOT / "templates"
    dst_templates = target / "templates"
    if not dst_templates.exists() and src_templates.is_dir():
        shutil.copytree(src_templates, dst_templates)

    # Create state/ subdirectories (runs/ holds per-run trace, evidence, decisions)
    state_dir = target / "state"
    for subdir in ("inputs", "designs", "implementations", "tests", "build", "runs"):
        (state_dir / subdir).mkdir(parents=True, exist_ok=True)

    print(f"Scaffolded project directory: {target}")


# ── LLM-powered spec generation ──────────────────────────────────────────────


def _generate_spec_suggestions(data: dict) -> dict | None:
    """Call the LLM to auto-generate remaining spec fields from the project seed.

    Returns a flat dict of generated fields on success, or None on any failure.
    """
    try:
        from engine.llm_provider import get_provider

        template_path = get_prompts_dir() / "intake_suggest.txt"
        prompt_template = template_path.read_text()
        prompt = prompt_template.format(
            name=data.get("name", ""),
            description=data.get("description", ""),
            domain=data.get("domain", "software"),
        )

        provider = get_provider()
        response = provider.generate(prompt)

        # Extract YAML block from between ```yaml and ``` fences
        match = re.search(r"```yaml\s*\n(.*?)```", response, re.DOTALL)
        if not match:
            print("  Warning: LLM response did not contain a valid YAML block.")
            return None

        parsed = yaml.safe_load(match.group(1))
        if not isinstance(parsed, dict):
            print("  Warning: parsed YAML is not a mapping.")
            return None

        # Validate required keys
        for key in ("functional", "acceptance", "artifacts"):
            if key not in parsed or not parsed[key]:
                print(f"  Warning: generated spec missing required key '{key}'.")
                return None

        return parsed

    except Exception as exc:
        print(f"  Warning: spec generation failed ({exc}). Continuing manually.")
        return None


# ── Main collection flow ─────────────────────────────────────────────────────


def collect_interactive() -> ProjectSpec | None:
    """Walk the user through every required field, then review/edit loop."""
    print("\n=== Autonomy Engine — Project Intake ===\n")
    print("  Fill in each section. You can review and edit anything before confirming.\n")

    data: dict = {}

    # Always collect the project seed manually
    data.update(_collect_project(data))

    # Offer LLM-powered generation for remaining sections
    if _prompt_yn(
        "Generate suggestions for remaining sections from your description", default=True
    ):
        print("\n  Generating suggestions...")
        suggestions = _generate_spec_suggestions(data)
        if suggestions:
            data.update(suggestions)
            print("  Done. Review the generated spec below.\n")
            result = _review_loop(data)
            return _build_spec(result) if result else None
        else:
            print("  Generation failed. Continuing with manual entry.\n")

    # Manual fallthrough: collect all remaining sections
    data.update(_collect_functional(data))
    data.update(_collect_non_functional(data))
    data["_domain"] = data.get("domain", "software")
    data.update(_collect_constraints(data))
    data.pop("_domain", None)
    data.update(_collect_non_goals(data))
    data.update(_collect_acceptance(data))
    data.update(_collect_outputs(data))

    # Review and edit loop
    result = _review_loop(data)
    if result is None:
        return None

    return _build_spec(result)


def load_from_file(path: str) -> ProjectSpec:
    """Load and validate a project spec from a YAML file."""
    with open(path) as f:
        data = yaml.safe_load(f)
    return ProjectSpec(**data)


def _write_and_report(spec: ProjectSpec) -> None:
    written = render_all(spec)
    state_dir = get_state_dir()
    print(f"\nIntake complete. Wrote {len(written)} files to {state_dir / 'inputs'}:")
    for path in written:
        print(f"  + {path}")
    print("\nEngine is ready to run.")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="intake",
        description="Autonomy Engine — Project Intake & Normalization",
    )
    parser.add_argument(
        "--project-dir",
        default=None,
        help="Path to an external project directory (default: engine root)",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("new-project", help="Create a new project spec interactively")

    sub.add_parser("edit", help="Edit the current project spec in state/inputs/")

    from_file = sub.add_parser(
        "from-file",
        help="Load and validate a project spec from a YAML file (non-interactive)",
    )
    from_file.add_argument("path", help="Path to project_spec.yml")
    from_file.add_argument(
        "--review",
        action="store_true",
        default=False,
        help="Open interactive review/edit loop before writing",
    )

    validate = sub.add_parser("validate", help="Validate an existing project_spec.yml")
    validate.add_argument("path", help="Path to project_spec.yml")

    args = parser.parse_args()

    # Initialize context (must happen before any path access)
    init_context(args.project_dir)

    # Scaffold project directory for any command that writes state
    if args.project_dir is not None and args.command in ("new-project", "from-file", "edit"):
        _scaffold_project_dir(Path(args.project_dir).resolve())

    if args.command == "new-project":
        try:
            spec = collect_interactive()
        except (KeyboardInterrupt, EOFError):
            print("\n\nIntake cancelled.")
            sys.exit(1)

        if spec is None:
            print("Aborted.")
            sys.exit(1)

        _write_and_report(spec)

    elif args.command == "edit":
        spec_path = get_state_dir() / "inputs" / "project_spec.yml"
        if not spec_path.exists():
            print("No existing spec found. Run 'new-project' first.")
            sys.exit(1)

        try:
            spec = load_from_file(str(spec_path))
            data = _data_from_spec(spec)
        except ValidationError as e:
            print(f"Existing spec is invalid:\n{e}")
            sys.exit(1)

        print("\n=== Autonomy Engine — Edit Project Spec ===\n")
        try:
            result = _review_loop(data)
        except (KeyboardInterrupt, EOFError):
            print("\n\nEdit cancelled.")
            sys.exit(1)

        if result is None:
            print("No changes written.")
            sys.exit(0)

        spec = _build_spec(result)
        _write_and_report(spec)

    elif args.command == "from-file":
        try:
            spec = load_from_file(args.path)
        except ValidationError as e:
            print(f"Validation failed:\n{e}")
            sys.exit(1)

        if args.review:
            # Interactive review/edit loop (opt-in)
            print(f"Spec loaded from {args.path}")
            data = _data_from_spec(spec)
            try:
                result = _review_loop(data)
            except (KeyboardInterrupt, EOFError):
                print("\n\nCancelled.")
                sys.exit(1)

            if result is None:
                print("Aborted.")
                sys.exit(1)

            spec = _build_spec(result)

        _write_and_report(spec)

    elif args.command == "validate":
        try:
            spec = load_from_file(args.path)
            print(f"Valid. Project: {spec.project.name}")
        except ValidationError as e:
            print(f"Invalid:\n{e}")
            sys.exit(1)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
