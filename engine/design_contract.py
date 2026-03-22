"""Design contract — the binding blueprint between the design and implementation stages.

When the AI designs a system, it produces two things: a human-readable architecture
document (ARCHITECTURE.md) and a machine-readable contract (DESIGN_CONTRACT.json).
The contract is the authoritative spec — if the prose and the contract disagree,
the contract wins.

Think of it like an engineering drawing with tolerances: the AI can choose *how* to
implement the logic inside each file, but the contract dictates *which* files exist,
*what* data types they define, and *how* components connect to each other.

The contract lifecycle:
    1. Extracted from the AI's design output (embedded JSON block)
    2. Validated with 15+ automated checks (duplicates, phantom dependencies, etc.)
    3. Saved as ``state/designs/DESIGN_CONTRACT.json``
    4. Fed to the implementation stage, which uses it to know exactly what to build
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from engine.extraction import extract_json_block

logger = logging.getLogger(__name__)

# ── Marker comments the LLM must use to delimit the contract JSON ────────────
CONTRACT_START = "<!-- DESIGN_CONTRACT_START -->"
CONTRACT_END = "<!-- DESIGN_CONTRACT_END -->"


# ── Data classes (lightweight — no Pydantic dependency required) ─────────────


@dataclass
class TypeDefinition:
    """A single type/interface/enum in the canonical schema."""

    name: str
    kind: str  # "interface" | "type" | "enum" | "class" | "dataclass"
    fields: dict[str, str]  # field_name → type_string (e.g. "id" → "string")
    file_path: str  # where this type lives (e.g. "src/types/index.ts")

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "fields": self.fields,
            "file_path": self.file_path,
        }

    @classmethod
    def from_dict(cls, d: dict) -> TypeDefinition:
        return cls(
            name=d["name"],
            kind=d["kind"],
            fields=d["fields"],
            file_path=d["file_path"],
        )


@dataclass
class ComponentContract:
    """Contract for a single implementation component/chunk."""

    name: str
    description: str
    files: list[str]  # exact file paths this component MUST produce
    imports_from: list[str]  # component names this one depends on
    exports_types: list[str]  # type names this component defines (if any)
    max_files: int  # hard ceiling for file count

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "files": self.files,
            "imports_from": self.imports_from,
            "exports_types": self.exports_types,
            "max_files": self.max_files,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ComponentContract:
        return cls(
            name=d["name"],
            description=d["description"],
            files=d["files"],
            imports_from=d.get("imports_from", []),
            exports_types=d.get("exports_types", []),
            max_files=d.get("max_files", 10),
        )


@dataclass
class TechDecision:
    """A locked technology choice — not a suggestion, a mandate."""

    category: str  # e.g. "framework", "database", "styling", "bundler"
    choice: str  # e.g. "React 18", "IndexedDB via Dexie", "Tailwind CSS"
    rationale: str  # one-line why

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "choice": self.choice,
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, d: dict) -> TechDecision:
        return cls(
            category=d["category"],
            choice=d["choice"],
            rationale=d.get("rationale", ""),
        )


@dataclass
class DesignContract:
    """The full design contract — structured handoff from Design to Implement."""

    project_name: str
    language: str  # "typescript" | "python" | "javascript" etc.
    components: list[ComponentContract]
    canonical_types: list[TypeDefinition]
    tech_decisions: list[TechDecision]
    entry_point: str  # e.g. "src/main.tsx" or "src/main.py"
    total_file_budget: int  # hard ceiling across all components

    def to_dict(self) -> dict:
        return {
            "project_name": self.project_name,
            "language": self.language,
            "components": [c.to_dict() for c in self.components],
            "canonical_types": [t.to_dict() for t in self.canonical_types],
            "tech_decisions": [d.to_dict() for d in self.tech_decisions],
            "entry_point": self.entry_point,
            "total_file_budget": self.total_file_budget,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, d: dict) -> DesignContract:
        return cls(
            project_name=d["project_name"],
            language=d["language"],
            components=[ComponentContract.from_dict(c) for c in d["components"]],
            canonical_types=[TypeDefinition.from_dict(t) for t in d["canonical_types"]],
            tech_decisions=[TechDecision.from_dict(t) for t in d.get("tech_decisions", [])],
            entry_point=d.get("entry_point", ""),
            total_file_budget=d.get("total_file_budget", 80),
        )

    @classmethod
    def from_json(cls, json_str: str) -> DesignContract:
        return cls.from_dict(json.loads(json_str))


# ── Validation ──────────────────────────────────────────────────────────────


class ContractValidationError(ValueError):
    """Raised when a design contract fails validation."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(
            f"Design contract has {len(errors)} validation error(s):\n"
            + "\n".join(f"  - {e}" for e in errors)
        )


def validate_contract(contract: DesignContract) -> list[str]:
    """Validate a design contract. Returns a list of error strings (empty = valid)."""
    errors: list[str] = []

    # ── Basic fields ──
    if not contract.project_name.strip():
        errors.append("project_name is empty")
    if not contract.language.strip():
        errors.append("language is empty")
    if not contract.components:
        errors.append("components list is empty — need at least 1 component")

    # ── Component validation ──
    comp_names = set()
    all_planned_files: list[str] = []
    for comp in contract.components:
        if not comp.name.strip():
            errors.append("A component has an empty name")
            continue

        if comp.name in comp_names:
            errors.append(f"Duplicate component name: '{comp.name}'")
        comp_names.add(comp.name)

        if not comp.files:
            errors.append(f"Component '{comp.name}' has no files listed")

        if len(comp.files) > comp.max_files:
            errors.append(
                f"Component '{comp.name}' has {len(comp.files)} files "
                f"but max_files is {comp.max_files}"
            )

        # Check for duplicate files within a component
        seen_files = set()
        for f in comp.files:
            if f in seen_files:
                errors.append(f"Component '{comp.name}' lists file '{f}' twice")
            seen_files.add(f)

        all_planned_files.extend(comp.files)

        # Validate imports_from references
        for dep in comp.imports_from:
            if dep not in [c.name for c in contract.components]:
                errors.append(
                    f"Component '{comp.name}' imports from '{dep}' which is not a known component"
                )

    # ── Cross-file uniqueness ──
    file_counts: dict[str, int] = {}
    for f in all_planned_files:
        file_counts[f] = file_counts.get(f, 0) + 1
    for f, count in file_counts.items():
        if count > 1:
            owners = [c.name for c in contract.components if f in c.files]
            errors.append(
                f"File '{f}' is assigned to {count} components: {owners}. "
                f"Each file must belong to exactly one component."
            )

    # ── Total file budget ──
    total_files = len(set(all_planned_files))
    if total_files > contract.total_file_budget:
        errors.append(
            f"Total planned files ({total_files}) exceeds budget ({contract.total_file_budget})"
        )

    # ── Canonical types ──
    type_names = set()
    for td in contract.canonical_types:
        if not td.name.strip():
            errors.append("A canonical type has an empty name")
            continue
        if td.name in type_names:
            errors.append(f"Duplicate canonical type: '{td.name}'")
        type_names.add(td.name)
        if not td.fields:
            errors.append(f"Canonical type '{td.name}' has no fields")
        if not td.file_path.strip():
            errors.append(f"Canonical type '{td.name}' has no file_path")

    # ── Component exports_types reference valid canonical types ──
    for comp in contract.components:
        for type_name in comp.exports_types:
            if type_name not in type_names:
                errors.append(
                    f"Component '{comp.name}' claims to export type '{type_name}' "
                    f"which is not in canonical_types"
                )

    return errors


# ── Extraction from LLM output ──────────────────────────────────────────────


def extract_contract(architecture_text: str) -> DesignContract:
    """Extract the DESIGN_CONTRACT JSON block from the architecture document.

    Looks for the JSON between CONTRACT_START and CONTRACT_END markers.
    Falls back to searching for a JSON code block with a "components" key.

    Raises RuntimeError if no valid contract is found.
    """
    start_idx = architecture_text.find(CONTRACT_START)
    end_idx = architecture_text.find(CONTRACT_END)

    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        raw = architecture_text[start_idx + len(CONTRACT_START) : end_idx].strip()
    else:
        # Fallback: scan all ```json blocks for one containing "components".
        # This replaces a fragile regex that broke on nested JSON objects
        # because lazy matching (.*?) would stop at the first closing brace.
        block = extract_json_block(architecture_text, required_key="components")
        if block is not None:
            raw = block
        else:
            raise RuntimeError(
                "Design output is missing the DESIGN_CONTRACT block. "
                "Expected markers: "
                f"{CONTRACT_START} ... {CONTRACT_END}"
            )

    # Strip optional json fences
    raw = raw.strip()
    if raw.startswith("```"):
        first_nl = raw.index("\n")
        raw = raw[first_nl + 1 :]
    if raw.endswith("```"):
        raw = raw[: raw.rfind("```")].rstrip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"DESIGN_CONTRACT JSON is malformed: {exc}") from exc

    contract = DesignContract.from_dict(data)

    # Validate
    errors = validate_contract(contract)
    if errors:
        raise ContractValidationError(errors)

    logger.info(
        "Design contract validated: %d components, %d types, %d tech decisions, "
        "%d total planned files (budget: %d).",
        len(contract.components),
        len(contract.canonical_types),
        len(contract.tech_decisions),
        sum(len(c.files) for c in contract.components),
        contract.total_file_budget,
    )

    return contract
