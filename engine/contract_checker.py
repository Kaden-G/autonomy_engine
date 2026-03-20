"""Contract compliance checker — verifies that extracted output matches the design contract.

Run after extraction to catch:
    1. Missing files (contract says X files, only Y were produced).
    2. Extra files (files not in any component's plan).
    3. File budget violations (component exceeds its max_files).
    4. Missing canonical types (types defined in contract but not in output).
    5. Type field mismatches (output has different fields than contract specifies).

Returns a structured report that the test stage can save as evidence.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ComplianceIssue:
    """A single contract compliance violation."""
    severity: str       # "error" | "warning"
    category: str       # "missing_file" | "extra_file" | "budget" | "type_mismatch" | "missing_type"
    component: str      # which component is affected (or "project" for global issues)
    message: str        # human-readable description

    def to_dict(self) -> dict:
        return {
            "severity": self.severity,
            "category": self.category,
            "component": self.component,
            "message": self.message,
        }


@dataclass
class ComplianceReport:
    """Full compliance check result."""
    passed: bool
    issues: list[ComplianceIssue] = field(default_factory=list)
    files_expected: int = 0
    files_found: int = 0
    files_missing: int = 0
    files_extra: int = 0

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "files_expected": self.files_expected,
            "files_found": self.files_found,
            "files_missing": self.files_missing,
            "files_extra": self.files_extra,
            "error_count": sum(1 for i in self.issues if i.severity == "error"),
            "warning_count": sum(1 for i in self.issues if i.severity == "warning"),
            "issues": [i.to_dict() for i in self.issues],
        }

    def summary(self) -> str:
        """One-line summary for logging."""
        errors = sum(1 for i in self.issues if i.severity == "error")
        warnings = sum(1 for i in self.issues if i.severity == "warning")
        status = "PASS" if self.passed else "FAIL"
        return (
            f"Contract compliance: {status} — "
            f"{self.files_found}/{self.files_expected} files, "
            f"{errors} error(s), {warnings} warning(s)"
        )

    def to_evidence_record(self) -> dict:
        """Format as an evidence record compatible with the test stage."""
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        details = []
        for issue in self.issues:
            icon = "ERROR" if issue.severity == "error" else "WARN"
            details.append(f"[{icon}] [{issue.component}] {issue.message}")

        stdout = self.summary() + "\n\n" + "\n".join(details) if details else self.summary()

        return {
            "name": "contract-compliance",
            "command": "(built-in contract checker)",
            "argv": [],
            "cwd": "",
            "started_at": now,
            "finished_at": now,
            "exit_code": 0 if self.passed else 1,
            "stdout": stdout,
            "stderr": "",
            "stdout_hash": "",
            "stderr_hash": "",
        }


def check_contract_compliance(
    contract_path: Path,
    project_dir: Path,
) -> ComplianceReport:
    """Check the extracted project against the design contract.

    Args:
        contract_path: Path to DESIGN_CONTRACT.json
        project_dir: Path to the extracted project directory

    Returns:
        ComplianceReport with all issues found.
    """
    issues: list[ComplianceIssue] = []

    # Load contract
    try:
        contract_data = json.loads(contract_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        return ComplianceReport(
            passed=False,
            issues=[ComplianceIssue(
                "error", "contract_load", "project",
                f"Could not load design contract: {exc}",
            )],
        )

    components = contract_data.get("components", [])
    canonical_types = contract_data.get("canonical_types", [])
    total_budget = contract_data.get("total_file_budget", 999)

    # ── Collect all expected files from contract ──
    expected_files: set[str] = set()
    for comp in components:
        for f in comp.get("files", []):
            expected_files.add(f)

    # ── Collect all actual files in project ──
    actual_files: set[str] = set()
    for path in project_dir.rglob("*"):
        if path.is_file():
            rel = str(path.relative_to(project_dir))
            # Skip node_modules, __pycache__, .git, .venv, etc.
            if any(skip in rel for skip in (
                "node_modules/", "__pycache__/", ".git/", ".env", ".venv/",
            )):
                continue
            actual_files.add(rel)

    # ── Missing files ──
    missing = expected_files - actual_files
    for f in sorted(missing):
        # Find which component owns this file
        owner = "unknown"
        for comp in components:
            if f in comp.get("files", []):
                owner = comp["name"]
                break
        issues.append(ComplianceIssue(
            "error", "missing_file", owner,
            f"Contract requires '{f}' but it was not produced.",
        ))

    # ── Extra files (not in contract) ──
    # Config files and package metadata are expected extras
    expected_extras = {
        "package.json", "package-lock.json", "tsconfig.json", "tsconfig.node.json",
        "vite.config.ts", "vite.config.js", ".eslintrc.js", ".eslintrc.json",
        "postcss.config.js", "tailwind.config.js", "tailwind.config.ts",
        "requirements.txt", "pyproject.toml", "setup.py", "setup.cfg",
        "README.md", ".gitignore", "index.html", "Makefile",
        "DESIGN_CONTRACT.json",
    }
    # __init__.py files are structural necessities in Python packages —
    # the LLM often omits them from the contract but they MUST exist.
    # Also allow common entry-point files (run.py, app.py, manage.py, main.py at root).
    _PYTHON_STRUCTURAL = {"run.py", "app.py", "manage.py", "wsgi.py", "asgi.py"}
    for f in list(actual_files):
        basename = Path(f).name
        if basename == "__init__.py":
            expected_extras.add(f)
        elif basename in _PYTHON_STRUCTURAL and "/" not in f:
            expected_extras.add(f)
    extra = actual_files - expected_files - expected_extras
    for f in sorted(extra):
        issues.append(ComplianceIssue(
            "warning", "extra_file", "project",
            f"File '{f}' was produced but is not in any component's contract.",
        ))

    # ── Per-component budget check ──
    for comp in components:
        comp_files = comp.get("files", [])
        max_files = comp.get("max_files", 10)
        # Count files that actually exist for this component
        existing = [f for f in comp_files if f in actual_files]
        if len(existing) > max_files:
            issues.append(ComplianceIssue(
                "error", "budget", comp["name"],
                f"Component has {len(existing)} files but max_files is {max_files}.",
            ))

    # ── Total budget check ──
    # Only count substantive files against budget (exclude structural extras)
    budgeted_files = actual_files - expected_extras
    if len(budgeted_files) > total_budget:
        issues.append(ComplianceIssue(
            "error", "budget", "project",
            f"Total files ({len(budgeted_files)}) exceeds budget ({total_budget})."
            f" ({len(actual_files)} total including {len(actual_files - budgeted_files)} structural extras)",
        ))

    # ── Canonical type checks ──
    for td in canonical_types:
        type_name = td.get("name", "")
        type_file = td.get("file_path", "")
        type_fields = td.get("fields", {})

        file_path = project_dir / type_file
        if not file_path.exists():
            issues.append(ComplianceIssue(
                "error", "missing_type", "project",
                f"Canonical type '{type_name}' should be in '{type_file}' "
                f"but that file does not exist.",
            ))
            continue

        # Check that the type name appears in the file
        content = file_path.read_text()
        if type_name not in content:
            issues.append(ComplianceIssue(
                "error", "missing_type", "project",
                f"Canonical type '{type_name}' not found in '{type_file}'.",
            ))
            continue

        # Check that expected fields appear in the file
        for field_name in type_fields:
            if field_name not in content:
                issues.append(ComplianceIssue(
                    "warning", "type_mismatch", "project",
                    f"Field '{field_name}' from canonical type '{type_name}' "
                    f"not found in '{type_file}'.",
                ))

    # ── Build report ──
    has_errors = any(i.severity == "error" for i in issues)
    report = ComplianceReport(
        passed=not has_errors,
        issues=issues,
        files_expected=len(expected_files),
        files_found=len(actual_files & expected_files),
        files_missing=len(missing),
        files_extra=len(extra),
    )

    logger.info(report.summary())
    return report
