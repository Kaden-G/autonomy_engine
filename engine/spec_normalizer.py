"""Spec normalizer — parse the project spec YAML into structured, unambiguous fields.

The raw ``project_spec.yml`` often contains ambiguous language like
"React (or Next.js)" or "SQLite or IndexedDB depending on deployment."
The design LLM shouldn't have to guess which one — these should be locked
decisions before the architecture stage begins.

This module:
    1. Parses the spec YAML.
    2. Extracts structured fields (features, tech stack, priorities).
    3. Detects ambiguous tech choices (e.g. "X or Y") and flags them.
    4. Produces a ``NormalizedSpec`` that downstream stages can use without
       interpretation.

If the spec contains ambiguous choices, the pipeline can either:
    - Ask the user to resolve them (via decision gate).
    - Let the design LLM choose and lock the decision in the contract.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


@dataclass
class Feature:
    """A single functional requirement, with priority."""
    description: str
    priority: str = "required"  # "required" | "nice_to_have" | "excluded"

    def to_dict(self) -> dict:
        return {"description": self.description, "priority": self.priority}


@dataclass
class TechChoice:
    """A technology choice — either locked or ambiguous."""
    category: str       # e.g. "frontend", "storage", "backend"
    raw: str            # original text from spec
    locked: str | None = None      # resolved choice (if locked)
    alternatives: list[str] = field(default_factory=list)  # if ambiguous
    is_ambiguous: bool = False

    def to_dict(self) -> dict:
        d: dict = {"category": self.category, "raw": self.raw}
        if self.locked:
            d["locked"] = self.locked
        if self.alternatives:
            d["alternatives"] = self.alternatives
        d["is_ambiguous"] = self.is_ambiguous
        return d


@dataclass
class NormalizedSpec:
    """The project spec, parsed into unambiguous structured fields."""
    project_name: str
    project_description: str
    features: list[Feature]
    non_functional: list[str]
    tech_stack: list[TechChoice]
    non_goals: list[str]
    acceptance_criteria: list[str]
    performance_constraints: list[str] = field(default_factory=list)
    security_constraints: list[str] = field(default_factory=list)
    ambiguities: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "project_name": self.project_name,
            "project_description": self.project_description,
            "features": [f.to_dict() for f in self.features],
            "non_functional": self.non_functional,
            "tech_stack": [t.to_dict() for t in self.tech_stack],
            "non_goals": self.non_goals,
            "acceptance_criteria": self.acceptance_criteria,
            "performance_constraints": self.performance_constraints,
            "security_constraints": self.security_constraints,
            "ambiguities": self.ambiguities,
        }

    def to_design_context(self) -> str:
        """Format the normalized spec as structured context for the design prompt.

        This replaces the raw prose with unambiguous, prioritized fields.
        """
        lines = []

        # ── Features with priorities ──
        lines.append("## Features (by priority)")
        required = [f for f in self.features if f.priority == "required"]
        nice = [f for f in self.features if f.priority == "nice_to_have"]
        if required:
            lines.append("\n**Required (must implement):**")
            for f in required:
                lines.append(f"- {f.description}")
        if nice:
            lines.append("\n**Nice to have (implement only if budget allows):**")
            for f in nice:
                lines.append(f"- {f.description}")

        # ── Technology decisions ──
        locked = [t for t in self.tech_stack if t.locked]
        ambiguous = [t for t in self.tech_stack if t.is_ambiguous]

        if locked:
            lines.append("\n## Locked Technology Decisions")
            for t in locked:
                lines.append(f"- **{t.category}**: {t.locked}")

        if ambiguous:
            lines.append("\n## Ambiguous Technology Choices (YOU must resolve these)")
            for t in ambiguous:
                lines.append(
                    f"- **{t.category}**: Choose ONE of: "
                    + " | ".join(t.alternatives)
                    + f" (original: \"{t.raw}\")"
                )

        # ── Constraints ──
        if self.performance_constraints:
            lines.append("\n## Performance Constraints")
            for c in self.performance_constraints:
                lines.append(f"- {c}")

        if self.security_constraints:
            lines.append("\n## Security Constraints")
            for c in self.security_constraints:
                lines.append(f"- {c}")

        return "\n".join(lines)


# ── Ambiguity detection ─────────────────────────────────────────────────────

# Patterns that indicate "X or Y" choices
_OR_PATTERNS = [
    re.compile(r"\b(\w[\w.]*)\s+(?:\()?or\s+(\w[\w.]*)\b", re.IGNORECASE),
    re.compile(r"\b(\w[\w.]*)\s*/\s*(\w[\w.]*)\b"),  # "X / Y"
]


def _detect_ambiguity(text: str) -> tuple[bool, list[str]]:
    """Check if a tech stack entry contains ambiguous 'or' choices.

    Returns (is_ambiguous, alternatives).
    """
    alternatives = []
    for pattern in _OR_PATTERNS:
        match = pattern.search(text)
        if match:
            alternatives.extend([match.group(1).strip(), match.group(2).strip()])

    # Deduplicate
    alternatives = list(dict.fromkeys(alternatives))
    return len(alternatives) >= 2, alternatives


# ── Main normalization ──────────────────────────────────────────────────────

def normalize_spec(spec_path: Path) -> NormalizedSpec:
    """Parse the project_spec.yml and produce a NormalizedSpec.

    Args:
        spec_path: Path to the project_spec.yml file.

    Returns:
        A NormalizedSpec with structured, prioritized fields and
        flagged ambiguities.
    """
    with open(spec_path) as f:
        raw = yaml.safe_load(f)

    project = raw.get("project", {})
    requirements = raw.get("requirements", {})
    constraints = raw.get("constraints", {})

    # ── Features ──
    features = []
    for desc in requirements.get("functional", []):
        # Heuristic: if description contains "optional" or "nice to have", lower priority
        priority = "required"
        lower = desc.lower()
        if "optional" in lower or "nice to have" in lower or "nice-to-have" in lower:
            priority = "nice_to_have"
        features.append(Feature(description=desc, priority=priority))

    # ── Tech stack ──
    tech_choices = []
    ambiguities = []
    for entry in constraints.get("tech_stack", []):
        # Try to extract category from "Category: value" format
        if ":" in entry:
            cat, value = entry.split(":", 1)
            cat = cat.strip().lower()
            value = value.strip()
        else:
            cat = "general"
            value = entry.strip()

        is_ambiguous, alternatives = _detect_ambiguity(value)
        tc = TechChoice(
            category=cat,
            raw=entry,
            locked=value if not is_ambiguous else None,
            alternatives=alternatives if is_ambiguous else [],
            is_ambiguous=is_ambiguous,
        )
        tech_choices.append(tc)
        if is_ambiguous:
            ambiguities.append(
                f"Tech choice for '{cat}' is ambiguous: \"{entry}\" "
                f"(alternatives: {alternatives})"
            )

    # ── Performance + security constraints (split from constraints) ──
    perf_raw = constraints.get("performance", "")
    perf_list = [s.strip() for s in perf_raw.split(".") if s.strip()] if perf_raw else []

    sec_raw = constraints.get("security", "")
    sec_list = [s.strip() for s in sec_raw.split(".") if s.strip()] if sec_raw else []

    spec = NormalizedSpec(
        project_name=project.get("name", "unnamed"),
        project_description=project.get("description", ""),
        features=features,
        non_functional=requirements.get("non_functional", []),
        tech_stack=tech_choices,
        non_goals=raw.get("non_goals", []),
        acceptance_criteria=raw.get("acceptance_criteria", []),
        performance_constraints=perf_list,
        security_constraints=sec_list,
        ambiguities=ambiguities,
    )

    if ambiguities:
        logger.warning(
            "Spec has %d ambiguous tech choice(s):\n%s",
            len(ambiguities),
            "\n".join(f"  - {a}" for a in ambiguities),
        )
    else:
        logger.info(
            "Spec normalized: %d features (%d required), %d tech choices (all locked).",
            len(features),
            sum(1 for f in features if f.priority == "required"),
            len(tech_choices),
        )

    return spec
