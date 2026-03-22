"""Design task — the AI creates the system architecture and binding contract.

This is where the AI reads the project requirements and produces two outputs:
    1. ARCHITECTURE.md — a human-readable architecture document explaining the
       design decisions, component structure, and technology choices.
    2. DESIGN_CONTRACT.json — the machine-readable blueprint that controls everything
       downstream.  This contract specifies exactly which files to create, which data
       types to define, and how components connect.  The implementation stage treats
       this contract as law.

This stage may pause at a decision gate for human approval of the architecture
before proceeding to code generation.
"""

import logging

from prefect import task

from engine.cache import build_cache_key, cache_lookup, cache_save, hash_content, hash_params
from engine.context import get_prompts_dir
from engine.decision_gates import DecisionRequired, decision_exists, load_decision
from engine.design_contract import ContractValidationError, extract_contract
from engine.llm_provider import get_provider
from engine.state_loader import load_state_file, save_state_file
from engine.tier_context import get_design_guidance
from engine.tracer import hash_prompt, trace

logger = logging.getLogger(__name__)


@task(name="design")
def design_system() -> None:
    """Read requirements from state/inputs/, generate architecture via LLM."""
    requirements = load_state_file("inputs/REQUIREMENTS.md")
    constraints = load_state_file("inputs/CONSTRAINTS.md")
    non_goals = load_state_file("inputs/NON_GOALS.md")

    # Decision guard: if this run already has an architecture decision,
    # feed the chosen approach back into the prompt
    decision_key = "architecture_choice_needed"
    if decision_exists(decision_key):
        record = load_decision(decision_key)
        chosen = record["selected"]
        extra_context = f"\n\nPrevious decision — chosen approach: {chosen}\n"
    else:
        extra_context = ""

    tier_guidance = get_design_guidance()

    prompt_template = (get_prompts_dir() / "design.txt").read_text()
    prompt = prompt_template.format(
        requirements=requirements,
        constraints=constraints,
        non_goals=non_goals,
        extra_context=extra_context,
        tier_guidance=tier_guidance,
    )

    provider = get_provider(stage="design")
    p_hash = hash_prompt(prompt)

    # Cache lookup
    template_hash = hash_content(prompt_template)
    envelope_hash = hash_content(
        requirements + constraints + non_goals + extra_context + tier_guidance
    )
    params_h = hash_params(provider.model, provider.max_tokens)
    cache_key = build_cache_key("design", template_hash, envelope_hash, provider.model, params_h)

    cached = cache_lookup(cache_key)
    if cached is not None:
        architecture = cached
        cache_hit = True
    else:
        architecture = provider.generate(prompt)
        cache_save(cache_key, architecture, "design", provider.model)
        cache_hit = False

    # If the LLM signals ambiguity, raise for human decision
    if "DECISION_REQUIRED:" in architecture:
        marker = architecture.split("DECISION_REQUIRED:")[1].strip()
        options = [opt.strip() for opt in marker.split("|") if opt.strip()]
        if not decision_exists(decision_key):
            raise DecisionRequired(decision_key, "design", options)

    # ── Save architecture prose ──────────────────────────────────────────
    output_path = "designs/ARCHITECTURE.md"
    save_state_file(output_path, architecture)

    # ── Extract and validate the design contract ─────────────────────────
    contract_path = "designs/DESIGN_CONTRACT.json"
    contract_extracted = False
    contract_errors: list[str] = []

    try:
        contract = extract_contract(architecture)
        save_state_file(contract_path, contract.to_json())
        contract_extracted = True
        logger.info(
            "Design contract saved: %d components, %d canonical types.",
            len(contract.components),
            len(contract.canonical_types),
        )
    except ContractValidationError as exc:
        # Contract exists but has validation errors — save it anyway for
        # debugging, but log the errors prominently.
        contract_errors = exc.errors
        logger.error(
            "Design contract has %d validation error(s):\n%s",
            len(exc.errors),
            "\n".join(f"  - {e}" for e in exc.errors),
        )
    except RuntimeError as exc:
        # No contract found at all — log warning but don't fail the stage.
        # The implement stage will fall back to the old behavior.
        logger.warning("Could not extract design contract: %s", exc)

    trace(
        task="design",
        inputs=["inputs/REQUIREMENTS.md", "inputs/CONSTRAINTS.md", "inputs/NON_GOALS.md"],
        outputs=[output_path] + ([contract_path] if contract_extracted else []),
        model=provider.model,
        prompt_hash=p_hash,
        provider=provider.provider,
        max_tokens=provider.max_tokens,
        extra={
            "cache_hit": cache_hit,
            "cache_key": cache_key,
            "contract_extracted": contract_extracted,
            "contract_errors": contract_errors,
            "usage": provider.total_usage,
        },
    )
