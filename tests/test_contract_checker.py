"""Tests for engine.contract_checker — post-extraction contract compliance validation."""

import json

import pytest

from engine.contract_checker import (
    ComplianceIssue,
    ComplianceReport,
    check_contract_compliance,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _write_contract(tmp_path, contract_data: dict):
    """Write a DESIGN_CONTRACT.json and return the path."""
    path = tmp_path / "DESIGN_CONTRACT.json"
    path.write_text(json.dumps(contract_data))
    return path


def _minimal_contract(**overrides) -> dict:
    """Build a minimal valid contract dict."""
    defaults = {
        "project_name": "test-app",
        "language": "typescript",
        "entry_point": "src/main.tsx",
        "total_file_budget": 40,
        "components": [
            {
                "name": "Core",
                "description": "Core types",
                "files": ["src/types/index.ts", "src/config.ts"],
                "imports_from": [],
                "exports_types": ["User"],
                "max_files": 5,
            }
        ],
        "canonical_types": [
            {
                "name": "User",
                "kind": "interface",
                "fields": {"id": "string", "email": "string"},
                "file_path": "src/types/index.ts",
            }
        ],
        "tech_decisions": [],
    }
    defaults.update(overrides)
    return defaults


def _create_project_files(project_dir, files: dict[str, str]):
    """Create project files from a dict of {relative_path: content}."""
    for rel_path, content in files.items():
        path = project_dir / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


# ── Report structure ─────────────────────────────────────────────────────────


class TestComplianceReport:
    def test_report_to_dict(self):
        report = ComplianceReport(
            passed=True,
            files_expected=5,
            files_found=5,
            files_missing=0,
            files_extra=0,
        )
        d = report.to_dict()
        assert d["passed"] is True
        assert d["error_count"] == 0
        assert d["warning_count"] == 0

    def test_report_with_issues(self):
        report = ComplianceReport(
            passed=False,
            issues=[
                ComplianceIssue("error", "missing_file", "Core", "Missing src/foo.ts"),
                ComplianceIssue("warning", "extra_file", "project", "Extra bar.ts"),
            ],
        )
        d = report.to_dict()
        assert d["error_count"] == 1
        assert d["warning_count"] == 1

    def test_summary_format(self):
        report = ComplianceReport(
            passed=False,
            issues=[ComplianceIssue("error", "missing_file", "Core", "Missing")],
            files_expected=5,
            files_found=4,
        )
        summary = report.summary()
        assert "FAIL" in summary
        assert "4/5" in summary
        assert "1 error" in summary

    def test_to_evidence_record(self):
        report = ComplianceReport(passed=True, files_expected=3, files_found=3)
        record = report.to_evidence_record()
        assert record["name"] == "contract-compliance"
        assert record["exit_code"] == 0
        assert "PASS" in record["stdout"]

    def test_failed_evidence_record_has_nonzero_exit(self):
        report = ComplianceReport(
            passed=False,
            issues=[ComplianceIssue("error", "budget", "project", "Over budget")],
        )
        record = report.to_evidence_record()
        assert record["exit_code"] == 1


# ── Full compliance checking ─────────────────────────────────────────────────


class TestCheckContractCompliance:
    def test_perfect_match_passes(self, tmp_path):
        contract = _minimal_contract()
        contract_path = _write_contract(tmp_path, contract)
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        _create_project_files(project_dir, {
            "src/types/index.ts": "export interface User { id: string; email: string; }",
            "src/config.ts": "export const config = {};",
        })
        report = check_contract_compliance(contract_path, project_dir)
        assert report.passed is True
        assert report.files_missing == 0

    def test_missing_file_is_error(self, tmp_path):
        contract = _minimal_contract()
        contract_path = _write_contract(tmp_path, contract)
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        # Only create one of two expected files
        _create_project_files(project_dir, {
            "src/types/index.ts": "export interface User { id: string; email: string; }",
        })
        report = check_contract_compliance(contract_path, project_dir)
        assert report.passed is False
        assert report.files_missing == 1
        assert any(i.category == "missing_file" for i in report.issues)

    def test_extra_file_is_warning(self, tmp_path):
        contract = _minimal_contract()
        contract_path = _write_contract(tmp_path, contract)
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        _create_project_files(project_dir, {
            "src/types/index.ts": "export interface User { id: string; email: string; }",
            "src/config.ts": "export const config = {};",
            "src/extra.ts": "// not in contract",
        })
        report = check_contract_compliance(contract_path, project_dir)
        # Extra files are warnings, not errors — should still pass
        assert report.passed is True
        assert report.files_extra == 1
        assert any(i.category == "extra_file" for i in report.issues)

    def test_expected_extras_not_flagged(self, tmp_path):
        """Config files like package.json should not be flagged as extra."""
        contract = _minimal_contract()
        contract_path = _write_contract(tmp_path, contract)
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        _create_project_files(project_dir, {
            "src/types/index.ts": "export interface User { id: string; email: string; }",
            "src/config.ts": "export const config = {};",
            "package.json": "{}",
            "tsconfig.json": "{}",
        })
        report = check_contract_compliance(contract_path, project_dir)
        assert report.passed is True
        assert report.files_extra == 0

    def test_total_budget_exceeded(self, tmp_path):
        contract = _minimal_contract(total_file_budget=2)
        contract_path = _write_contract(tmp_path, contract)
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        _create_project_files(project_dir, {
            "src/types/index.ts": "export interface User { id: string; email: string; }",
            "src/config.ts": "export const config = {};",
            "src/extra1.ts": "",
            "src/extra2.ts": "",
            "src/extra3.ts": "",
        })
        report = check_contract_compliance(contract_path, project_dir)
        assert report.passed is False
        assert any(i.category == "budget" for i in report.issues)

    def test_missing_canonical_type_in_file(self, tmp_path):
        contract = _minimal_contract()
        contract_path = _write_contract(tmp_path, contract)
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        _create_project_files(project_dir, {
            "src/types/index.ts": "export interface SomeOtherType { x: number; }",
            "src/config.ts": "export const config = {};",
        })
        report = check_contract_compliance(contract_path, project_dir)
        assert report.passed is False
        assert any(i.category == "missing_type" for i in report.issues)

    def test_missing_type_field_is_warning(self, tmp_path):
        contract = _minimal_contract()
        contract_path = _write_contract(tmp_path, contract)
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        # Has User but is missing the "email" field
        _create_project_files(project_dir, {
            "src/types/index.ts": "export interface User { id: string; name: string; }",
            "src/config.ts": "export const config = {};",
        })
        report = check_contract_compliance(contract_path, project_dir)
        assert any(
            i.category == "type_mismatch" and "email" in i.message
            for i in report.issues
        )

    def test_corrupt_contract_file(self, tmp_path):
        path = tmp_path / "DESIGN_CONTRACT.json"
        path.write_text("not valid json {{{")
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        report = check_contract_compliance(path, project_dir)
        assert report.passed is False
        assert any(i.category == "contract_load" for i in report.issues)

    def test_node_modules_excluded_from_actual(self, tmp_path):
        """node_modules/ should be excluded from actual file list."""
        contract = _minimal_contract()
        contract_path = _write_contract(tmp_path, contract)
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        _create_project_files(project_dir, {
            "src/types/index.ts": "export interface User { id: string; email: string; }",
            "src/config.ts": "export const config = {};",
            "node_modules/foo/index.js": "module.exports = {};",
        })
        report = check_contract_compliance(contract_path, project_dir)
        # node_modules file should not appear as extra
        assert report.passed is True
        assert not any("node_modules" in i.message for i in report.issues)
