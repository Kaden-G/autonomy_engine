"""Implement task — LLM generates code from the approved design.

Supports two modes:
    1. **Single-call** (default) — one LLM call produces the entire implementation.
    2. **Chunked** — when the estimated output exceeds the token budget, the
       architecture is split into components and each is implemented in a
       separate call.  Manifests are merged at the end.
"""

import json
import logging
import re

from prefect import task

from engine.cache import build_cache_key, cache_lookup, cache_save, hash_content, hash_params
from engine.context import get_prompts_dir
from engine.llm_provider import get_model_limit, get_provider
from engine.state_loader import load_state_file, save_state_file
from engine.tier_context import get_implement_guidance
from engine.tracer import hash_prompt, trace

logger = logging.getLogger(__name__)

_MANIFEST_START = "<!-- FILE_MANIFEST_START -->"
_MANIFEST_END = "<!-- FILE_MANIFEST_END -->"

# Token-per-char heuristic (matches cost_estimator)
_CHARS_PER_TOKEN = 4

# If estimated output tokens exceed this fraction of the budget, chunk.
_CHUNK_THRESHOLD = 0.85

# ── Type contract extraction ─────────────────────────────────────────────────
# File extensions whose exported types/interfaces should be forwarded to
# subsequent chunks so they can import instead of reinventing.

_CONTRACT_EXTENSIONS = {
    ".ts", ".tsx", ".js", ".jsx",   # TypeScript / JavaScript
    ".py", ".pyi",                   # Python
}

# Max characters of contract text per file — keeps the prompt bounded.
_MAX_CONTRACT_CHARS_PER_FILE = 3000

# Max total contract characters injected into a chunk prompt.
_MAX_TOTAL_CONTRACT_CHARS = 12000

# TypeScript/JavaScript patterns for exported type definitions
_TS_CONTRACT_PATTERNS = [
    # export interface Foo { ... }  (capture up to closing brace)
    re.compile(
        r"^(export\s+(?:default\s+)?interface\s+\w+(?:\s+extends\s+[^{]+)?\s*\{[^}]*\})",
        re.MULTILINE | re.DOTALL,
    ),
    # export type Foo = ...;
    re.compile(
        r"^(export\s+(?:default\s+)?type\s+\w+\s*=\s*[^;]+;)",
        re.MULTILINE,
    ),
    # export enum Foo { ... }
    re.compile(
        r"^(export\s+(?:default\s+)?(?:const\s+)?enum\s+\w+\s*\{[^}]*\})",
        re.MULTILINE | re.DOTALL,
    ),
    # export class Foo { (signature line only, not the body)
    re.compile(
        r"^(export\s+(?:default\s+)?(?:abstract\s+)?class\s+\w+(?:\s+(?:extends|implements)\s+[^{]+)?\s*\{)",
        re.MULTILINE,
    ),
    # export const FOO = ... (UPPER_CASE constants only — configs, enums-as-objects)
    re.compile(
        r"^(export\s+const\s+[A-Z_][A-Z_0-9]*\s*(?::\s*[^=]+)?\s*=\s*.+)",
        re.MULTILINE,
    ),
    # export function foo(params): ReturnType  (signature only)
    re.compile(
        r"^(export\s+(?:default\s+)?(?:async\s+)?function\s+\w+\s*\([^)]*\)\s*(?::\s*[^{]+)?)\s*\{",
        re.MULTILINE,
    ),
]

# Python patterns for type definitions
_PY_CONTRACT_PATTERNS = [
    # class Foo(Base):  or  @dataclass class Foo:
    re.compile(
        r"^((?:@\w+(?:\([^)]*\))?\s*\n)?class\s+\w+(?:\([^)]*\))?\s*:)",
        re.MULTILINE,
    ),
    # Foo: TypeAlias = ...
    re.compile(
        r"^(\w+\s*:\s*TypeAlias\s*=\s*.+)",
        re.MULTILINE,
    ),
    # Foo = TypeVar(...)
    re.compile(
        r"^(\w+\s*=\s*TypeVar\(.+\))",
        re.MULTILINE,
    ),
]


def _extract_type_contracts(manifest_json: str) -> str:
    """Extract exported type signatures from a chunk's manifest files.

    Scans each file in the manifest for type-defining patterns
    (interfaces, types, enums, classes, constants) using regex.
    Returns a formatted string ready to inject into the next chunk's prompt,
    with each file's contracts labeled by path so the LLM knows where to
    import from.

    This is intentionally regex-based (no AST parsing) to be fast,
    dependency-free, and tolerant of incomplete code.
    """
    parsed = json.loads(manifest_json)
    contracts: list[str] = []
    total_chars = 0

    for file_entry in parsed.get("files", []):
        path = file_entry.get("path", "")
        content = file_entry.get("content", "")

        # Determine file extension
        dot_idx = path.rfind(".")
        ext = path[dot_idx:] if dot_idx != -1 else ""

        if ext not in _CONTRACT_EXTENSIONS:
            continue

        # Select patterns based on language
        patterns = _PY_CONTRACT_PATTERNS if ext in (".py", ".pyi") else _TS_CONTRACT_PATTERNS

        matches: list[str] = []
        for pattern in patterns:
            for m in pattern.finditer(content):
                match_text = m.group(1).strip()
                if match_text and match_text not in matches:
                    matches.append(match_text)

        if not matches:
            continue

        file_contract = "\n".join(matches)

        # Truncate per-file contracts if needed
        if len(file_contract) > _MAX_CONTRACT_CHARS_PER_FILE:
            file_contract = file_contract[:_MAX_CONTRACT_CHARS_PER_FILE] + "\n// ... (truncated)"

        # Check total budget
        entry_text = f"// From {path}:\n{file_contract}"
        if total_chars + len(entry_text) > _MAX_TOTAL_CONTRACT_CHARS:
            contracts.append("// ... (remaining contracts omitted for token budget)")
            break

        contracts.append(entry_text)
        total_chars += len(entry_text)

    if not contracts:
        return ""

    return "\n\n".join(contracts)


_MANIFEST_RECOVERY_PROMPT = """\
Your previous response was cut short before you could include the FILE_MANIFEST.

Here is the implementation you produced (truncated):
---
{truncated_response_tail}
---

Now produce ONLY the file manifest as a JSON object.  List every file from your
implementation.  Use this exact format:

```json
{{
  "files": [
    {{"path": "relative/path/to/file.py", "content": "full file content here"}}
  ]
}}
```

Rules:
- Include EVERY file from the implementation above.
- `content` must contain the COMPLETE file content (use `\\n` for newlines).
- Output ONLY the JSON block — no markdown, no explanation.
"""


# ── JSON / manifest helpers ──────────────────────────────────────────────────

def _extract_json(raw: str) -> str:
    """Strip optional ```json fences and validate JSON syntax."""
    raw = raw.strip()
    if raw.startswith("```"):
        first_newline = raw.index("\n")
        raw = raw[first_newline + 1:]
    if raw.endswith("```"):
        raw = raw[:raw.rfind("```")].rstrip()
    try:
        json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Manifest JSON is malformed: {exc}") from exc
    return raw


def _split_response(response: str) -> tuple[str | None, str | None]:
    """Split LLM response into (markdown, manifest_json).

    Returns (None, None) when the manifest markers are missing.
    """
    start = response.find(_MANIFEST_START)
    end = response.find(_MANIFEST_END)

    if start != -1 and end != -1 and end > start:
        markdown = response[:start].rstrip()
        raw_json = response[start + len(_MANIFEST_START):end].strip()
        return markdown, _extract_json(raw_json)

    # Fallback: find a JSON code block with a "files" array
    pattern = r"```json\s*(\{[\s\S]*?\"files\"\s*:\s*\[[\s\S]*?\})\s*```"
    match = re.search(pattern, response)
    if match:
        raw_json = match.group(1)
        markdown = response[:match.start()].rstrip()
        return markdown, _extract_json(raw_json)

    return None, None


def _recover_manifest(provider, response: str) -> str:
    """Follow-up LLM call to extract the file manifest from a truncated response.

    Uses a dedicated provider with the model's full token capacity —
    recovery is a critical operation and should never be budget-capped
    by the tier that caused the truncation in the first place.
    """
    tail = response[-6000:] if len(response) > 6000 else response
    recovery_prompt = _MANIFEST_RECOVERY_PROMPT.format(
        truncated_response_tail=tail,
    )
    logger.info(
        "Response was truncated (hit %d token limit). "
        "Making recovery call for file manifest with full model capacity...",
        provider.max_tokens,
    )
    # Use a fresh provider at the model's hard limit — don't inherit
    # the chunk's budget cap that caused the truncation.
    hard_limit = get_model_limit(provider.provider, provider.model)
    recovery_provider = get_provider(
        stage="implement",
        max_tokens_override=hard_limit,
    )
    logger.info(
        "Recovery provider: %d tokens (vs chunk budget of %d)",
        recovery_provider.max_tokens, provider.max_tokens,
    )
    manifest_response = recovery_provider.generate(recovery_prompt)

    raw = manifest_response.strip()
    if raw.startswith("```"):
        first_newline = raw.index("\n")
        raw = raw[first_newline + 1:]
    if raw.endswith("```"):
        raw = raw[:raw.rfind("```")].rstrip()

    try:
        parsed = json.loads(raw)
        if "files" not in parsed:
            raise RuntimeError("Recovery response missing 'files' key")
        return raw
    except (json.JSONDecodeError, RuntimeError) as exc:
        raise RuntimeError(
            f"Manifest recovery failed: {exc}. "
            f"Try re-running with a higher token budget "
            f"(current: {provider.max_tokens}). You can increase "
            f"max_tokens in config.yml or select the Premium tier."
        ) from exc


def _merge_manifests(
    manifest_jsons: list[str],
    component_names: list[str] | None = None,
) -> tuple[str, list[dict]]:
    """Merge multiple per-component manifests into a single manifest.

    Returns ``(merged_json, conflicts)`` where *conflicts* is a list of
    dicts describing duplicate paths::

        {"path": "src/types/index.ts",
         "chunks": ["Core Types", "Database Layer"],
         "winner": "Database Layer"}

    Last-writer-wins is preserved for backward compatibility, but
    conflicts are now surfaced so they can be logged and traced.
    """
    names = component_names or [f"chunk_{i}" for i in range(len(manifest_jsons))]

    # Track which chunk(s) produced each path
    path_owners: dict[str, list[str]] = {}
    seen: dict[str, dict] = {}

    for mj, chunk_name in zip(manifest_jsons, names):
        parsed = json.loads(mj)
        for f in parsed["files"]:
            path = f["path"]
            if path in path_owners:
                path_owners[path].append(chunk_name)
            else:
                path_owners[path] = [chunk_name]
            seen[path] = f

    # Build conflict report
    conflicts: list[dict] = []
    for path, owners in path_owners.items():
        if len(owners) > 1:
            conflict = {
                "path": path,
                "chunks": owners,
                "winner": owners[-1],  # last-writer-wins
            }
            conflicts.append(conflict)
            logger.warning(
                "Manifest conflict: '%s' produced by %d chunks %s — "
                "keeping version from '%s' (last-writer-wins).",
                path, len(owners), owners, owners[-1],
            )

    if conflicts:
        logger.warning(
            "%d duplicate path(s) detected across chunks. "
            "This often means the LLM re-created files that already existed. "
            "The type-contracts system should reduce this — if it persists, "
            "consider adjusting the component plan.",
            len(conflicts),
        )

    merged = {"files": list(seen.values())}
    return json.dumps(merged, indent=2), conflicts


# ── Chunked implementation ───────────────────────────────────────────────────

def _plan_components(provider, architecture: str) -> list[dict]:
    """Ask the LLM to split the architecture into implementable chunks.

    Returns a list of dicts: [{"name": "...", "description": "..."}, ...]
    """
    plan_template = (get_prompts_dir() / "implement_plan.txt").read_text()
    prompt = plan_template.format(architecture=architecture)

    logger.info("Planning component split for chunked implementation...")
    response = provider.generate(prompt)

    # Extract JSON from the response
    raw = response.strip()
    if raw.startswith("```"):
        first_newline = raw.index("\n")
        raw = raw[first_newline + 1:]
    if raw.endswith("```"):
        raw = raw[:raw.rfind("```")].rstrip()

    try:
        components = json.loads(raw)
        if not isinstance(components, list) or len(components) == 0:
            raise RuntimeError("Component plan is not a non-empty list")
        for c in components:
            if "name" not in c or "description" not in c:
                raise RuntimeError(f"Component missing name/description: {c}")
        logger.info(
            "Component plan: %d chunks — %s",
            len(components),
            ", ".join(c["name"] for c in components),
        )
        return components
    except (json.JSONDecodeError, RuntimeError) as exc:
        raise RuntimeError(
            f"Failed to parse component plan: {exc}. "
            f"The architecture may need clearer component boundaries."
        ) from exc


def _implement_chunk(
    provider,
    architecture: str,
    requirements: str,
    constraints: str,
    component: dict,
    existing_files: list[str],
    type_contracts: str = "",
) -> tuple[str, str]:
    """Implement a single component chunk. Returns (markdown, manifest_json).

    Args:
        type_contracts: Extracted type signatures from previous chunks.
            Injected into the prompt so the LLM can import existing types
            instead of reinventing them.
    """
    tier_guidance = get_implement_guidance()
    chunk_template = (get_prompts_dir() / "implement_chunk.txt").read_text()

    existing_files_str = (
        "\n".join(f"- `{f}`" for f in existing_files)
        if existing_files
        else "(none — this is the first chunk)"
    )

    # Format type contracts for the prompt
    if type_contracts:
        type_contracts_str = type_contracts
    else:
        type_contracts_str = "(none — this is the first chunk)"

    prompt = chunk_template.format(
        architecture=architecture,
        requirements=requirements,
        constraints=constraints,
        component_name=component["name"],
        component_description=component["description"],
        existing_files=existing_files_str,
        type_contracts=type_contracts_str,
        tier_guidance=tier_guidance,
    )

    # Cache lookup for this specific chunk
    # NOTE: type_contracts and tier_guidance are included in the envelope hash so
    # that changes to earlier chunks or tier selection correctly invalidate caches.
    template_hash = hash_content(chunk_template)
    envelope_hash = hash_content(
        architecture + requirements + constraints
        + component["name"] + component["description"]
        + "".join(existing_files)
        + type_contracts
        + tier_guidance
    )
    params_h = hash_params(provider.model, provider.max_tokens)
    cache_key = build_cache_key(
        f"implement_chunk_{component['name']}",
        template_hash, envelope_hash, provider.model, params_h,
    )

    cached = cache_lookup(cache_key)
    if cached is not None:
        response = cached
        logger.info("  Cache hit for chunk '%s'", component["name"])
    else:
        logger.info(
            "  Generating chunk '%s' (%d token budget)...",
            component["name"], provider.max_tokens,
        )
        response = provider.generate(prompt)
        cache_save(cache_key, response, f"implement_chunk_{component['name']}", provider.model)

    markdown, manifest_json = _split_response(response)

    # Recovery if truncated
    if manifest_json is None:
        if provider.was_truncated:
            manifest_json = _recover_manifest(provider, response)
            markdown = response
            logger.info("  Manifest for '%s' recovered via follow-up call.", component["name"])
        else:
            raise RuntimeError(
                f"Chunk '{component['name']}' is missing FILE_MANIFEST markers "
                f"and was not truncated. Check the implement_chunk.txt template."
            )

    return markdown, manifest_json


def _should_chunk(
    architecture: str,
    requirements: str,
    constraints: str,
    provider,
) -> bool:
    """Estimate whether the implementation will exceed the single-call budget.

    Uses a heuristic: if (input_tokens + estimated_output_tokens) > threshold
    of the provider's max_tokens, we should chunk.
    """
    input_size = len(architecture + requirements + constraints)
    input_tokens = input_size // _CHARS_PER_TOKEN

    # Implementation output is typically 5-8x the input for code generation
    estimated_output = input_tokens * 6
    budget = provider.max_tokens

    needs_chunking = estimated_output > (budget * _CHUNK_THRESHOLD)

    if needs_chunking:
        logger.info(
            "Estimated output (%d tokens) exceeds %.0f%% of budget (%d tokens). "
            "Switching to chunked implementation.",
            estimated_output, _CHUNK_THRESHOLD * 100, budget,
        )
    else:
        logger.info(
            "Estimated output (%d tokens) fits within budget (%d tokens). "
            "Using single-call implementation.",
            estimated_output, budget,
        )

    return needs_chunking


# ── Single-call implementation (original behavior) ──────────────────────────

def _implement_single(
    provider,
    architecture: str,
    requirements: str,
    constraints: str,
) -> tuple[str, str, str, bool]:
    """Single-call implementation. Returns (markdown, manifest_json, cache_key, cache_hit)."""
    tier_guidance = get_implement_guidance()

    prompt_template = (get_prompts_dir() / "implement.txt").read_text()
    prompt = prompt_template.format(
        architecture=architecture,
        requirements=requirements,
        constraints=constraints,
        tier_guidance=tier_guidance,
    )

    template_hash = hash_content(prompt_template)
    envelope_hash = hash_content(architecture + requirements + constraints + tier_guidance)
    params_h = hash_params(provider.model, provider.max_tokens)
    cache_key = build_cache_key("implement", template_hash, envelope_hash, provider.model, params_h)

    cached = cache_lookup(cache_key)
    if cached is not None:
        response = cached
        cache_hit = True
    else:
        response = provider.generate(prompt)
        cache_save(cache_key, response, "implement", provider.model)
        cache_hit = False

    markdown, manifest_json = _split_response(response)

    # Recovery on truncation
    if manifest_json is None:
        if provider.was_truncated or not cache_hit:
            manifest_json = _recover_manifest(provider, response)
            markdown = response
            logger.info("Manifest recovered via follow-up LLM call.")
        else:
            raise RuntimeError(
                "LLM response is missing FILE_MANIFEST markers. "
                "Expected <!-- FILE_MANIFEST_START --> and "
                "<!-- FILE_MANIFEST_END -->."
            )

    return markdown, manifest_json, cache_key, cache_hit


# ── Main task ───────────────────────────────────────────────────────────────

@task(name="implement")
def implement_system() -> None:
    """Read architecture from state/designs/, generate implementation via LLM.

    Automatically switches to chunked mode when the project is estimated
    to exceed the token budget for a single call.
    """
    architecture = load_state_file("designs/ARCHITECTURE.md")
    requirements = load_state_file("inputs/REQUIREMENTS.md")
    constraints = load_state_file("inputs/CONSTRAINTS.md")

    provider = get_provider(stage="implement")

    if _should_chunk(architecture, requirements, constraints, provider):
        # ── Chunked implementation ──────────────────────────────────
        components = _plan_components(provider, architecture)

        all_markdowns: list[str] = []
        all_manifests: list[str] = []
        all_files: list[str] = []
        accumulated_contracts: list[str] = []  # type contracts from all previous chunks

        for i, component in enumerate(components):
            logger.info(
                "Implementing chunk %d/%d: %s",
                i + 1, len(components), component["name"],
            )

            # Build the type contracts string from all previous chunks
            type_contracts = "\n\n".join(accumulated_contracts)

            md, manifest_json = _implement_chunk(
                provider, architecture, requirements, constraints,
                component, all_files,
                type_contracts=type_contracts,
            )
            all_markdowns.append(f"## Component: {component['name']}\n\n{md}")
            all_manifests.append(manifest_json)

            # Track files produced so far for context in next chunk
            parsed = json.loads(manifest_json)
            all_files.extend(f["path"] for f in parsed["files"])

            # Extract type contracts from this chunk for subsequent chunks
            chunk_contracts = _extract_type_contracts(manifest_json)
            if chunk_contracts:
                accumulated_contracts.append(chunk_contracts)
                logger.info(
                    "  Extracted %d chars of type contracts from chunk '%s'.",
                    len(chunk_contracts), component["name"],
                )

        markdown = "\n\n---\n\n".join(all_markdowns)
        component_names = [c["name"] for c in components]
        manifest_json, merge_conflicts = _merge_manifests(all_manifests, component_names)
        cache_key = f"chunked_{len(components)}_components"
        cache_hit = False

        # Count unique files after dedup
        final_file_count = len(json.loads(manifest_json)["files"])

        logger.info(
            "Chunked implementation complete: %d components, %d total files "
            "(%d before dedup), %d duplicate path(s), "
            "%d chars of type contracts accumulated.",
            len(components), final_file_count, len(all_files),
            len(merge_conflicts),
            sum(len(c) for c in accumulated_contracts),
        )
    else:
        # ── Single-call implementation ──────────────────────────────
        markdown, manifest_json, cache_key, cache_hit = _implement_single(
            provider, architecture, requirements, constraints,
        )
        merge_conflicts = []

    # ── Save outputs + trace ────────────────────────────────────────────
    p_hash = hash_prompt(architecture + requirements + constraints)
    md_path = "implementations/IMPLEMENTATION.md"
    manifest_path = "implementations/FILE_MANIFEST.json"
    save_state_file(md_path, markdown)
    save_state_file(manifest_path, manifest_json)

    extra = {"cache_hit": cache_hit, "cache_key": cache_key}
    if merge_conflicts:
        extra["merge_conflicts"] = merge_conflicts
        extra["merge_conflict_count"] = len(merge_conflicts)

    trace(
        task="implement",
        inputs=["designs/ARCHITECTURE.md", "inputs/REQUIREMENTS.md", "inputs/CONSTRAINTS.md"],
        outputs=[md_path, manifest_path],
        model=provider.model,
        prompt_hash=p_hash,
        provider=provider.provider,
        max_tokens=provider.max_tokens,
        extra=extra,
    )
