"""Tests for design contract schema, validation, and extraction."""

import json

import pytest

from engine.design_contract import (
    CONTRACT_END,
    CONTRACT_START,
    ComponentContract,
    ContractValidationError,
    DesignContract,
    TechDecision,
    TypeDefinition,
    extract_contract,
    validate_contract,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _minimal_contract(**overrides) -> DesignContract:
    """Build a minimal valid contract with optional overrides."""
    defaults = dict(
        project_name="test-app",
        language="typescript",
        entry_point="src/main.tsx",
        total_file_budget=40,
        components=[
            ComponentContract(
                name="Core",
                description="Core types and config",
                files=["src/types/index.ts", "src/config.ts"],
                imports_from=[],
                exports_types=["User"],
                max_files=5,
            ),
        ],
        canonical_types=[
            TypeDefinition(
                name="User",
                kind="interface",
                fields={"id": "string", "email": "string"},
                file_path="src/types/index.ts",
            ),
        ],
        tech_decisions=[
            TechDecision(
                category="framework",
                choice="React 18",
                rationale="SPA requirement",
            ),
        ],
    )
    defaults.update(overrides)
    return DesignContract(**defaults)


def _contract_json(contract: DesignContract) -> str:
    return contract.to_json()


def _wrap_in_markers(json_str: str) -> str:
    return f"{CONTRACT_START}\n```json\n{json_str}\n```\n{CONTRACT_END}"


# ── Serialization ────────────────────────────────────────────────────────────

class TestSerialization:
    def test_roundtrip(self):
        c = _minimal_contract()
        j = c.to_json()
        c2 = DesignContract.from_json(j)
        assert c2.project_name == c.project_name
        assert len(c2.components) == len(c.components)
        assert c2.components[0].name == c.components[0].name
        assert c2.canonical_types[0].fields == c.canonical_types[0].fields

    def test_to_dict_structure(self):
        c = _minimal_contract()
        d = c.to_dict()
        assert "project_name" in d
        assert "components" in d
        assert isinstance(d["components"], list)
        assert d["components"][0]["name"] == "Core"

    def test_from_dict_defaults(self):
        """Missing optional fields get defaults."""
        raw = {
            "project_name": "test",
            "language": "python",
            "components": [{
                "name": "Main",
                "description": "The main module",
                "files": ["main.py"],
            }],
            "canonical_types": [{
                "name": "Item",
                "kind": "dataclass",
                "fields": {"id": "str"},
                "file_path": "types.py",
            }],
        }
        c = DesignContract.from_dict(raw)
        assert c.entry_point == ""
        assert c.total_file_budget == 80
        assert c.components[0].max_files == 10
        assert c.components[0].imports_from == []


# ── Validation ───────────────────────────────────────────────────────────────

class TestValidation:
    def test_valid_contract_passes(self):
        c = _minimal_contract()
        errors = validate_contract(c)
        assert errors == []

    def test_empty_project_name(self):
        c = _minimal_contract(project_name="")
        errors = validate_contract(c)
        assert any("project_name" in e for e in errors)

    def test_empty_language(self):
        c = _minimal_contract(language="  ")
        errors = validate_contract(c)
        assert any("language" in e for e in errors)

    def test_no_components(self):
        c = _minimal_contract(components=[])
        errors = validate_contract(c)
        assert any("components" in e for e in errors)

    def test_duplicate_component_names(self):
        comp = ComponentContract("A", "desc", ["a.ts"], [], [], 5)
        c = _minimal_contract(components=[comp, comp])
        errors = validate_contract(c)
        assert any("Duplicate component" in e for e in errors)

    def test_component_no_files(self):
        comp = ComponentContract("Empty", "desc", [], [], [], 5)
        c = _minimal_contract(components=[comp])
        errors = validate_contract(c)
        assert any("no files" in e for e in errors)

    def test_component_exceeds_max_files(self):
        comp = ComponentContract(
            "Big", "desc",
            ["a.ts", "b.ts", "c.ts"],
            [], [], max_files=2,
        )
        c = _minimal_contract(components=[comp])
        errors = validate_contract(c)
        assert any("3 files" in e and "max_files is 2" in e for e in errors)

    def test_cross_component_duplicate_files(self):
        comp1 = ComponentContract("A", "desc", ["shared.ts"], [], [], 5)
        comp2 = ComponentContract("B", "desc", ["shared.ts"], [], [], 5)
        c = _minimal_contract(components=[comp1, comp2])
        errors = validate_contract(c)
        assert any("shared.ts" in e and "2 components" in e for e in errors)

    def test_imports_from_unknown_component(self):
        comp = ComponentContract("A", "desc", ["a.ts"], ["NonExistent"], [], 5)
        c = _minimal_contract(components=[comp])
        errors = validate_contract(c)
        assert any("NonExistent" in e for e in errors)

    def test_total_file_budget_exceeded(self):
        comp = ComponentContract(
            "Huge", "desc",
            [f"file{i}.ts" for i in range(50)],
            [], [], max_files=50,
        )
        c = _minimal_contract(components=[comp], total_file_budget=10)
        errors = validate_contract(c)
        assert any("exceeds budget" in e for e in errors)

    def test_duplicate_canonical_type_names(self):
        t1 = TypeDefinition("User", "interface", {"id": "string"}, "types.ts")
        t2 = TypeDefinition("User", "interface", {"name": "string"}, "types.ts")
        c = _minimal_contract(canonical_types=[t1, t2])
        errors = validate_contract(c)
        assert any("Duplicate canonical type" in e for e in errors)

    def test_canonical_type_no_fields(self):
        t = TypeDefinition("Empty", "interface", {}, "types.ts")
        c = _minimal_contract(canonical_types=[t])
        errors = validate_contract(c)
        assert any("no fields" in e for e in errors)

    def test_exports_types_references_valid_canonical(self):
        """exports_types referencing a non-existent canonical type is an error."""
        comp = ComponentContract("A", "desc", ["a.ts"], [], ["Ghost"], 5)
        c = _minimal_contract(components=[comp])
        errors = validate_contract(c)
        assert any("Ghost" in e for e in errors)


# ── Extraction ───────────────────────────────────────────────────────────────

class TestExtraction:
    def test_extracts_from_markers(self):
        c = _minimal_contract()
        arch = f"# Architecture\n\nSome prose.\n\n{_wrap_in_markers(_contract_json(c))}"
        extracted = extract_contract(arch)
        assert extracted.project_name == "test-app"
        assert len(extracted.components) == 1

    def test_extracts_from_json_codeblock_fallback(self):
        """When markers are missing, falls back to JSON code block with 'components'."""
        c = _minimal_contract()
        arch = f"# Architecture\n\n```json\n{_contract_json(c)}\n```"
        extracted = extract_contract(arch)
        assert extracted.project_name == "test-app"

    def test_raises_on_no_contract(self):
        arch = "# Architecture\n\nJust prose, no contract."
        with pytest.raises(RuntimeError, match="DESIGN_CONTRACT"):
            extract_contract(arch)

    def test_raises_on_malformed_json(self):
        arch = f"{CONTRACT_START}\n{{not valid json}}\n{CONTRACT_END}"
        with pytest.raises(RuntimeError, match="malformed"):
            extract_contract(arch)

    def test_raises_on_validation_errors(self):
        bad = {"project_name": "", "language": "", "components": [], "canonical_types": []}
        arch = _wrap_in_markers(json.dumps(bad))
        with pytest.raises(ContractValidationError):
            extract_contract(arch)
