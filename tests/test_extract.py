"""Tests for tasks.extract — path safety and manifest validation."""

import json
from pathlib import Path

import pytest

from tasks.extract import _safe_path, _load_and_validate_manifest


# ── _safe_path: malicious inputs ─────────────────────────────────────────────


class TestSafePathRejectsTraversal:
    """Paths that attempt to escape the output directory must raise ValueError."""

    def setup_method(self):
        self.root = Path("/fake/output")

    def test_parent_traversal_simple(self):
        with pytest.raises(ValueError, match="Parent traversal"):
            _safe_path(self.root, "../evil.py")

    def test_parent_traversal_nested(self):
        with pytest.raises(ValueError, match="Parent traversal"):
            _safe_path(self.root, "../../etc/cron.d/evil")

    def test_parent_traversal_mid_path(self):
        with pytest.raises(ValueError, match="Parent traversal"):
            _safe_path(self.root, "src/../../evil.py")

    def test_parent_traversal_deep(self):
        with pytest.raises(ValueError, match="Parent traversal"):
            _safe_path(self.root, "a/b/c/../../../../../../../tmp/x")

    def test_absolute_path_unix(self):
        with pytest.raises(ValueError, match="Absolute path"):
            _safe_path(self.root, "/tmp/x")

    def test_absolute_path_etc(self):
        with pytest.raises(ValueError, match="Absolute path"):
            _safe_path(self.root, "/etc/passwd")

    def test_empty_path(self):
        with pytest.raises(ValueError, match="Empty"):
            _safe_path(self.root, "")

    def test_whitespace_only_path(self):
        with pytest.raises(ValueError, match="Empty"):
            _safe_path(self.root, "   ")


# ── _safe_path: valid inputs ─────────────────────────────────────────────────


class TestSafePathAllowsValid:
    """Legitimate nested paths must resolve correctly under the output dir."""

    def setup_method(self):
        self.root = Path("/fake/output")

    def test_simple_file(self):
        result = _safe_path(self.root, "app.py")
        assert result == (self.root / "app.py").resolve()

    def test_nested_path(self):
        result = _safe_path(self.root, "src/app/main.py")
        assert result == (self.root / "src/app/main.py").resolve()

    def test_deeply_nested(self):
        result = _safe_path(self.root, "src/models/db/migrations/001.sql")
        assert result == (self.root / "src/models/db/migrations/001.sql").resolve()

    def test_dotfile(self):
        result = _safe_path(self.root, ".gitignore")
        assert result == (self.root / ".gitignore").resolve()

    def test_hidden_nested(self):
        result = _safe_path(self.root, "config/.env.example")
        assert result == (self.root / "config/.env.example").resolve()

    def test_result_under_root(self):
        result = _safe_path(self.root, "a/b/c.py")
        assert result.is_relative_to(self.root.resolve())


# ── _load_and_validate_manifest: valid inputs ────────────────────────────────


class TestValidManifest:
    """Valid manifest JSON must parse and validate successfully."""

    def _manifest_json(self, files: list[dict]) -> str:
        return json.dumps({"files": files})

    def test_single_file(self):
        raw = self._manifest_json([{"path": "app.py", "content": "print('hi')"}])
        result = _load_and_validate_manifest(raw)
        assert len(result.files) == 1
        assert result.files[0].path == "app.py"
        assert result.files[0].content == "print('hi')"

    def test_multiple_files(self):
        raw = self._manifest_json(
            [
                {"path": "a.py", "content": "# a"},
                {"path": "b.py", "content": "# b"},
            ]
        )
        result = _load_and_validate_manifest(raw)
        assert len(result.files) == 2

    def test_nested_paths(self):
        raw = self._manifest_json([{"path": "src/models/user.py", "content": "class User: pass"}])
        result = _load_and_validate_manifest(raw)
        assert result.files[0].path == "src/models/user.py"

    def test_dockerfile(self):
        raw = self._manifest_json([{"path": "Dockerfile", "content": "FROM python:3.12"}])
        result = _load_and_validate_manifest(raw)
        assert result.files[0].path == "Dockerfile"

    def test_makefile(self):
        raw = self._manifest_json([{"path": "Makefile", "content": "all:\n\techo hello"}])
        result = _load_and_validate_manifest(raw)
        assert result.files[0].path == "Makefile"

    def test_gitignore(self):
        raw = self._manifest_json([{"path": ".gitignore", "content": "__pycache__/\n*.pyc"}])
        result = _load_and_validate_manifest(raw)
        assert result.files[0].path == ".gitignore"

    def test_nested_dotfile(self):
        raw = self._manifest_json([{"path": "config/.env.example", "content": "KEY=val"}])
        result = _load_and_validate_manifest(raw)
        assert result.files[0].path == "config/.env.example"

    def test_empty_content(self):
        raw = self._manifest_json([{"path": "pkg/__init__.py", "content": ""}])
        result = _load_and_validate_manifest(raw)
        assert result.files[0].content == ""


# ── _load_and_validate_manifest: invalid inputs ──────────────────────────────


class TestInvalidManifest:
    """Malformed or schema-violating input must raise RuntimeError."""

    def test_not_json(self):
        with pytest.raises(RuntimeError, match="not valid JSON"):
            _load_and_validate_manifest("this is not json")

    def test_empty_string(self):
        with pytest.raises(RuntimeError, match="not valid JSON"):
            _load_and_validate_manifest("")

    def test_missing_files_key(self):
        with pytest.raises(RuntimeError, match="schema validation"):
            _load_and_validate_manifest('{"data": []}')

    def test_empty_files_list(self):
        with pytest.raises(RuntimeError, match="schema validation"):
            _load_and_validate_manifest('{"files": []}')

    def test_missing_path(self):
        with pytest.raises(RuntimeError, match="schema validation"):
            _load_and_validate_manifest('{"files": [{"content": "x"}]}')

    def test_missing_content(self):
        with pytest.raises(RuntimeError, match="schema validation"):
            _load_and_validate_manifest('{"files": [{"path": "a.py"}]}')

    def test_empty_path(self):
        with pytest.raises(RuntimeError, match="schema validation"):
            _load_and_validate_manifest('{"files": [{"path": "", "content": "x"}]}')

    def test_whitespace_path(self):
        with pytest.raises(RuntimeError, match="schema validation"):
            _load_and_validate_manifest('{"files": [{"path": "   ", "content": "x"}]}')

    def test_files_not_a_list(self):
        with pytest.raises(RuntimeError, match="schema validation"):
            _load_and_validate_manifest('{"files": "not a list"}')

    def test_json_array_instead_of_object(self):
        with pytest.raises(RuntimeError, match="schema validation"):
            _load_and_validate_manifest('[{"path": "a.py", "content": "x"}]')


# ── Manifest + _safe_path integration ────────────────────────────────────────


class TestManifestPathSafety:
    """Paths that pass schema validation but are unsafe must be caught by _safe_path."""

    def setup_method(self):
        self.root = Path("/fake/output")

    def test_traversal_path_passes_schema_caught_by_safe_path(self):
        raw = json.dumps({"files": [{"path": "../../evil.py", "content": "import os"}]})
        manifest = _load_and_validate_manifest(raw)
        assert manifest.files[0].path == "../../evil.py"
        with pytest.raises(ValueError, match="Parent traversal"):
            _safe_path(self.root, manifest.files[0].path)

    def test_absolute_path_passes_schema_caught_by_safe_path(self):
        raw = json.dumps({"files": [{"path": "/etc/passwd", "content": "root:x:0:0"}]})
        manifest = _load_and_validate_manifest(raw)
        assert manifest.files[0].path == "/etc/passwd"
        with pytest.raises(ValueError, match="Absolute path"):
            _safe_path(self.root, manifest.files[0].path)
