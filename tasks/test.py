"""Test task — run automated quality checks and capture the results as evidence.

No AI is involved in testing.  This stage runs real tools (syntax checkers, linters,
type checkers, test suites) against the generated code in an isolated workspace.
Every result is captured as a structured evidence record (pass/fail, full output,
timestamps) that feeds into the verification stage and the audit trail.

The checks are either explicitly configured in ``config.yml`` or auto-detected
based on the project type (Python, Node.js, etc.).  Contract compliance is always
checked — verifying that the generated code matches the design blueprint.
"""

import re
from pathlib import Path

import yaml
from prefect import task

from engine.context import get_project_dir, get_state_dir
from engine.contract_checker import check_contract_compliance
from engine.decision_gates import DecisionRequired, decision_exists, load_decision
from engine.evidence import (
    auto_detect_checks,
    load_all_evidence,
    load_configured_checks,
    no_checks_record,
    run_check,
    save_evidence,
)
from engine.sandbox import collect_host_metadata, create_sandbox, load_sandbox_config
from engine.state_loader import load_state_file, save_state_file
from engine.tracer import get_run_id, trace


def _slugify(name: str) -> str:
    """Lowercase, replace spaces/underscores with hyphens, strip non-alphanumeric."""
    slug = name.lower().strip()
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"[^a-z0-9\-]", "", slug)
    return slug


def _get_project_dir() -> Path:
    """Determine the extracted project directory from the project spec."""
    spec_raw = load_state_file("inputs/project_spec.yml")
    spec = yaml.safe_load(spec_raw)
    project_name = spec["project"]["name"]
    return get_project_dir().parent / _slugify(project_name)


def _build_test_summary(evidence: list[dict]) -> str:
    """Build a factual markdown summary from evidence records (no LLM)."""
    lines = ["# Test Results", ""]

    if not evidence or (len(evidence) == 1 and evidence[0]["name"] == "no_checks_configured"):
        lines.append("**No automated checks were configured for this project.**")
        lines.append("")
        lines.append("Add a `checks` section to `config.yml` to enable real test execution.")
        lines.append("")
        return "\n".join(lines)

    # Environment section (from first record that has it)
    for r in evidence:
        env = r.get("environment")
        if env:
            lines.append("## Environment")
            lines.append("")
            lines.append(f"- **Sandboxed:** {'yes' if env.get('sandboxed') else 'no'}")
            if env.get("python_version"):
                lines.append(f"- **Python:** {env['python_version']}")
            if env.get("platform"):
                lines.append(f"- **Platform:** {env['platform']}")
            if env.get("deps_installed") is not None:
                lines.append(
                    f"- **Dependencies installed:** {'yes' if env['deps_installed'] else 'no'}"
                )
            lines.append("")
            break

    total = len(evidence)
    passed = sum(1 for r in evidence if r["exit_code"] == 0)
    failed = total - passed

    lines.append("## Summary")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Checks run | {total} |")
    lines.append(f"| Passed | {passed} |")
    lines.append(f"| Failed | {failed} |")
    lines.append("")
    lines.append("## Details")
    lines.append("")

    for r in evidence:
        status = "PASSED" if r["exit_code"] == 0 else "FAILED"
        lines.append(f"### {r['name']} — {status} (exit {r['exit_code']})")
        lines.append("")
        lines.append(f"- **Command:** `{r['command']}`")
        lines.append(f"- **Started:** {r['started_at']}")
        lines.append(f"- **Finished:** {r['finished_at']}")
        lines.append("")

        if r["stdout"].strip():
            stdout = r["stdout"]
            if len(stdout) > 3000:
                stdout = stdout[:1500] + "\n... (truncated) ...\n" + stdout[-1500:]
            lines.append("<details>")
            lines.append("<summary>stdout</summary>")
            lines.append("")
            lines.append("```")
            lines.append(stdout.rstrip())
            lines.append("```")
            lines.append("")
            lines.append("</details>")
            lines.append("")

        if r["stderr"].strip():
            stderr = r["stderr"]
            if len(stderr) > 2000:
                stderr = stderr[:1000] + "\n... (truncated) ...\n" + stderr[-1000:]
            lines.append("<details>")
            lines.append("<summary>stderr</summary>")
            lines.append("")
            lines.append("```")
            lines.append(stderr.rstrip())
            lines.append("```")
            lines.append("")
            lines.append("</details>")
            lines.append("")

    return "\n".join(lines)


def _run_checks_sandboxed(checks: list[dict], project_dir: Path, sandbox_cfg: dict) -> dict:
    """Run all checks inside an isolated sandbox. Returns sandbox metadata."""
    install = sandbox_cfg.get("install_deps", True)

    with create_sandbox(project_dir, install_deps=install, sandbox_cfg=sandbox_cfg) as sb:
        env_meta = sb.metadata()
        for check in checks:
            record = run_check(
                name=check["name"],
                command=check["command"],
                cwd=sb.workspace,
                env=sb.env,
            )
            record["environment"] = env_meta
            save_evidence(record)
        return env_meta


def _run_checks_direct(checks: list[dict], project_dir: Path) -> None:
    """Run all checks directly against the project directory (no sandbox)."""
    env_meta = collect_host_metadata()
    for check in checks:
        cwd = Path(check["cwd"]) if "cwd" in check else project_dir
        record = run_check(
            name=check["name"],
            command=check["command"],
            cwd=cwd,
        )
        record["environment"] = env_meta
        save_evidence(record)


def _has_failures(evidence: list[dict]) -> bool:
    """Return True if any evidence record has a non-zero exit code.

    Ignores the ``no_checks_configured`` sentinel record.
    """
    return any(r["exit_code"] != 0 for r in evidence if r.get("name") != "no_checks_configured")


@task(name="test")
def test_system() -> None:
    """Run configured checks against the extracted project, capture evidence."""
    # Decision guard: if a previous triage decision was "abort", stop immediately
    if decision_exists("test_failure_triage"):
        record = load_decision("test_failure_triage")
        if record["selected"] == "abort":
            raise RuntimeError("Test failure triage decision was 'abort'")

    checks = load_configured_checks()
    run_id = get_run_id()

    # Auto-detect checks from the extracted project when none are configured
    auto_detected = False
    if not checks:
        project_dir = _get_project_dir()
        checks = auto_detect_checks(project_dir)
        if checks:
            auto_detected = True

    sandbox_meta: dict = {}
    if not checks:
        save_evidence(no_checks_record())
    else:
        if not auto_detected:
            project_dir = _get_project_dir()
        sandbox_cfg = load_sandbox_config()
        use_sandbox = sandbox_cfg.get("enabled", True)

        if use_sandbox:
            sandbox_meta = _run_checks_sandboxed(checks, project_dir, sandbox_cfg)
        else:
            _run_checks_direct(checks, project_dir)

    # ── Contract compliance check ────────────────────────────────────────
    # If a design contract exists, verify the output matches it.
    contract_path = get_state_dir() / "designs" / "DESIGN_CONTRACT.json"
    if contract_path.exists():
        compliance = check_contract_compliance(contract_path, project_dir)
        save_evidence(compliance.to_evidence_record())

    evidence = load_all_evidence()
    summary = _build_test_summary(evidence)

    output_path = "tests/TEST_RESULTS.md"
    save_state_file(output_path, summary)

    evidence_rel = [f"runs/{run_id}/evidence/{r['name']}.json" for r in evidence]
    extra = {}
    if sandbox_meta:
        extra.update({
            "sandbox_venv_cache_hit": sandbox_meta.get("venv_cache_hit"),
            "sandbox_venv_create_time_s": sandbox_meta.get("venv_create_time_s"),
            "sandbox_deps_install_time_s": sandbox_meta.get("deps_install_time_s"),
        })
    if auto_detected:
        extra["checks_auto_detected"] = True
        extra["auto_detected_checks"] = [c["name"] for c in checks]

    trace(
        task="test",
        inputs=["implementations/IMPLEMENTATION.md"],
        outputs=[output_path] + evidence_rel,
        extra=extra or None,
    )

    # Gate trigger: raise if failures detected and no decision recorded yet
    if _has_failures(evidence) and not decision_exists("test_failure_triage"):
        raise DecisionRequired("test_failure_triage", "test", ["continue", "abort"])
