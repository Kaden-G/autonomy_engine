"""Implement task — LLM generates code from the approved design."""

import json

from prefect import task

from engine.context import get_prompts_dir
from engine.llm_provider import get_provider
from engine.state_loader import load_state_file, save_state_file
from engine.tracer import hash_prompt, trace

_MANIFEST_START = "<!-- FILE_MANIFEST_START -->"
_MANIFEST_END = "<!-- FILE_MANIFEST_END -->"


def _split_response(response: str) -> tuple[str, str]:
    """Split LLM response into (markdown, manifest_json).

    Extracts JSON between FILE_MANIFEST_START / FILE_MANIFEST_END markers,
    strips optional ```json fences, and validates JSON syntax.
    """
    start = response.find(_MANIFEST_START)
    end = response.find(_MANIFEST_END)

    if start == -1 or end == -1 or end <= start:
        raise RuntimeError(
            "LLM response is missing FILE_MANIFEST markers. "
            "Expected <!-- FILE_MANIFEST_START --> and <!-- FILE_MANIFEST_END -->."
        )

    markdown = response[:start].rstrip()
    raw_json = response[start + len(_MANIFEST_START) : end].strip()

    # Strip optional ```json ... ``` fences
    if raw_json.startswith("```"):
        first_newline = raw_json.index("\n")
        raw_json = raw_json[first_newline + 1 :]
    if raw_json.endswith("```"):
        raw_json = raw_json[: raw_json.rfind("```")].rstrip()

    # Validate JSON syntax
    try:
        json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Manifest JSON is malformed: {exc}") from exc

    return markdown, raw_json


@task(name="implement")
def implement_system() -> None:
    """Read architecture from state/designs/, generate implementation via LLM."""
    architecture = load_state_file("designs/ARCHITECTURE.md")
    requirements = load_state_file("inputs/REQUIREMENTS.md")
    constraints = load_state_file("inputs/CONSTRAINTS.md")

    prompt_template = (get_prompts_dir() / "implement.txt").read_text()
    prompt = prompt_template.format(
        architecture=architecture,
        requirements=requirements,
        constraints=constraints,
    )

    provider = get_provider(stage="implement")
    p_hash = hash_prompt(prompt)
    response = provider.generate(prompt)

    markdown, manifest_json = _split_response(response)

    md_path = "implementations/IMPLEMENTATION.md"
    manifest_path = "implementations/FILE_MANIFEST.json"
    save_state_file(md_path, markdown)
    save_state_file(manifest_path, manifest_json)

    trace(
        task="implement",
        inputs=["designs/ARCHITECTURE.md", "inputs/REQUIREMENTS.md", "inputs/CONSTRAINTS.md"],
        outputs=[md_path, manifest_path],
        model=provider.model,
        prompt_hash=p_hash,
        provider=provider.provider,
        max_tokens=provider.max_tokens,
    )
