"""Verify task — assess execution evidence against acceptance criteria.

Supports three modes:
- always_llm: always call the LLM (original behavior)
- never_llm: always write deterministic VERIFICATION.md, never call LLM
- auto: call LLM only when configured (llm_on_fail_summary / llm_on_pass_summary)
"""

import yaml
from prefect import task

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


def _build_deterministic_verification(evidence: list[dict], passed: bool) -> str:
    """Write a deterministic VERIFICATION.md without calling the LLM."""
    lines = ["# Verification Summary", ""]

    if passed:
        lines.append("## Result: PASSED")
        lines.append("")
        lines.append("All configured checks passed successfully.")
    else:
        lines.append("## Result: FAILED")
        lines.append("")
        lines.append("One or more checks failed:")

    lines.append("")
    for r in evidence:
        if r.get("name") == "no_checks_configured":
            continue
        status = "PASS" if r.get("exit_code", -1) == 0 else "FAIL"
        lines.append(f"- **{r['name']}**: {status} (exit code {r.get('exit_code', 'N/A')})")

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
        prompt = prompt_template.format(
            evidence=evidence_text,
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
        },
    )

    # Gate trigger
    if call_llm and "REJECTED" in verification and not decision_exists("verification_review"):
        raise DecisionRequired("verification_review", "verify", ["accept", "reject"])
