"""Tests for canonical type schema extraction from architecture documents."""

from tasks.implement import _extract_canonical_schema


# ── Sample architecture documents ────────────────────────────────────────────

_ARCH_WITH_SCHEMA = """\
# Architecture

## Component Diagram

- Frontend → API → Database

## Canonical Type Schema

### `src/types/index.ts`
```typescript
export interface User {
  id: string;
  email: string;
  name: string;
}

export interface Post {
  id: string;
  title: string;
  body: string;
  authorId: string;
}
```

## Data Flow

User creates post → API validates → Database stores.
"""

_ARCH_WITHOUT_SCHEMA = """\
# Architecture

## Component Diagram

- Frontend → API → Database

## Data Flow

User creates post → API validates → Database stores.
"""

_ARCH_SCHEMA_AT_END = """\
# Architecture

## Components

Just one big module.

## Canonical Type Schema

### `src/types.ts`
```typescript
export interface Item {
  id: string;
  label: string;
}
```
"""


class TestExtractCanonicalSchema:
    def test_extracts_schema_section(self):
        schema = _extract_canonical_schema(_ARCH_WITH_SCHEMA)
        assert "User" in schema
        assert "Post" in schema
        assert "id: string" in schema

    def test_stops_at_next_h2(self):
        schema = _extract_canonical_schema(_ARCH_WITH_SCHEMA)
        assert "Data Flow" not in schema
        assert "API validates" not in schema

    def test_returns_empty_when_no_schema(self):
        schema = _extract_canonical_schema(_ARCH_WITHOUT_SCHEMA)
        assert schema == ""

    def test_schema_at_end_of_document(self):
        schema = _extract_canonical_schema(_ARCH_SCHEMA_AT_END)
        assert "Item" in schema
        assert "label: string" in schema

    def test_preserves_code_blocks(self):
        schema = _extract_canonical_schema(_ARCH_WITH_SCHEMA)
        assert "```typescript" in schema
        assert "export interface" in schema

    def test_includes_file_path_headers(self):
        schema = _extract_canonical_schema(_ARCH_WITH_SCHEMA)
        assert "src/types/index.ts" in schema
