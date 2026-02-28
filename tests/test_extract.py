"""Tests for tasks.extract — focused on path traversal safety."""

from pathlib import Path

import pytest

from tasks.extract import _safe_path, _parse_code_blocks


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


# ── _parse_code_blocks ───────────────────────────────────────────────────────


class TestParseCodeBlocks:
    """Verify parsing extracts only filename-labelled code blocks."""

    def test_bold_filename(self):
        md = "**app.py**\n```python\nprint('hello')\n```\n"
        result = _parse_code_blocks(md)
        assert "app.py" in result
        assert result["app.py"] == "print('hello')"

    def test_header_filename(self):
        md = "### config.py\n```python\nX = 1\n```\n"
        result = _parse_code_blocks(md)
        assert "config.py" in result
        assert result["config.py"] == "X = 1"

    def test_nested_path_bold(self):
        md = "**src/models/user.py**\n```python\nclass User: pass\n```\n"
        result = _parse_code_blocks(md)
        assert "src/models/user.py" in result

    def test_unlabelled_block_skipped(self):
        md = "Some text\n```sql\nSELECT 1;\n```\n"
        result = _parse_code_blocks(md)
        assert len(result) == 0

    def test_traversal_filename_parsed_but_caught_by_safe_path(self):
        """The parser will extract the filename; _safe_path is what blocks it."""
        md = "**../../evil.py**\n```python\nimport os\n```\n"
        result = _parse_code_blocks(md)
        # Parser extracts it — the safety check is in _safe_path, not here
        assert "../../evil.py" in result
        # Verify _safe_path catches it
        with pytest.raises(ValueError, match="Parent traversal"):
            _safe_path(Path("/output"), "../../evil.py")
