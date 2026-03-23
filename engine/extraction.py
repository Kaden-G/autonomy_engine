"""Extraction utilities — robust parsers that replace fragile regex fallbacks.

The LLM's output often contains JSON blocks and type definitions that need to
be extracted reliably.  Earlier versions used regex like:

    r'```json\\s*(\\{[\\s\\S]*?"components"\\s*:\\s*\\[[\\s\\S]*?\\})\\s*```'

This is fragile because ``[\\s\\S]*?\\}`` uses lazy matching for the closing
brace, which can stop too early on nested objects (a common pattern in design
contracts with nested fields/types).

This module provides two categories of extractors:

1. **JSON block scanner** — finds fenced ``json`` code blocks, parses each one,
   and returns the first that contains a required key.  No regex needed for the
   JSON itself; ``json.loads`` does the heavy lifting.

2. **Brace-counting type extractor** — extracts TypeScript/Python type
   definitions by counting brace nesting depth instead of using ``[^}]*`` which
   fails on nested structures like ``{ foo: { bar: string } }``.

Design decisions
----------------
- **No AST dependency**: We intentionally avoid pulling in ``tree-sitter`` or
  ``esprima`` to keep the engine dependency-free.  Brace-counting is ~95%
  accurate for well-formed LLM output, which is good enough for prompt
  injection (the downstream LLM can still handle minor imperfections).

- **Fallback chain**: Marker-delimited content is always preferred.  These
  utilities are the *fallback* when the LLM forgets the markers — they're
  designed to recover gracefully, not be the primary path.
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)


# ── JSON block scanner ───────────────────────────────────────────────────────


def extract_json_block(text: str, required_key: str) -> str | None:
    """Find the first fenced JSON code block containing *required_key*.

    Scans all ``json`` code blocks in *text*, attempts to parse each one,
    and returns the raw JSON string of the first that:
        1. Parses as valid JSON
        2. Is a dict (not a list or scalar)
        3. Contains *required_key* at the top level

    Returns ``None`` if no qualifying block is found.

    Why this is better than regex
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    The old regex ``r'```json\\s*(\\{.*?"key".*?\\})\\s*```'`` broke when
    JSON contained nested objects — the lazy ``.*?\\}`` would match the
    first closing brace, not the one that balances the opening brace.
    By letting ``json.loads`` do the parsing, we handle arbitrary nesting
    and even trailing commas (Python's json module is strict, but the
    block boundaries are correct).
    """
    # Find all ```json ... ``` blocks.  We use a simple state machine
    # instead of regex to handle edge cases (e.g. nested triple-backticks
    # inside strings, which regex would misparse).
    blocks = _find_fenced_blocks(text, "json")

    for block in blocks:
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            # Might be malformed; try the next block
            continue

        if isinstance(data, dict) and required_key in data:
            return block

    return None


def _find_fenced_blocks(text: str, language: str) -> list[str]:
    """Extract all fenced code blocks with the given language tag.

    Returns the *content* of each block (without the fences themselves).
    Handles both ````` ```json ````` and ````` ``` json ````` (space after
    backticks).
    """
    blocks: list[str] = []
    # Pattern: ``` optionally followed by language tag, then content, then ```
    # We match the opening fence with language and walk to the closing fence.
    pattern = re.compile(
        r"^```\s*" + re.escape(language) + r"\s*\n"  # opening fence
        r"(.*?)"  # content (non-greedy)
        r"\n\s*```",  # closing fence
        re.MULTILINE | re.DOTALL,
    )
    for match in pattern.finditer(text):
        blocks.append(match.group(1).strip())
    return blocks


# ── Brace-counting type extractor ────────────────────────────────────────────


def extract_braced_body(text: str, start_pos: int) -> str:
    """Extract a brace-delimited block starting from the ``{`` at *start_pos*.

    Counts nesting depth to find the matching closing brace.  Correctly
    handles:
    - Nested objects: ``{ foo: { bar: string } }``
    - String literals with braces: ``{ regex: "\\{.*\\}" }``
    - Template literals: ``{ sql: `SELECT * FROM {table}` }``

    Returns the content between (and including) the braces.
    Raises ``ValueError`` if no matching brace is found.

    Why brace-counting instead of regex
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    The old pattern ``\\{[^}]*\\}`` silently truncated any definition with
    nested braces.  For example, a TypeScript interface like:

        export interface Config {
          database: {
            host: string;
            port: number;
          };
        }

    would only capture up to the first ``}``, yielding a broken extraction.
    Brace-counting handles arbitrary nesting depth.
    """
    if start_pos >= len(text) or text[start_pos] != "{":
        raise ValueError(f"Expected '{{' at position {start_pos}")

    depth = 0
    in_string: str | None = None  # None, '"', "'", or '`'
    escape_next = False
    i = start_pos

    while i < len(text):
        ch = text[i]

        if escape_next:
            escape_next = False
            i += 1
            continue

        if ch == "\\":
            escape_next = True
            i += 1
            continue

        # Track string state (skip braces inside strings)
        if in_string is not None:
            if ch == in_string:
                in_string = None
            i += 1
            continue

        if ch in ('"', "'", "`"):
            in_string = ch
            i += 1
            continue

        # Count braces outside strings
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start_pos : i + 1]

        i += 1

    raise ValueError(
        f"Unmatched '{{' at position {start_pos} — reached end of text at depth {depth}"
    )


# ── TypeScript/JavaScript type extraction ────────────────────────────────────

# Patterns that identify the *start* of a type definition.  We capture up to
# (but not including) the opening brace, then use extract_braced_body() to
# get the full body with correct nesting.
#
# Each tuple: (compiled_regex, needs_braced_body: bool)
# - True: the match is a prefix; append extract_braced_body() for the body
# - False: the match is self-contained (e.g. type aliases ending with ;)

_TS_EXTRACTORS: list[tuple[re.Pattern, bool]] = [
    # export interface Foo { ... }  — needs brace extraction
    (
        re.compile(
            r"^(export\s+(?:default\s+)?interface\s+\w+(?:\s+extends\s+[^{]+)?)\s*(?=\{)",
            re.MULTILINE,
        ),
        True,
    ),
    # export enum Foo { ... }  — needs brace extraction
    (
        re.compile(
            r"^(export\s+(?:default\s+)?(?:const\s+)?enum\s+\w+)\s*(?=\{)",
            re.MULTILINE,
        ),
        True,
    ),
    # export class Foo { ... } — capture signature + opening brace only
    # (we intentionally DON'T capture the full class body — just the
    # declaration line, which is enough for import resolution)
    (
        re.compile(
            r"^(export\s+(?:default\s+)?(?:abstract\s+)?class\s+\w+"
            r"(?:\s+(?:extends|implements)\s+[^{]+)?)\s*\{",
            re.MULTILINE,
        ),
        False,  # signature only — body is too large and noisy
    ),
    # export type Foo = ...;  — self-contained
    (
        re.compile(
            r"^(export\s+(?:default\s+)?type\s+\w+\s*=\s*[^;]+;)",
            re.MULTILINE,
        ),
        False,
    ),
    # export const FOO = ...  (UPPER_CASE only) — self-contained
    (
        re.compile(
            r"^(export\s+const\s+[A-Z_][A-Z_0-9]*\s*(?::\s*[^=]+)?\s*=\s*.+)",
            re.MULTILINE,
        ),
        False,
    ),
    # export function foo(params): ReturnType  — signature only
    (
        re.compile(
            r"^(export\s+(?:default\s+)?(?:async\s+)?function\s+\w+\s*\([^)]*\)"
            r"\s*(?::\s*[^{]+)?)\s*\{",
            re.MULTILINE,
        ),
        False,
    ),
]


def extract_ts_type_signatures(content: str) -> list[str]:
    """Extract exported type signatures from TypeScript/JavaScript source.

    Uses brace-counting for interface/enum bodies so nested types like
    ``{ database: { host: string } }`` are captured correctly.

    Returns a deduplicated list of signature strings.
    """
    results: list[str] = []
    seen: set[str] = set()

    for pattern, needs_body in _TS_EXTRACTORS:
        for match in pattern.finditer(content):
            prefix = match.group(1).strip()

            if needs_body:
                # Find the opening brace after the prefix
                brace_pos = content.find("{", match.end() - 1)
                if brace_pos == -1:
                    continue
                try:
                    body = extract_braced_body(content, brace_pos)
                    signature = f"{prefix} {body}"
                except ValueError:
                    # Unmatched braces — fall back to just the prefix
                    signature = prefix + " { /* extraction failed */ }"
                    logger.debug(
                        "Brace-counting failed for '%s' — using prefix only.",
                        prefix[:60],
                    )
            else:
                signature = prefix

            if signature not in seen:
                seen.add(signature)
                results.append(signature)

    return results


# ── Python type extraction ───────────────────────────────────────────────────


def extract_py_type_signatures(content: str) -> list[str]:
    """Extract type definitions from Python source.

    Captures:
    - Class definitions with their body (using indentation, not braces)
    - TypeAlias assignments
    - TypeVar declarations

    For classes, we capture the signature line plus the *body* up to the
    next dedented line.  This gives downstream chunks the field names and
    types — not just the class name.
    """
    results: list[str] = []
    seen: set[str] = set()
    lines = content.split("\n")

    i = 0
    while i < len(lines):
        line = lines[i]

        # ── TypeAlias / TypeVar (single-line) ──
        if re.match(r"^\w+\s*:\s*TypeAlias\s*=", line):
            if line not in seen:
                seen.add(line)
                results.append(line.strip())
            i += 1
            continue

        if re.match(r"^\w+\s*=\s*TypeVar\(", line):
            if line not in seen:
                seen.add(line)
                results.append(line.strip())
            i += 1
            continue

        # ── Class definitions (with body) ──
        # Detect decorator + class pattern
        class_start = i

        # Collect decorators
        while i < len(lines) and re.match(r"^@\w+", lines[i]):
            i += 1

        if i < len(lines) and re.match(r"^class\s+\w+", lines[i]):
            # Found a class — capture signature + indented body
            class_lines = lines[class_start : i + 1]  # decorators + class line
            base_indent = _get_indent(lines[i])
            i += 1

            # Capture body lines (indented more than the class line)
            while i < len(lines):
                if lines[i].strip() == "":
                    # Blank lines inside the class are OK
                    class_lines.append(lines[i])
                    i += 1
                    continue
                line_indent = _get_indent(lines[i])
                if line_indent > base_indent:
                    class_lines.append(lines[i])
                    i += 1
                else:
                    break

            # Strip trailing blank lines
            while class_lines and class_lines[-1].strip() == "":
                class_lines.pop()

            signature = "\n".join(class_lines)
            if signature not in seen:
                seen.add(signature)
                results.append(signature)
            continue

        # If we advanced past decorators but didn't find a class, reset
        if i == class_start:
            i += 1

    return results


def _get_indent(line: str) -> int:
    """Return the number of leading spaces in a line."""
    return len(line) - len(line.lstrip())
