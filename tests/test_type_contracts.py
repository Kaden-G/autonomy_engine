"""Tests for _extract_type_contracts — the cross-chunk type contract extractor.

Verifies that exported types, interfaces, enums, classes, and constants
are correctly extracted from manifest file contents and formatted for
injection into subsequent chunk prompts.
"""

import json


from tasks.implement import _extract_type_contracts


def _make_manifest(*files: tuple[str, str]) -> str:
    """Build a manifest JSON string from (path, content) pairs."""
    return json.dumps({"files": [{"path": p, "content": c} for p, c in files]})


class TestTypeScriptContracts:
    """Extract TypeScript/JavaScript type definitions."""

    def test_extracts_exported_interface(self):
        manifest = _make_manifest(
            (
                "src/types/index.ts",
                "export interface JournalEntry {\n  id: string;\n  title: string;\n  content: string;\n}",
            )
        )
        result = _extract_type_contracts(manifest)
        assert "// From src/types/index.ts:" in result
        assert "export interface JournalEntry" in result
        assert "id: string;" in result

    def test_extracts_exported_type_alias(self):
        manifest = _make_manifest(
            (
                "src/types/index.ts",
                "export type Mood = 'great' | 'good' | 'neutral' | 'bad' | 'terrible';",
            )
        )
        result = _extract_type_contracts(manifest)
        assert "export type Mood" in result
        assert "'great'" in result

    def test_extracts_exported_enum(self):
        manifest = _make_manifest(
            (
                "src/types/index.ts",
                "export enum ThemeId {\n  GRATITUDE = 'gratitude',\n  STOICISM = 'stoicism'\n}",
            )
        )
        result = _extract_type_contracts(manifest)
        assert "export enum ThemeId" in result
        assert "GRATITUDE" in result

    def test_extracts_exported_class_signature(self):
        manifest = _make_manifest(
            (
                "src/utils/errors.ts",
                "export class DatabaseError extends Error {\n  constructor(msg: string) {\n    super(msg);\n  }\n}",
            )
        )
        result = _extract_type_contracts(manifest)
        assert "export class DatabaseError extends Error" in result

    def test_extracts_uppercase_constants(self):
        manifest = _make_manifest(
            (
                "src/constants/index.ts",
                "export const MAX_ENTRIES = 10000;\nexport const API_URL = 'http://localhost';",
            )
        )
        result = _extract_type_contracts(manifest)
        assert "MAX_ENTRIES" in result
        assert "API_URL" in result

    def test_ignores_non_exported_definitions(self):
        manifest = _make_manifest(
            (
                "src/types/internal.ts",
                "interface InternalThing {\n  secret: string;\n}\nconst localVar = 42;",
            )
        )
        result = _extract_type_contracts(manifest)
        # Non-exported items should NOT appear
        assert result == ""

    def test_multiple_files(self):
        manifest = _make_manifest(
            ("src/types/index.ts", "export interface Foo {\n  id: string;\n}"),
            ("src/types/search.ts", "export type SearchQuery = string;"),
        )
        result = _extract_type_contracts(manifest)
        assert "// From src/types/index.ts:" in result
        assert "// From src/types/search.ts:" in result
        assert "export interface Foo" in result
        assert "export type SearchQuery" in result


class TestPythonContracts:
    """Extract Python type definitions."""

    def test_extracts_class_definition(self):
        manifest = _make_manifest(
            ("models.py", "class JournalEntry:\n    id: str\n    title: str\n")
        )
        result = _extract_type_contracts(manifest)
        assert "class JournalEntry:" in result

    def test_extracts_dataclass(self):
        manifest = _make_manifest(
            ("models.py", "@dataclass\nclass Config:\n    debug: bool = False\n")
        )
        result = _extract_type_contracts(manifest)
        assert "@dataclass" in result
        assert "class Config:" in result

    def test_extracts_type_alias(self):
        manifest = _make_manifest(("types.py", "EntryId: TypeAlias = str\n"))
        result = _extract_type_contracts(manifest)
        assert "EntryId: TypeAlias = str" in result


class TestFiltering:
    """Verify non-code files are skipped and budget limits work."""

    def test_skips_non_code_files(self):
        manifest = _make_manifest(
            ("README.md", "# Title\nexport interface Fake {}"),
            ("config.json", '{"key": "value"}'),
            (".gitignore", "node_modules/"),
        )
        result = _extract_type_contracts(manifest)
        assert result == ""

    def test_handles_tsx_jsx_extensions(self):
        manifest = _make_manifest(
            ("App.tsx", "export interface AppProps {\n  name: string;\n}"),
            ("Widget.jsx", "export type WidgetSize = 'sm' | 'md' | 'lg';"),
        )
        result = _extract_type_contracts(manifest)
        assert "AppProps" in result
        assert "WidgetSize" in result

    def test_empty_manifest_returns_empty_string(self):
        manifest = json.dumps({"files": []})
        result = _extract_type_contracts(manifest)
        assert result == ""

    def test_no_types_returns_empty_string(self):
        manifest = _make_manifest(("src/main.ts", "console.log('hello world');\nconst x = 42;\n"))
        result = _extract_type_contracts(manifest)
        assert result == ""
