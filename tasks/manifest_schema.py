"""File manifest schema — structured contract for the extract step."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class FileEntry(BaseModel):
    """A single file to extract: relative path + content."""

    path: str = Field(min_length=1, description="Relative file path")
    content: str

    @field_validator("path")
    @classmethod
    def path_not_whitespace(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("path must not be blank or whitespace-only")
        return v


class FileManifest(BaseModel):
    """Top-level manifest listing every file the LLM produced."""

    files: list[FileEntry] = Field(min_length=1, description="At least one file required")
