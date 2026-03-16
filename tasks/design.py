"""Design task — LLM generates architecture from requirements."""

from prefect import task

from engine.cache import build_cache_key, cache_lookup, cache_save, hash_content, hash_params
from engine.context import get_prompts_dir
from engine.decision_gates import DecisionRequired, decision_exists, load_decision
from engine.llm_provider import get_provider
from engine.state_loader import load_state_file, save_state_file
from engine.tier_context import get_design_guidance
from engine.tracer import hash_prompt, trace


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
    envelope_hash = hash_content(requirements + constraints + non_goals + extra_context + tier_guidance)
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

    output_path = "designs/ARCHITECTURE.md"
    save_state_file(output_path, architecture)

    trace(
        task="design",
        inputs=["inputs/REQUIREMENTS.md", "inputs/CONSTRAINTS.md", "inputs/NON_GOALS.md"],
        outputs=[output_path],
        model=provider.model,
        prompt_hash=p_hash,
        provider=provider.provider,
        max_tokens=provider.max_tokens,
        extra={"cache_hit": cache_hit, "cache_key": cache_key},
    )
