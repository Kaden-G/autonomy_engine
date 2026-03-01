"""Verify task — LLM assesses real execution evidence against acceptance criteria.

The LLM summarizes and interprets evidence but does not replace it.
"""

from prefect import task

from engine.context import get_prompts_dir
from engine.decision_gates import DecisionRequired, decision_exists, load_decision
from engine.evidence import format_evidence_for_llm, load_all_evidence
from engine.llm_provider import get_provider
from engine.state_loader import load_state_file, save_state_file
from engine.tracer import get_run_id, hash_prompt, trace


@task(name="verify")
def verify_system() -> None:
    """Load execution evidence, have LLM assess against acceptance criteria."""
    # Decision guard: if a previous review decision was "reject", stop immediately
    if decision_exists("verification_review"):
        record = load_decision("verification_review")
        if record["selected"] == "reject":
            raise RuntimeError("Verification review decision was 'reject'")

    evidence = load_all_evidence()
    evidence_text = format_evidence_for_llm(evidence)
    acceptance = load_state_file("inputs/ACCEPTANCE_CRITERIA.md")
    requirements = load_state_file("inputs/REQUIREMENTS.md")

    prompt_template = (get_prompts_dir() / "verify.txt").read_text()
    prompt = prompt_template.format(
        evidence=evidence_text,
        acceptance_criteria=acceptance,
        requirements=requirements,
    )

    provider = get_provider(stage="verify")
    p_hash = hash_prompt(prompt)
    verification = provider.generate(prompt)

    output_path = "tests/VERIFICATION.md"
    save_state_file(output_path, verification)

    run_id = get_run_id()
    evidence_rel = [f"runs/{run_id}/evidence/{r['name']}.json" for r in evidence]
    trace(
        task="verify",
        inputs=evidence_rel + ["inputs/ACCEPTANCE_CRITERIA.md", "inputs/REQUIREMENTS.md"],
        outputs=[output_path],
        model=provider.model,
        prompt_hash=p_hash,
        provider=provider.provider,
        max_tokens=provider.max_tokens,
    )

    # Gate trigger: raise if LLM rejected and no decision recorded yet
    if "REJECTED" in verification and not decision_exists("verification_review"):
        raise DecisionRequired("verification_review", "verify", ["accept", "reject"])
