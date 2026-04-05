"""Verify task — the final go/no-go decision on the generated project.

Reviews all test evidence and acceptance criteria to produce a verification
report (VERIFICATION.md) with a recommendation: accept, reject, or flag issues.

Three modes let you balance thoroughness against cost:
    - **always_llm** — the AI analyzes evidence and writes a detailed verdict (default)
    - **auto** — skip the AI when results are obvious (everything passed or everything failed)
    - **never_llm** — purely rule-based verdict with structured issue breakdown (zero AI cost)

May trigger a decision gate if the verdict is "reject" or "conditional accept."
"""

import yaml
from engine.compat import task

from engine.cache import build_cache_key, cache_lookup, cache_save, hash_content, hash_params
from engine.context import get_config_path, get_prompts_dir
from engine.decision_gates import DecisionRequired, decision_exists, load_decision
from engine.evidence import format_evidence_for_llm, load_all_evidence
from engine.llm_provider import get_provider
from engine.state_loader import load_state_file, save_state_file
from engine.tracer import get_run_id, hash_prompt, trace


def _load_verify_config() -> dict:
    """Load the verify section from config.yml."""
    config_path = get_config_path()
    if not config_path.exists():
        return {}
    with open(config_path) as f:
        config = yaml.safe_load(f) or {}
    return config.get("verify") or {}


def _all_checks_passed(evidence: list[dict]) -> bool:
    """Return True if every evidence record has exit_code == 0."""
    for r in evidence:
        if r.get("name") == "no_checks_configured":
            continue
        if r.get("exit_code", -1) != 0:
            return False
    return True


def _classify_issues(evidence: list[dict]) -> dict:
    """Classify evidence into structural issue categories.

    Returns a dict with keys: type_errors, import_errors, lint_errors,
    test_failures, contract_issues, build_errors — each a list of strings.
    """
    categories: dict[str, list[str]] = {
        "type_errors": [],
        "import_errors": [],
        "lint_errors": [],
        "test_failures": [],
        "contract_issues": [],
        "build_errors": [],
        "other_failures": [],
    }

    for r in evidence:
        name = r.get("name", "")
        exit_code = r.get("exit_code", -1)
        if exit_code == 0 or name == "no_checks_configured":
            continue

        stdout = r.get("stdout", "")
        stderr = r.get("stderr", "")
        output = stdout + "\n" + stderr

        if name == "contract-compliance":
            # Extract individual issues from compliance output
            for line in output.splitlines():
                line = line.strip()
                if line.startswith("[ERROR]") or line.startswith("[WARN]"):
                    categories["contract_issues"].append(line)
            if not categories["contract_issues"]:
                categories["contract_issues"].append(
                    f"Contract compliance check failed (exit {exit_code})"
                )

        elif name in ("typecheck", "type-check"):
            # Count type errors from output
            error_lines = [
                line for line in output.splitlines() if "error TS" in line or ": error:" in line
            ]
            if error_lines:
                categories["type_errors"].extend(error_lines[:10])  # Cap at 10
                if len(error_lines) > 10:
                    categories["type_errors"].append(
                        f"... and {len(error_lines) - 10} more type errors"
                    )
            else:
                categories["type_errors"].append(f"Type check failed (exit {exit_code})")

        elif name in ("import-check", "import_check"):
            error_lines = [line for line in output.splitlines() if "cannot resolve" in line.lower()]
            if error_lines:
                categories["import_errors"].extend(error_lines[:10])
                if len(error_lines) > 10:
                    categories["import_errors"].append(
                        f"... and {len(error_lines) - 10} more import errors"
                    )
            else:
                categories["import_errors"].append(f"Import check failed (exit {exit_code})")

        elif name == "lint":
            error_lines = [
                line
                for line in output.splitlines()
                if ": " in line and ("E" in line or "F" in line)
            ]
            if error_lines:
                categories["lint_errors"].extend(error_lines[:10])
                if len(error_lines) > 10:
                    categories["lint_errors"].append(
                        f"... and {len(error_lines) - 10} more lint errors"
                    )
            else:
                categories["lint_errors"].append(f"Lint check failed (exit {exit_code})")

        elif name == "test":
            categories["test_failures"].append(f"Tests failed (exit {exit_code})")
            # Grab failure summary line if present
            for line in output.splitlines():
                if "failed" in line.lower() and (
                    "passed" in line.lower() or "error" in line.lower()
                ):
                    categories["test_failures"].append(line.strip())
                    break

        elif name == "build":
            categories["build_errors"].append(f"Build failed (exit {exit_code})")
            # Grab the last few lines which usually contain the error
            error_tail = [line.strip() for line in output.strip().splitlines()[-5:] if line.strip()]
            categories["build_errors"].extend(error_tail)

        else:
            categories["other_failures"].append(f"{name}: failed (exit {exit_code})")

    # Remove empty categories
    return {k: v for k, v in categories.items() if v}


def _build_deterministic_verification(evidence: list[dict], passed: bool) -> str:
    """Write a deterministic VERIFICATION.md without calling the LLM.

    This now includes structural issue analysis — classifying failures by
    category so the user (and the pipeline) can see exactly what went wrong,
    not just pass/fail.
    """
    lines = ["# Verification Summary", ""]

    if passed:
        lines.append("## Result: PASSED")
        lines.append("")
        lines.append("All configured checks passed successfully.")
    else:
        lines.append("## Result: FAILED")
        lines.append("")
        lines.append("One or more checks failed. See issue breakdown below.")

    # Per-check summary
    lines.append("")
    lines.append("## Check Results")
    lines.append("")
    for r in evidence:
        if r.get("name") == "no_checks_configured":
            continue
        status = "PASS" if r.get("exit_code", -1) == 0 else "FAIL"
        lines.append(f"- **{r['name']}**: {status} (exit code {r.get('exit_code', 'N/A')})")

    # Structural issue breakdown
    if not passed:
        issues = _classify_issues(evidence)
        if issues:
            lines.append("")
            lines.append("## Structural Issue Breakdown")
            lines.append("")

            category_labels = {
                "contract_issues": "Contract Compliance",
                "type_errors": "Type Errors",
                "import_errors": "Import / Dependency Errors",
                "lint_errors": "Lint Errors",
                "build_errors": "Build Errors",
                "test_failures": "Test Failures",
                "other_failures": "Other Failures",
            }

            for cat, items in issues.items():
                label = category_labels.get(cat, cat)
                lines.append(f"### {label} ({len(items)} issue(s))")
                lines.append("")
                for item in items:
                    lines.append(f"- {item}")
                lines.append("")

            # Actionable guidance
            lines.append("## Recommended Actions")
            lines.append("")
            if "type_errors" in issues:
                lines.append(
                    "- **Type errors** indicate cross-file type inconsistencies. "
                    "Check that the canonical type schema was respected in all chunks."
                )
            if "import_errors" in issues:
                lines.append(
                    "- **Import errors** suggest files are referencing modules that "
                    "don't exist or were named differently. Check the component "
                    "dependency graph in the design contract."
                )
            if "contract_issues" in issues:
                lines.append(
                    "- **Contract compliance issues** mean the output diverged from "
                    "the design contract. Files may be missing, extra, or have "
                    "incorrect type definitions."
                )
            if "build_errors" in issues:
                lines.append(
                    "- **Build errors** usually cascade from type or import errors. "
                    "Fix those first and rebuild."
                )
            if "lint_errors" in issues:
                lines.append(
                    "- **Lint errors** are often cosmetic but can indicate real bugs "
                    "(unused imports, undefined names). Review the specific codes."
                )
            lines.append("")

    lines.append("")
    return "\n".join(lines)


@task(name="verify")
def verify_system() -> None:
    """Load execution evidence, assess against acceptance criteria."""
    # Decision guard
    if decision_exists("verification_review"):
        record = load_decision("verification_review")
        if record["selected"] == "reject":
            raise RuntimeError("Verification review decision was 'reject'")

    evidence = load_all_evidence()
    evidence_text = format_evidence_for_llm(evidence)
    acceptance = load_state_file("inputs/ACCEPTANCE_CRITERIA.md")
    requirements = load_state_file("inputs/REQUIREMENTS.md")

    verify_cfg = _load_verify_config()
    mode = verify_cfg.get("mode", "always_llm")
    llm_on_fail = verify_cfg.get("llm_on_fail_summary", True)
    llm_on_pass = verify_cfg.get("llm_on_pass_summary", True)

    passed = _all_checks_passed(evidence)

    # Decide whether to call LLM
    call_llm = False
    rationale = ""
    if mode == "always_llm":
        call_llm = True
        rationale = "mode=always_llm"
    elif mode == "never_llm":
        call_llm = False
        rationale = "mode=never_llm"
    elif mode == "auto":
        if passed and llm_on_pass:
            call_llm = True
            rationale = "auto: all passed, llm_on_pass_summary=true"
        elif passed and not llm_on_pass:
            call_llm = False
            rationale = "auto: all passed, llm_on_pass_summary=false"
        elif not passed and llm_on_fail:
            call_llm = True
            rationale = "auto: failures detected, llm_on_fail_summary=true"
        else:
            call_llm = False
            rationale = "auto: failures detected, llm_on_fail_summary=false"
    else:
        call_llm = True  # unknown mode, fallback to LLM
        rationale = f"unknown mode '{mode}', fallback to LLM"

    # Execute
    cache_hit = False
    cache_key = None
    model_used = None
    provider_name = None
    p_hash = None
    max_tokens_used = None

    if call_llm:
        prompt_template = (get_prompts_dir() / "verify.txt").read_text()

        # Build structural analysis for the LLM
        issues = _classify_issues(evidence)
        if issues:
            structural_lines = []
            category_labels = {
                "contract_issues": "Contract Compliance",
                "type_errors": "Type Errors",
                "import_errors": "Import / Dependency Errors",
                "lint_errors": "Lint Errors",
                "build_errors": "Build Errors",
                "test_failures": "Test Failures",
                "other_failures": "Other Failures",
            }
            for cat, items in issues.items():
                label = category_labels.get(cat, cat)
                structural_lines.append(f"### {label} ({len(items)} issue(s))")
                for item in items:
                    structural_lines.append(f"- {item}")
                structural_lines.append("")
            structural_analysis = "\n".join(structural_lines)
        else:
            structural_analysis = "No structural issues detected — all checks passed."

        prompt = prompt_template.format(
            evidence=evidence_text,
            structural_analysis=structural_analysis,
            acceptance_criteria=acceptance,
            requirements=requirements,
        )

        provider = get_provider(stage="verify")
        p_hash = hash_prompt(prompt)
        model_used = provider.model
        provider_name = provider.provider
        max_tokens_used = provider.max_tokens

        # Cache integration
        template_hash = hash_content(prompt_template)
        envelope_hash = hash_content(evidence_text + acceptance + requirements)
        params_h = hash_params(provider.model, provider.max_tokens)
        cache_key = build_cache_key(
            "verify", template_hash, envelope_hash, provider.model, params_h
        )

        cached = cache_lookup(cache_key)
        if cached is not None:
            verification = cached
            cache_hit = True
        else:
            verification = provider.generate(prompt)
            cache_save(cache_key, verification, "verify", provider.model)
            cache_hit = False
    else:
        verification = _build_deterministic_verification(evidence, passed)

    output_path = "tests/VERIFICATION.md"
    save_state_file(output_path, verification)

    run_id = get_run_id()
    evidence_rel = [f"runs/{run_id}/evidence/{r['name']}.json" for r in evidence]
    trace(
        task="verify",
        inputs=evidence_rel + ["inputs/ACCEPTANCE_CRITERIA.md", "inputs/REQUIREMENTS.md"],
        outputs=[output_path],
        model=model_used,
        prompt_hash=p_hash,
        provider=provider_name,
        max_tokens=max_tokens_used,
        extra={
            "verify_mode": mode,
            "llm_called": call_llm,
            "rationale": rationale,
            "all_checks_passed": passed,
            "cache_hit": cache_hit,
            "cache_key": cache_key,
            "usage": provider.total_usage
            if call_llm
            else {"input_tokens": 0, "output_tokens": 0, "llm_calls": 0},
        },
    )

    # Gate trigger
    if call_llm and "REJECTED" in verification and not decision_exists("verification_review"):
        raise DecisionRequired("verification_review", "verify", ["accept", "reject"])
