"""Tests for spec normalizer — parsing project_spec.yml into structured fields."""

import textwrap
from pathlib import Path

import pytest

from engine.spec_normalizer import normalize_spec


@pytest.fixture
def spec_dir(tmp_path):
    """Create a temp dir with a sample project_spec.yml."""
    return tmp_path


def _write_spec(spec_dir: Path, content: str) -> Path:
    p = spec_dir / "project_spec.yml"
    p.write_text(textwrap.dedent(content))
    return p


class TestNormalizeSpec:
    def test_basic_parsing(self, spec_dir):
        path = _write_spec(
            spec_dir,
            """\
            project:
              name: my-app
              description: A test app
            requirements:
              functional:
                - User can create entries
                - User can delete entries
              non_functional:
                - App should load in under 2 seconds
            constraints:
              tech_stack:
                - "frontend: React 18"
                - "storage: IndexedDB"
              performance: Search should return in 200ms.
              security: Data must be encrypted at rest.
            non_goals:
              - Multi-user support
            acceptance_criteria:
              - User can create and view entries
        """,
        )

        spec = normalize_spec(path)
        assert spec.project_name == "my-app"
        assert len(spec.features) == 2
        assert spec.features[0].priority == "required"
        assert len(spec.tech_stack) == 2
        assert spec.tech_stack[0].locked == "React 18"
        assert not spec.tech_stack[0].is_ambiguous
        assert len(spec.non_goals) == 1
        assert len(spec.acceptance_criteria) == 1

    def test_detects_ambiguous_or_choice(self, spec_dir):
        path = _write_spec(
            spec_dir,
            """\
            project:
              name: test
              description: test
            requirements:
              functional:
                - Feature one
            constraints:
              tech_stack:
                - "frontend: React or Next.js"
            non_goals: []
            acceptance_criteria: []
        """,
        )

        spec = normalize_spec(path)
        assert spec.tech_stack[0].is_ambiguous
        assert len(spec.tech_stack[0].alternatives) >= 2
        assert spec.tech_stack[0].locked is None
        assert len(spec.ambiguities) == 1

    def test_detects_slash_alternatives(self, spec_dir):
        path = _write_spec(
            spec_dir,
            """\
            project:
              name: test
              description: test
            requirements:
              functional:
                - Feature one
            constraints:
              tech_stack:
                - "storage: SQLite / IndexedDB"
            non_goals: []
            acceptance_criteria: []
        """,
        )

        spec = normalize_spec(path)
        assert spec.tech_stack[0].is_ambiguous
        assert "SQLite" in spec.tech_stack[0].alternatives
        assert "IndexedDB" in spec.tech_stack[0].alternatives

    def test_optional_features_get_nice_to_have_priority(self, spec_dir):
        path = _write_spec(
            spec_dir,
            """\
            project:
              name: test
              description: test
            requirements:
              functional:
                - User can create entries
                - User can optionally add tags to entries
            constraints:
              tech_stack: []
            non_goals: []
            acceptance_criteria: []
        """,
        )

        spec = normalize_spec(path)
        assert spec.features[0].priority == "required"
        assert spec.features[1].priority == "nice_to_have"

    def test_performance_constraints_split_by_sentence(self, spec_dir):
        path = _write_spec(
            spec_dir,
            """\
            project:
              name: test
              description: test
            requirements:
              functional: []
            constraints:
              tech_stack: []
              performance: Search in 200ms. Load in 2s. Save in 100ms.
            non_goals: []
            acceptance_criteria: []
        """,
        )

        spec = normalize_spec(path)
        assert len(spec.performance_constraints) == 3

    def test_design_context_output(self, spec_dir):
        path = _write_spec(
            spec_dir,
            """\
            project:
              name: my-app
              description: test
            requirements:
              functional:
                - User can create entries
                - User can optionally export data
            constraints:
              tech_stack:
                - "frontend: React 18"
                - "storage: SQLite or IndexedDB"
              performance: Fast search.
              security: Encrypt data.
            non_goals: []
            acceptance_criteria: []
        """,
        )

        spec = normalize_spec(path)
        ctx = spec.to_design_context()
        assert "Required (must implement)" in ctx
        assert "Nice to have" in ctx
        assert "Locked Technology Decisions" in ctx
        assert "React 18" in ctx
        assert "Ambiguous Technology Choices" in ctx
        assert "SQLite" in ctx

    def test_no_ambiguity_when_all_locked(self, spec_dir):
        path = _write_spec(
            spec_dir,
            """\
            project:
              name: test
              description: test
            requirements:
              functional:
                - Feature one
            constraints:
              tech_stack:
                - "frontend: React 18"
                - "bundler: Vite 4"
            non_goals: []
            acceptance_criteria: []
        """,
        )

        spec = normalize_spec(path)
        assert len(spec.ambiguities) == 0
        assert all(not t.is_ambiguous for t in spec.tech_stack)
