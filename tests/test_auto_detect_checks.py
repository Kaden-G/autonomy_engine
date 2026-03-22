"""Tests for engine.evidence.auto_detect_checks — project-aware check discovery."""

import json


from engine.evidence import auto_detect_checks


# ── Helpers ──────────────────────────────────────────────────────────────────


def _write_pkg(tmp_path, scripts=None, dev_deps=None, deps=None):
    """Write a minimal package.json with optional scripts and deps."""
    pkg = {}
    if scripts is not None:
        pkg["scripts"] = scripts
    if dev_deps is not None:
        pkg["devDependencies"] = dev_deps
    if deps is not None:
        pkg["dependencies"] = deps
    (tmp_path / "package.json").write_text(json.dumps(pkg))


# ── Node.js / TypeScript projects ────────────────────────────────────────────


class TestNodeAutoDetect:
    """Auto-detection from package.json."""

    def test_empty_package_json_returns_install_only(self, tmp_path):
        _write_pkg(tmp_path)
        checks = auto_detect_checks(tmp_path)
        assert len(checks) == 1
        assert checks[0]["name"] == "install"
        assert checks[0]["command"] == "npm install"

    def test_detects_build_script(self, tmp_path):
        _write_pkg(tmp_path, scripts={"build": "vite build"})
        checks = auto_detect_checks(tmp_path)
        names = [c["name"] for c in checks]
        assert "install" in names
        assert "build" in names
        assert checks[-1]["command"] == "npm run build"

    def test_detects_test_script(self, tmp_path):
        _write_pkg(tmp_path, scripts={"test": "vitest"})
        checks = auto_detect_checks(tmp_path)
        names = [c["name"] for c in checks]
        assert "test" in names

    def test_detects_lint_script(self, tmp_path):
        _write_pkg(tmp_path, scripts={"lint": "eslint ."})
        checks = auto_detect_checks(tmp_path)
        names = [c["name"] for c in checks]
        assert "lint" in names

    def test_detects_typecheck_script(self, tmp_path):
        _write_pkg(tmp_path, scripts={"typecheck": "tsc --noEmit"})
        checks = auto_detect_checks(tmp_path)
        names = [c["name"] for c in checks]
        assert "typecheck" in names

    def test_typescript_dep_adds_tsc_when_no_typecheck_script(self, tmp_path):
        _write_pkg(tmp_path, dev_deps={"typescript": "^5.0.0"})
        checks = auto_detect_checks(tmp_path)
        names = [c["name"] for c in checks]
        assert "typecheck" in names
        tc = next(c for c in checks if c["name"] == "typecheck")
        assert tc["command"] == "npx tsc --noEmit"

    def test_typecheck_script_preferred_over_tsc_fallback(self, tmp_path):
        _write_pkg(
            tmp_path,
            scripts={"typecheck": "tsc -p tsconfig.json --noEmit"},
            dev_deps={"typescript": "^5.0.0"},
        )
        checks = auto_detect_checks(tmp_path)
        tc = next(c for c in checks if c["name"] == "typecheck")
        assert tc["command"] == "npm run typecheck"

    def test_react_scripts_test_gets_watchAll_false(self, tmp_path):
        _write_pkg(tmp_path, scripts={"test": "react-scripts test"})
        checks = auto_detect_checks(tmp_path)
        tc = next(c for c in checks if c["name"] == "test")
        assert "--watchAll=false" in tc["command"]

    def test_full_featured_project(self, tmp_path):
        _write_pkg(
            tmp_path,
            scripts={
                "build": "vite build",
                "test": "vitest run",
                "lint": "eslint src/",
                "typecheck": "tsc --noEmit",
            },
            dev_deps={"typescript": "^5.0.0"},
        )
        checks = auto_detect_checks(tmp_path)
        names = [c["name"] for c in checks]
        assert names == ["install", "typecheck", "build", "lint", "test"]

    def test_install_is_always_first(self, tmp_path):
        _write_pkg(tmp_path, scripts={"test": "jest", "build": "tsc"})
        checks = auto_detect_checks(tmp_path)
        assert checks[0]["name"] == "install"

    def test_malformed_package_json_returns_empty(self, tmp_path):
        (tmp_path / "package.json").write_text("not json {{{")
        checks = auto_detect_checks(tmp_path)
        # Should still return install (we detect package.json exists)
        # but pkg parsing fails gracefully — install is appended before parse
        assert len(checks) == 1  # just install
        assert checks[0]["name"] == "install"

    def test_typescript_in_dependencies_also_detected(self, tmp_path):
        _write_pkg(tmp_path, deps={"typescript": "^5.0.0"})
        checks = auto_detect_checks(tmp_path)
        names = [c["name"] for c in checks]
        assert "typecheck" in names


# ── Python projects ──────────────────────────────────────────────────────────


class TestPythonAutoDetect:
    """Auto-detection from pyproject.toml / requirements.txt."""

    def test_requirements_txt_adds_pip_install(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("flask==2.0\n")
        checks = auto_detect_checks(tmp_path)
        assert checks[0]["name"] == "install"
        assert "requirements.txt" in checks[0]["command"]

    def test_pyproject_toml_adds_pip_install_editable(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'myapp'\n")
        checks = auto_detect_checks(tmp_path)
        assert checks[0]["name"] == "install"
        assert "-e ." in checks[0]["command"]

    def test_setup_py_adds_pip_install_editable(self, tmp_path):
        (tmp_path / "setup.py").write_text("from setuptools import setup\nsetup()")
        checks = auto_detect_checks(tmp_path)
        assert checks[0]["name"] == "install"
        assert "-e ." in checks[0]["command"]

    def test_pytest_config_adds_test_check(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
        checks = auto_detect_checks(tmp_path)
        names = [c["name"] for c in checks]
        assert "test" in names

    def test_tests_directory_adds_pytest(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("flask\n")
        (tmp_path / "tests").mkdir()
        checks = auto_detect_checks(tmp_path)
        names = [c["name"] for c in checks]
        assert "test" in names

    def test_ruff_config_adds_lint(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nline-length = 88\n")
        checks = auto_detect_checks(tmp_path)
        names = [c["name"] for c in checks]
        assert "lint" in names

    def test_mypy_config_adds_typecheck(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[tool.mypy]\nstrict = true\n")
        checks = auto_detect_checks(tmp_path)
        names = [c["name"] for c in checks]
        assert "typecheck" in names

    def test_full_python_project(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            "[project]\nname = 'myapp'\n[tool.pytest.ini_options]\n[tool.ruff]\n[tool.mypy]\n"
        )
        checks = auto_detect_checks(tmp_path)
        names = [c["name"] for c in checks]
        assert "install" in names
        assert "test" in names
        assert "lint" in names
        assert "typecheck" in names


# ── No project files ─────────────────────────────────────────────────────────


class TestNoProject:
    """When the directory has no recognizable project files."""

    def test_empty_dir_returns_empty(self, tmp_path):
        checks = auto_detect_checks(tmp_path)
        assert checks == []

    def test_random_files_return_empty(self, tmp_path):
        (tmp_path / "README.md").write_text("# Hello")
        (tmp_path / "data.csv").write_text("a,b,c\n")
        checks = auto_detect_checks(tmp_path)
        assert checks == []


# ── Priority: package.json wins over Python ──────────────────────────────────


class TestPriority:
    """When both Node and Python project files exist, package.json wins."""

    def test_package_json_takes_precedence(self, tmp_path):
        _write_pkg(tmp_path, scripts={"build": "vite build"})
        (tmp_path / "requirements.txt").write_text("flask\n")
        checks = auto_detect_checks(tmp_path)
        # Should detect Node.js project, not Python
        assert checks[0]["command"] == "npm install"
