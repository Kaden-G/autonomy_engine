"""Tests for engine.extraction — robust JSON and type extraction utilities.

These tests verify the brace-counting and JSON-scanning approaches that
replaced fragile regex patterns.  The key improvement is that nested
structures (nested JSON objects, nested TypeScript interfaces, Python
class bodies) are now extracted correctly.
"""

import json

import pytest

from engine.extraction import (
    extract_braced_body,
    extract_json_block,
    extract_py_type_signatures,
    extract_ts_type_signatures,
    _find_fenced_blocks,
)


# ── JSON block scanner ───────────────────────────────────────────────────────


class TestExtractJsonBlock:
    """Tests for the JSON-block scanner that replaced the fragile regex fallback."""

    def test_finds_block_with_required_key(self):
        text = '# Heading\n\n```json\n{"components": [1, 2]}\n```\n\nMore text.'
        result = extract_json_block(text, "components")
        assert result is not None
        data = json.loads(result)
        assert "components" in data

    def test_returns_none_when_key_missing(self):
        text = '```json\n{"files": [1]}\n```'
        result = extract_json_block(text, "components")
        assert result is None

    def test_skips_malformed_json(self):
        """The first block is broken JSON; the second is valid."""
        text = (
            '```json\n{broken json\n```\n\n'
            '```json\n{"components": ["a"]}\n```'
        )
        result = extract_json_block(text, "components")
        assert result is not None
        assert json.loads(result)["components"] == ["a"]

    def test_handles_nested_objects(self):
        """The key improvement: nested objects don't break extraction."""
        nested = {
            "components": [
                {"name": "Core", "config": {"nested": {"deep": True}}}
            ]
        }
        text = f"```json\n{json.dumps(nested, indent=2)}\n```"
        result = extract_json_block(text, "components")
        assert result is not None
        parsed = json.loads(result)
        assert parsed["components"][0]["config"]["nested"]["deep"] is True

    def test_returns_first_qualifying_block(self):
        """When multiple blocks have the key, returns the first."""
        text = (
            '```json\n{"components": ["first"]}\n```\n'
            '```json\n{"components": ["second"]}\n```'
        )
        result = extract_json_block(text, "components")
        assert json.loads(result)["components"] == ["first"]

    def test_ignores_non_json_fences(self):
        """Only processes ```json blocks, not ```python or plain ```."""
        text = (
            '```python\n{"components": [1]}\n```\n'
            '```json\n{"files": [1]}\n```'
        )
        result = extract_json_block(text, "components")
        assert result is None  # The python block should be ignored

    def test_no_blocks_returns_none(self):
        text = "Just plain text with no code blocks."
        assert extract_json_block(text, "anything") is None

    def test_ignores_json_lists(self):
        """A JSON array (not object) should be skipped."""
        text = '```json\n[1, 2, 3]\n```'
        assert extract_json_block(text, "components") is None


class TestFindFencedBlocks:
    def test_finds_multiple_blocks(self):
        text = '```json\n{"a": 1}\n```\ntext\n```json\n{"b": 2}\n```'
        blocks = _find_fenced_blocks(text, "json")
        assert len(blocks) == 2

    def test_handles_space_after_backticks(self):
        text = '``` json\n{"a": 1}\n```'
        blocks = _find_fenced_blocks(text, "json")
        assert len(blocks) == 1

    def test_ignores_other_languages(self):
        text = '```python\nprint("hi")\n```\n```json\n{"a": 1}\n```'
        blocks = _find_fenced_blocks(text, "json")
        assert len(blocks) == 1


# ── Brace counting ───────────────────────────────────────────────────────────


class TestExtractBracedBody:
    def test_simple_braces(self):
        text = "prefix { hello } suffix"
        result = extract_braced_body(text, 7)
        assert result == "{ hello }"

    def test_nested_braces(self):
        """The main fix: nested braces are handled correctly."""
        text = "{ outer: { inner: true } }"
        result = extract_braced_body(text, 0)
        assert result == "{ outer: { inner: true } }"

    def test_deeply_nested(self):
        text = "{ a: { b: { c: { d: 1 } } } }"
        result = extract_braced_body(text, 0)
        assert result == text

    def test_braces_in_strings_ignored(self):
        """Braces inside string literals should not affect counting."""
        text = '{ key: "value with { and }" }'
        result = extract_braced_body(text, 0)
        assert result == text

    def test_braces_in_template_literals(self):
        text = "{ sql: `SELECT * FROM {table}` }"
        result = extract_braced_body(text, 0)
        assert result == text

    def test_escaped_quotes(self):
        text = r'{ key: "escaped \" brace {" }'
        result = extract_braced_body(text, 0)
        assert result == text

    def test_raises_on_unmatched(self):
        text = "{ open but never closed"
        with pytest.raises(ValueError, match="Unmatched"):
            extract_braced_body(text, 0)

    def test_raises_on_bad_start(self):
        text = "no brace here"
        with pytest.raises(ValueError, match="Expected"):
            extract_braced_body(text, 0)

    def test_multiline_body(self):
        text = "export interface Foo {\n  id: string;\n  config: {\n    host: string;\n  };\n}"
        result = extract_braced_body(text, text.index("{"))
        assert result.endswith("}")
        assert "config" in result
        assert "host" in result


# ── TypeScript type extraction ───────────────────────────────────────────────


class TestExtractTsTypeSignatures:
    def test_simple_interface(self):
        code = "export interface User {\n  id: string;\n  name: string;\n}"
        sigs = extract_ts_type_signatures(code)
        assert len(sigs) == 1
        assert "User" in sigs[0]
        assert "id: string" in sigs[0]

    def test_nested_interface(self):
        """Key improvement: nested objects are now captured fully."""
        code = (
            "export interface Config {\n"
            "  database: {\n"
            "    host: string;\n"
            "    port: number;\n"
            "  };\n"
            "  cache: {\n"
            "    ttl: number;\n"
            "  };\n"
            "}"
        )
        sigs = extract_ts_type_signatures(code)
        assert len(sigs) == 1
        assert "database" in sigs[0]
        assert "port: number" in sigs[0]
        assert "cache" in sigs[0]

    def test_interface_with_extends(self):
        code = "export interface Admin extends User {\n  role: string;\n}"
        sigs = extract_ts_type_signatures(code)
        assert len(sigs) == 1
        assert "extends User" in sigs[0]

    def test_type_alias(self):
        code = "export type Status = 'active' | 'inactive' | 'pending';"
        sigs = extract_ts_type_signatures(code)
        assert len(sigs) == 1
        assert "Status" in sigs[0]

    def test_enum(self):
        code = "export enum Color {\n  Red = 'red',\n  Blue = 'blue',\n}"
        sigs = extract_ts_type_signatures(code)
        assert len(sigs) == 1
        assert "Red" in sigs[0]

    def test_const_enum(self):
        code = "export const enum Direction {\n  Up,\n  Down,\n}"
        sigs = extract_ts_type_signatures(code)
        assert len(sigs) == 1
        assert "Direction" in sigs[0]

    def test_class_signature_only(self):
        """Classes capture just the signature, not the full body."""
        code = (
            "export class UserService extends BaseService {\n"
            "  private db: Database;\n"
            "  constructor() { super(); }\n"
            "  getUser(id: string): User { return this.db.get(id); }\n"
            "}"
        )
        sigs = extract_ts_type_signatures(code)
        assert len(sigs) == 1
        assert "UserService" in sigs[0]
        assert "extends BaseService" in sigs[0]
        # Should NOT contain the full body
        assert "getUser" not in sigs[0]

    def test_uppercase_const(self):
        code = "export const MAX_RETRIES: number = 3;"
        sigs = extract_ts_type_signatures(code)
        assert len(sigs) == 1
        assert "MAX_RETRIES" in sigs[0]

    def test_ignores_lowercase_const(self):
        """Only UPPER_CASE constants are extracted (configs, enums-as-objects)."""
        code = "export const myHelper = () => {};"
        sigs = extract_ts_type_signatures(code)
        assert len(sigs) == 0

    def test_function_signature(self):
        code = "export function processData(input: string[]): Result {\n  return {};\n}"
        sigs = extract_ts_type_signatures(code)
        assert len(sigs) == 1
        assert "processData" in sigs[0]
        assert "Result" in sigs[0]

    def test_async_function(self):
        code = "export async function fetchUser(id: string): Promise<User> {\n  return {};\n}"
        sigs = extract_ts_type_signatures(code)
        assert len(sigs) == 1
        assert "async" in sigs[0]
        assert "Promise<User>" in sigs[0]

    def test_deduplication(self):
        """Duplicate definitions (e.g. re-exported) should be deduped."""
        code = (
            "export interface Foo { id: string; }\n"
            "export interface Foo { id: string; }\n"
        )
        sigs = extract_ts_type_signatures(code)
        assert len(sigs) == 1

    def test_multiple_types_in_one_file(self):
        code = (
            "export interface User { id: string; }\n\n"
            "export type Role = 'admin' | 'user';\n\n"
            "export enum Status { Active, Inactive }\n"
        )
        sigs = extract_ts_type_signatures(code)
        assert len(sigs) == 3


# ── Python type extraction ───────────────────────────────────────────────────


class TestExtractPyTypeSignatures:
    def test_simple_class(self):
        code = "class User:\n    id: str\n    name: str\n"
        sigs = extract_py_type_signatures(code)
        assert len(sigs) == 1
        assert "class User:" in sigs[0]
        assert "id: str" in sigs[0]
        assert "name: str" in sigs[0]

    def test_dataclass(self):
        code = "@dataclass\nclass Config:\n    host: str\n    port: int = 5432\n"
        sigs = extract_py_type_signatures(code)
        assert len(sigs) == 1
        assert "@dataclass" in sigs[0]
        assert "port: int" in sigs[0]

    def test_class_with_inheritance(self):
        code = "class Admin(User):\n    role: str\n    permissions: list[str]\n"
        sigs = extract_py_type_signatures(code)
        assert len(sigs) == 1
        assert "Admin(User)" in sigs[0]

    def test_class_with_methods(self):
        """Methods in the body are captured (useful for knowing the API surface)."""
        code = (
            "class Service:\n"
            "    def __init__(self, db: Database):\n"
            "        self.db = db\n"
            "\n"
            "    def get_user(self, id: str) -> User:\n"
            "        return self.db.find(id)\n"
        )
        sigs = extract_py_type_signatures(code)
        assert len(sigs) == 1
        assert "get_user" in sigs[0]

    def test_type_alias(self):
        code = "UserId: TypeAlias = str\n"
        sigs = extract_py_type_signatures(code)
        assert len(sigs) == 1
        assert "UserId" in sigs[0]

    def test_typevar(self):
        code = "T = TypeVar('T', bound=Comparable)\n"
        sigs = extract_py_type_signatures(code)
        assert len(sigs) == 1
        assert "TypeVar" in sigs[0]

    def test_multiple_classes(self):
        code = (
            "class User:\n    id: str\n\n"
            "class Admin:\n    role: str\n"
        )
        sigs = extract_py_type_signatures(code)
        assert len(sigs) == 2

    def test_class_body_ends_at_dedent(self):
        """Class body extraction stops when indentation returns to base level."""
        code = (
            "class Foo:\n"
            "    x: int\n"
            "\n"
            "# This should NOT be part of Foo\n"
            "some_variable = 42\n"
        )
        sigs = extract_py_type_signatures(code)
        assert len(sigs) == 1
        assert "some_variable" not in sigs[0]

    def test_class_with_decorator_args(self):
        code = '@dataclass(frozen=True)\nclass Point:\n    x: float\n    y: float\n'
        sigs = extract_py_type_signatures(code)
        assert len(sigs) == 1
        assert "@dataclass(frozen=True)" in sigs[0]
