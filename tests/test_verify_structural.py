"""Tests for the structural issue classification in the verify task."""

from tasks.verify import _classify_issues, _build_deterministic_verification


# ── Evidence record helpers ──────────────────────────────────────────────────


def _make_record(name: str, exit_code: int, stdout: str = "", stderr: str = "") -> dict:
    return {
        "name": name,
        "command": f"check-{name}",
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "started_at": "2026-01-01T00:00:00Z",
        "finished_at": "2026-01-01T00:00:01Z",
    }


# ── _classify_issues tests ───────────────────────────────────────────────────


class TestClassifyIssues:
    def test_all_passing_returns_empty(self):
        evidence = [
            _make_record("install", 0),
            _make_record("build", 0),
            _make_record("test", 0),
        ]
        assert _classify_issues(evidence) == {}

    def test_typecheck_failure_classified(self):
        evidence = [
            _make_record(
                "typecheck", 1, stdout="src/foo.ts(10,5): error TS2339: Property 'x' does not exist"
            ),
        ]
        issues = _classify_issues(evidence)
        assert "type_errors" in issues
        assert any("TS2339" in item for item in issues["type_errors"])

    def test_import_check_failure_classified(self):
        evidence = [
            _make_record("import-check", 1, stdout="src/bar.py:5: cannot resolve mymodule.utils"),
        ]
        issues = _classify_issues(evidence)
        assert "import_errors" in issues
        assert any("cannot resolve" in item for item in issues["import_errors"])

    def test_lint_failure_classified(self):
        evidence = [
            _make_record("lint", 1, stdout="src/foo.py:10:1: F401 'os' imported but unused"),
        ]
        issues = _classify_issues(evidence)
        assert "lint_errors" in issues

    def test_test_failure_classified(self):
        evidence = [
            _make_record("test", 1, stdout="FAILED test_something - 2 failed, 3 passed"),
        ]
        issues = _classify_issues(evidence)
        assert "test_failures" in issues
        assert any("failed" in item.lower() for item in issues["test_failures"])

    def test_build_failure_classified(self):
        evidence = [
            _make_record("build", 1, stderr="Error: Module not found: Can't resolve './missing'"),
        ]
        issues = _classify_issues(evidence)
        assert "build_errors" in issues

    def test_contract_compliance_failure(self):
        evidence = [
            _make_record(
                "contract-compliance",
                1,
                stdout="Contract compliance: FAIL\n\n[ERROR] [Core] Missing src/types.ts\n[WARN] [project] Extra file",
            ),
        ]
        issues = _classify_issues(evidence)
        assert "contract_issues" in issues
        assert len(issues["contract_issues"]) == 2  # one ERROR, one WARN

    def test_unknown_check_goes_to_other(self):
        evidence = [
            _make_record("custom-check", 1),
        ]
        issues = _classify_issues(evidence)
        assert "other_failures" in issues

    def test_no_checks_configured_ignored(self):
        evidence = [
            _make_record("no_checks_configured", -1),
        ]
        issues = _classify_issues(evidence)
        assert issues == {}

    def test_multiple_categories(self):
        evidence = [
            _make_record("typecheck", 1, stdout="error TS2339: foo"),
            _make_record("import-check", 1, stdout="cannot resolve bar"),
            _make_record("build", 0),  # passed — should not appear
        ]
        issues = _classify_issues(evidence)
        assert "type_errors" in issues
        assert "import_errors" in issues
        assert "build_errors" not in issues

    def test_typecheck_truncates_at_10(self):
        """Long type error output should be capped at 10 + summary."""
        lines = [f"src/f.ts({i},1): error TS2339: Property 'p{i}'" for i in range(20)]
        evidence = [_make_record("typecheck", 1, stdout="\n".join(lines))]
        issues = _classify_issues(evidence)
        # 10 errors + 1 "... and N more" line
        assert len(issues["type_errors"]) == 11


# ── _build_deterministic_verification tests ──────────────────────────────────


class TestBuildDeterministicVerification:
    def test_all_passed_output(self):
        evidence = [
            _make_record("install", 0),
            _make_record("build", 0),
        ]
        md = _build_deterministic_verification(evidence, passed=True)
        assert "PASSED" in md
        assert "All configured checks passed" in md
        assert "Structural Issue Breakdown" not in md

    def test_failure_output_includes_breakdown(self):
        evidence = [
            _make_record("install", 0),
            _make_record("typecheck", 1, stdout="error TS2339: blah"),
            _make_record("build", 1, stderr="Build failed"),
        ]
        md = _build_deterministic_verification(evidence, passed=False)
        assert "FAILED" in md
        assert "Structural Issue Breakdown" in md
        assert "Type Errors" in md
        assert "Build Errors" in md
        assert "Recommended Actions" in md

    def test_no_checks_sentinel_excluded(self):
        evidence = [_make_record("no_checks_configured", -1)]
        md = _build_deterministic_verification(evidence, passed=True)
        assert "no_checks_configured" not in md

    def test_contract_issues_trigger_guidance(self):
        evidence = [
            _make_record("contract-compliance", 1, stdout="[ERROR] [Core] Missing file"),
        ]
        md = _build_deterministic_verification(evidence, passed=False)
        assert "Contract Compliance" in md
        assert "design contract" in md.lower()
