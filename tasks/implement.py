"""Implement task — the AI writes the actual code, guided by the design contract.

This is the main code generation stage.  The AI receives the design contract
(the binding blueprint) and produces source code files that implement it.

Two modes:
    1. **Single-call** — one AI call produces the entire implementation.  Used when
       the project is small enough to fit in one response.
    2. **Chunked** — for larger projects, the system splits the architecture into
       components and asks the AI to implement each one separately.  Each chunk
       receives the same shared type definitions, so the AI can't invent conflicting
       versions of the same data structure.  Results are merged at the end.
"""

import json
import logging
import re

from engine.compat import task

from engine.cache import build_cache_key, cache_lookup, cache_save, hash_content, hash_params
from engine.context import get_prompts_dir
from engine.extraction import (
    extract_json_block,
    extract_ts_type_signatures,
    extract_py_type_signatures,
)
from engine.llm_provider import get_model_limit, get_provider
from engine.design_contract import DesignContract
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
    ".ts",
    ".tsx",
    ".js",
    ".jsx",  # TypeScript / JavaScript
    ".py",
    ".pyi",  # Python
}

# Max characters of contract text per file — keeps the prompt bounded.
_MAX_CONTRACT_CHARS_PER_FILE = 3000

# Max total contract characters injected into a chunk prompt.
_MAX_TOTAL_CONTRACT_CHARS = 12000


def _extract_type_contracts(manifest_json: str) -> str:
    """Extract exported type signatures from a chunk's manifest files.

    Scans each file in the manifest for type-defining patterns
    (interfaces, types, enums, classes, constants) using the brace-counting
    extractors in ``engine.extraction``.  Returns a formatted string ready
    to inject into the next chunk's prompt, with each file's contracts
    labeled by path so the LLM knows where to import from.

    Uses brace-counting (not ``[^}]*``) for TypeScript interfaces/enums
    so nested types like ``{ database: { host: string } }`` are captured
    correctly.  Python classes use indentation-based extraction to capture
    the full body (field names + types), not just the signature line.
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

        # Use the appropriate extractor based on language
        if ext in (".py", ".pyi"):
            matches = extract_py_type_signatures(content)
        else:
            matches = extract_ts_type_signatures(content)

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


# ── Canonical type schema extraction ─────────────────────────────────────────

_CANONICAL_SCHEMA_HEADER = "## Canonical Type Schema"


def _extract_canonical_schema(architecture: str) -> str:
    """Extract the Canonical Type Schema section from the architecture document.

    Returns the raw text of the schema section (including code blocks), or
    an empty string if not found.  This schema is the single source of truth
    for all shared types — it is injected into *every* implementation chunk
    so the LLM never has to guess at field names.
    """
    idx = architecture.find(_CANONICAL_SCHEMA_HEADER)
    if idx == -1:
        logger.warning(
            "Architecture document has no '%s' section. "
            "Type consistency between chunks may suffer.",
            _CANONICAL_SCHEMA_HEADER,
        )
        return ""

    # Find the next H2 heading after the schema section to delimit the end
    rest = architecture[idx + len(_CANONICAL_SCHEMA_HEADER) :]
    next_h2 = re.search(r"^## ", rest, re.MULTILINE)
    if next_h2:
        schema_text = rest[: next_h2.start()].strip()
    else:
        schema_text = rest.strip()

    logger.info(
        "Extracted canonical type schema (%d chars) from architecture.",
        len(schema_text),
    )
    return schema_text


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


def _repair_json(raw: str) -> str | None:
    """Attempt lightweight repairs on malformed LLM JSON.

    LLMs commonly produce JSON with:
    - Trailing commas before closing braces/brackets
    - Missing commas between elements
    - Truncated output (unterminated strings, missing closing braces)

    This does NOT attempt to be a general-purpose JSON fixer — it handles
    the specific failure modes we see from code-generation LLMs. Returns
    the repaired string if successful, None if repair fails.

    Security note: This only adds/removes punctuation. It never modifies
    string content or injects new keys, so it can't change the semantic
    meaning of the manifest.
    """
    import re as _re

    # Pass 1: strip trailing commas before } or ]
    # e.g., {"a": 1,} → {"a": 1}
    repaired = _re.sub(r",\s*([}\]])", r"\1", raw)

    # Pass 2: add missing commas between elements
    # e.g., "value"\n"key" → "value",\n"key"
    # and   }\n{ → },\n{  and  ]\n[ → ],\n[
    repaired = _re.sub(r'"\s*\n(\s*")', r'",\n\1', repaired)
    repaired = _re.sub(r"}\s*\n(\s*{)", r"},\n\1", repaired)
    repaired = _re.sub(r"]\s*\n(\s*\[)", r"],\n\1", repaired)

    # Pass 3: close unterminated structures (truncated output)
    # Count open vs close braces/brackets and append missing closers
    open_braces = repaired.count("{") - repaired.count("}")
    open_brackets = repaired.count("[") - repaired.count("]")
    if open_braces > 0 or open_brackets > 0:
        # Terminate any open string first
        if repaired.count('"') % 2 != 0:
            repaired += '"'
        repaired += "]" * max(0, open_brackets)
        repaired += "}" * max(0, open_braces)

    try:
        json.loads(repaired)
        return repaired
    except json.JSONDecodeError:
        return None


def _extract_json(raw: str) -> str:
    """Strip optional ```json fences and validate JSON syntax.

    If initial parse fails, attempts lightweight repair for common LLM
    JSON mistakes (trailing commas, missing commas, truncated output)
    before raising.
    """
    raw = raw.strip()
    if raw.startswith("```"):
        first_newline = raw.index("\n")
        raw = raw[first_newline + 1 :]
    if raw.endswith("```"):
        raw = raw[: raw.rfind("```")].rstrip()
    try:
        json.loads(raw)
        return raw
    except json.JSONDecodeError as exc:
        # Attempt repair before giving up
        repaired = _repair_json(raw)
        if repaired is not None:
            logger.warning(
                "Manifest JSON had syntax errors — auto-repaired. Original error: %s",
                exc,
            )
            return repaired
        raise RuntimeError(f"Manifest JSON is malformed: {exc}") from exc


def _split_response(response: str) -> tuple[str | None, str | None]:
    """Split LLM response into (markdown, manifest_json).

    Returns (None, None) when the manifest markers are missing.

    The fallback path uses ``extract_json_block`` (JSON-parse-based scanning)
    instead of a regex.  The old regex ``r'```json\\s*(\\{.*?"files".*?\\})\\s*```'``
    broke on manifests with nested objects because lazy matching stopped at the
    first closing brace.
    """
    start = response.find(_MANIFEST_START)
    end = response.find(_MANIFEST_END)

    if start != -1 and end != -1 and end > start:
        markdown = response[:start].rstrip()
        raw_json = response[start + len(_MANIFEST_START) : end].strip()
        return markdown, _extract_json(raw_json)

    # Fallback: scan all ```json blocks for one containing "files"
    block = extract_json_block(response, required_key="files")
    if block is not None:
        # Find the position of this block in the response to split markdown
        # from manifest.  We search for the raw JSON string.
        block_pos = response.find(block)
        markdown = response[:block_pos].rstrip() if block_pos > 0 else ""
        # Remove any trailing fence chars from the markdown
        markdown = re.sub(r"```\s*json\s*$", "", markdown).rstrip()
        return markdown, _extract_json(block)

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
        recovery_provider.max_tokens,
        provider.max_tokens,
    )
    manifest_response = recovery_provider.generate(recovery_prompt)

    raw = manifest_response.strip()
    if raw.startswith("```"):
        first_newline = raw.index("\n")
        raw = raw[first_newline + 1 :]
    if raw.endswith("```"):
        raw = raw[: raw.rfind("```")].rstrip()

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
                path,
                len(owners),
                owners,
                owners[-1],
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
        raw = raw[first_newline + 1 :]
    if raw.endswith("```"):
        raw = raw[: raw.rfind("```")].rstrip()

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
    canonical_schema: str = "",
) -> tuple[str, str]:
    """Implement a single component chunk. Returns (markdown, manifest_json).

    Args:
        type_contracts: Extracted type signatures from previous chunks.
            Injected into the prompt so the LLM can import existing types
            instead of reinventing them.
        canonical_schema: The authoritative type schema from the architecture
            document.  Injected into every chunk as the single source of truth.
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

    # Format canonical schema
    if canonical_schema:
        canonical_schema_str = canonical_schema
    else:
        canonical_schema_str = (
            "(no canonical schema provided — use your best judgment for type definitions)"
        )

    prompt = chunk_template.format(
        architecture=architecture,
        requirements=requirements,
        constraints=constraints,
        component_name=component["name"],
        component_description=component["description"],
        existing_files=existing_files_str,
        type_contracts=type_contracts_str,
        canonical_schema=canonical_schema_str,
        tier_guidance=tier_guidance,
    )

    # Cache lookup for this specific chunk
    # NOTE: type_contracts, canonical_schema, and tier_guidance are included in the
    # envelope hash so that changes to earlier chunks, schema, or tier selection
    # correctly invalidate caches.
    template_hash = hash_content(chunk_template)
    envelope_hash = hash_content(
        architecture
        + requirements
        + constraints
        + component["name"]
        + component["description"]
        + "".join(existing_files)
        + type_contracts
        + canonical_schema
        + tier_guidance
    )
    params_h = hash_params(provider.model, provider.max_tokens)
    cache_key = build_cache_key(
        f"implement_chunk_{component['name']}",
        template_hash,
        envelope_hash,
        provider.model,
        params_h,
    )

    cached = cache_lookup(cache_key)
    if cached is not None:
        response = cached
        logger.info("  Cache hit for chunk '%s'", component["name"])
    else:
        logger.info(
            "  Generating chunk '%s' (%d token budget)...",
            component["name"],
            provider.max_tokens,
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
            estimated_output,
            _CHUNK_THRESHOLD * 100,
            budget,
        )
    else:
        logger.info(
            "Estimated output (%d tokens) fits within budget (%d tokens). "
            "Using single-call implementation.",
            estimated_output,
            budget,
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


def _load_design_contract() -> DesignContract | None:
    """Try to load the design contract from state.  Returns None if not found."""
    try:
        raw = load_state_file("designs/DESIGN_CONTRACT.json")
        return DesignContract.from_json(raw)
    except Exception:
        logger.info("No DESIGN_CONTRACT.json found — falling back to LLM-planned components.")
        return None


def _contract_to_components(contract: DesignContract) -> list[dict]:
    """Convert a design contract into the component dicts used by _implement_chunk.

    Each dict gets an extra ``contract_files`` key listing the exact files
    the chunk is expected to produce — this is injected into the prompt.
    """
    components = []
    for comp in contract.components:
        dep_descriptions = []
        for dep_name in comp.imports_from:
            dep = next((c for c in contract.components if c.name == dep_name), None)
            if dep:
                dep_descriptions.append(f"- **{dep.name}**: {dep.description}")

        imports_context = "\n".join(dep_descriptions) if dep_descriptions else "(none)"

        description = (
            f"{comp.description}\n\n"
            f"**You MUST produce exactly these files (no more, no fewer):**\n"
            + "\n".join(f"- `{f}`" for f in comp.files)
            + f"\n\n**Max files for this component: {comp.max_files}**"
            + f"\n\n**This component imports from:**\n{imports_context}"
        )

        if comp.exports_types:
            description += (
                "\n\n**This component defines these canonical types "
                "(use exact field names from the schema):** " + ", ".join(comp.exports_types)
            )

        components.append(
            {
                "name": comp.name,
                "description": description,
                "contract_files": comp.files,
                "max_files": comp.max_files,
            }
        )
    return components


def _build_canonical_schema_from_contract(contract: DesignContract) -> str:
    """Build a canonical schema string from the contract's type definitions.

    This is more reliable than extracting from prose because it comes
    from validated, structured JSON.
    """
    if not contract.canonical_types:
        return ""

    # Group types by file path
    by_file: dict[str, list] = {}
    for td in contract.canonical_types:
        by_file.setdefault(td.file_path, []).append(td)

    parts = []
    for file_path, types in by_file.items():
        lines = [f"### `{file_path}`"]
        lang = "typescript" if contract.language in ("typescript", "javascript") else "python"
        lines.append(f"```{lang}")

        for td in types:
            if lang in ("typescript",):
                lines.append(f"export {td.kind} {td.name} {{")
                for fname, ftype in td.fields.items():
                    lines.append(f"  {fname}: {ftype};")
                lines.append("}")
                lines.append("")
            else:
                lines.append("@dataclass")
                lines.append(f"class {td.name}:")
                for fname, ftype in td.fields.items():
                    lines.append(f"    {fname}: {ftype}")
                lines.append("")

        lines.append("```")
        parts.append("\n".join(lines))

    return "\n\n".join(parts)


@task(name="implement")
def implement_system() -> None:
    """Read architecture from state/designs/, generate implementation via LLM.

    If a DESIGN_CONTRACT.json exists, uses it as the authoritative spec:
    - Components come from the contract (no LLM planning step).
    - Each chunk gets its exact file list from the contract.
    - Canonical types come from the contract's structured definitions.

    Falls back to LLM-planned components if no contract is available.
    """
    architecture = load_state_file("designs/ARCHITECTURE.md")
    requirements = load_state_file("inputs/REQUIREMENTS.md")
    constraints = load_state_file("inputs/CONSTRAINTS.md")

    provider = get_provider(stage="implement")

    # ── Try to load the design contract ──────────────────────────────
    contract = _load_design_contract()

    if _should_chunk(architecture, requirements, constraints, provider):
        # ── Chunked implementation ──────────────────────────────────
        if contract:
            # Contract-driven: components come from the validated contract.
            # No LLM planning step — the architect already decided.
            components = _contract_to_components(contract)
            canonical_schema = _build_canonical_schema_from_contract(contract)
            logger.info(
                "Using design contract: %d components, %d canonical types, budget %d files.",
                len(contract.components),
                len(contract.canonical_types),
                contract.total_file_budget,
            )
        else:
            # Legacy fallback: ask the LLM to split the architecture.
            components = _plan_components(provider, architecture)
            canonical_schema = _extract_canonical_schema(architecture)
            logger.warning(
                "No design contract — using LLM-planned components. Type consistency may suffer."
            )

        all_markdowns: list[str] = []
        all_manifests: list[str] = []
        all_files: list[str] = []
        accumulated_contracts: list[str] = []  # type contracts from all previous chunks

        for i, component in enumerate(components):
            logger.info(
                "Implementing chunk %d/%d: %s",
                i + 1,
                len(components),
                component["name"],
            )

            # Build the type contracts string from all previous chunks
            type_contracts = "\n\n".join(accumulated_contracts)

            md, manifest_json = _implement_chunk(
                provider,
                architecture,
                requirements,
                constraints,
                component,
                all_files,
                type_contracts=type_contracts,
                canonical_schema=canonical_schema,
            )
            all_markdowns.append(f"## Component: {component['name']}\n\n{md}")
            all_manifests.append(manifest_json)

            # Track files produced so far for context in next chunk
            parsed = json.loads(manifest_json)
            chunk_files = [f["path"] for f in parsed["files"]]
            all_files.extend(chunk_files)

            # If contract-driven, log adherence to file list
            if contract and "contract_files" in component:
                expected = set(component["contract_files"])
                actual = set(chunk_files)
                extra_files = actual - expected
                missing_files = expected - actual
                if extra_files:
                    logger.warning(
                        "  Chunk '%s' produced %d EXTRA files not in contract: %s",
                        component["name"],
                        len(extra_files),
                        sorted(extra_files),
                    )
                if missing_files:
                    logger.warning(
                        "  Chunk '%s' is MISSING %d files from contract: %s",
                        component["name"],
                        len(missing_files),
                        sorted(missing_files),
                    )
                if not extra_files and not missing_files:
                    logger.info(
                        "  Chunk '%s' matches contract exactly (%d files).",
                        component["name"],
                        len(actual),
                    )

            # Extract type contracts from this chunk for subsequent chunks
            chunk_contracts = _extract_type_contracts(manifest_json)
            if chunk_contracts:
                accumulated_contracts.append(chunk_contracts)
                logger.info(
                    "  Extracted %d chars of type contracts from chunk '%s'.",
                    len(chunk_contracts),
                    component["name"],
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
            len(components),
            final_file_count,
            len(all_files),
            len(merge_conflicts),
            sum(len(c) for c in accumulated_contracts),
        )
    else:
        # ── Single-call implementation ──────────────────────────────
        markdown, manifest_json, cache_key, cache_hit = _implement_single(
            provider,
            architecture,
            requirements,
            constraints,
        )
        merge_conflicts = []

    # ── Save outputs + trace ────────────────────────────────────────────
    p_hash = hash_prompt(architecture + requirements + constraints)
    md_path = "implementations/IMPLEMENTATION.md"
    manifest_path = "implementations/FILE_MANIFEST.json"
    save_state_file(md_path, markdown)
    save_state_file(manifest_path, manifest_json)

    extra = {
        "cache_hit": cache_hit,
        "cache_key": cache_key,
        "contract_driven": contract is not None,
        "usage": provider.total_usage,
    }
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
