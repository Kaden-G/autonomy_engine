"""Project spec schema — the single authoritative contract for every engine run.

Fields in this schema are either:
- consumed by engine tasks (design, implement, test, verify, extract), or
- rendered into state/inputs/ documents that feed LLM prompts.

Runtime settings (LLM provider, sandbox, notifications) live in ``config.yml``,
not here.  This schema captures *what to build*, not *how to run the engine*.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, model_validator


class Domain(str, Enum):
    SOFTWARE = "software"
    DATA = "data"
    ML = "ml"
    INFRA = "infra"


class ProjectInfo(BaseModel):
    name: str = Field(min_length=1, description="Project name")
    description: str = Field(min_length=10, description="What the project does")
    domain: Domain


class Requirements(BaseModel):
    functional: list[str] = Field(min_length=1, description="At least one functional requirement")
    non_functional: list[str] = Field(default_factory=list)


class Constraints(BaseModel):
    tech_stack: list[str] = Field(default_factory=list)
    performance: str | None = None
    security: str | None = None


class Outputs(BaseModel):
    expected_artifacts: list[str] = Field(
        min_length=1, description="At least one expected artifact"
    )


class ProjectSpec(BaseModel):
    """Root schema — everything the engine needs to know about *what* to build."""

    project: ProjectInfo
    requirements: Requirements
    constraints: Constraints = Field(default_factory=Constraints)
    non_goals: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(
        min_length=1, description="At least one acceptance criterion"
    )
    outputs: Outputs

    @model_validator(mode="after")
    def security_constraints_required_if_security_domain(self) -> ProjectSpec:
        """Block if domain touches security but no security constraints provided."""
        if self.project.domain == Domain.INFRA and not self.constraints.security:
            raise ValueError(
                "Security constraints are required for infra-domain projects. "
                "Set constraints.security to describe your security requirements."
            )
        return self
