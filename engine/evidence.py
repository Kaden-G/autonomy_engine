"""Evidence runner — run quality checks and capture the receipts.

This module executes automated checks (syntax, imports, lint, type safety, tests)
against the AI-generated project and records structured evidence of every result.
Each check produces a JSON "evidence record" with the command that ran, whether
it passed or failed, full output, and timestamps.

Important safety property:
    Only pre-approved commands from the config file are executed.  The AI never
    controls which shell commands run — it only generates code, not test commands.

When no checks are explicitly configured, the engine auto-detects appropriate
checks by inspecting the project (e.g., finding ``package.json`` for Node.js
projects or ``requirements.txt`` for Python projects).
"""

import hashlib
import json
import logging
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import yaml

from engine.context import get_config_path, get_state_dir
from engine.tracer import get_run_id

logger = logging.getLogger(__name__)


# ── Evidence directory ───────────────────────────────────────────────────────


def _evidence_dir() -> Path:
    """Return ``state/runs/<run_id>/evidence/``, creating if needed."""
    d = get_state_dir() / "runs" / get_run_id() / "evidence"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Config loading ───────────────────────────────────────────────────────────


def load_configured_checks() -> list[dict]:
    """Load the ``checks`` list from ``config.yml``.

    Each entry must have ``name`` (str) and ``command`` (str).
    Returns an empty list if the section is missing or the config
    file does not exist.
    """
    config_path = get_config_path()
    if not config_path.exists():
        return []

    with open(config_path) as f:
        config = yaml.safe_load(f) or {}

    return config.get("checks") or []


# ── Auto-detection ───────────────────────────────────────────────────────────


def auto_detect_checks(project_dir: Path) -> list[dict]:
    """Inspect *project_dir* for package.json / pyproject.toml and return checks.

    This is a **fallback** — only called when the user hasn't configured
    explicit ``checks`` in ``config.yml``.  The commands returned are
    conservative smoke tests (install deps, type-check, build, test).

    Returns an empty list if no recognisable project files are found.
    """
    checks: list[dict] = []

    # ── Node.js / TypeScript projects ────────────────────────────────────
    pkg_json_path = project_dir / "package.json"
    if pkg_json_path.exists():
        try:
            pkg = json.loads(pkg_json_path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to parse %s: %s", pkg_json_path, exc)
            pkg = {}

        # Always install deps first — almost everything else depends on it
        checks.append({"name": "install", "command": "npm install"})

        scripts = pkg.get("scripts", {})

        # ALWAYS run TypeScript type-check if TypeScript is present
        if "typecheck" in scripts:
            checks.append({"name": "typecheck", "command": "npm run typecheck"})
        elif _has_ts_dependency(pkg):
            checks.append({"name": "typecheck", "command": "npx tsc --noEmit"})

        # Build — always try if script exists (this is a critical check)
        if "build" in scripts:
            checks.append({"name": "build", "command": "npm run build"})

        # Lint
        if "lint" in scripts:
            checks.append({"name": "lint", "command": "npm run lint"})

        # Test
        if "test" in scripts:
            test_cmd = scripts["test"]
            if "react-scripts test" in test_cmd:
                checks.append(
                    {"name": "test", "command": "npx react-scripts test --watchAll=false"}
                )
            else:
                checks.append({"name": "test", "command": "npm test"})

        logger.info(
            "Auto-detected %d check(s) from package.json: %s",
            len(checks),
            [c["name"] for c in checks],
        )
        return checks

    # ── Python projects ──────────────────────────────────────────────────
    pyproject_path = project_dir / "pyproject.toml"
    requirements_path = project_dir / "requirements.txt"
    setup_path = project_dir / "setup.py"

    is_python = pyproject_path.exists() or requirements_path.exists() or setup_path.exists()

    if is_python:
        # Install dependencies — use "python -m pip" instead of bare "pip" to
        # avoid stale shebang paths when using a cached virtualenv.
        if requirements_path.exists():
            checks.append(
                {"name": "install", "command": "python -m pip install -r requirements.txt"}
            )
        elif pyproject_path.exists():
            checks.append({"name": "install", "command": "python -m pip install -e ."})
        elif setup_path.exists():
            checks.append({"name": "install", "command": "python -m pip install -e ."})

        # ALWAYS run syntax check — catches truncated files and basic errors
        checks.append(
            {
                "name": "syntax-check",
                "command": "python -m py_compile $(find . -name '*.py' -not -path './.venv/*' -not -path './venv/*' -not -path './.*')"
                " && echo 'All files compile'",
            }
        )

        # ALWAYS run import validation — catches cross-file import errors
        checks.append(
            {
                "name": "import-check",
                "command": _build_python_import_check_command(project_dir),
            }
        )

        # Read pyproject.toml for tool configs
        pyproject_text = ""
        if pyproject_path.exists():
            try:
                pyproject_text = pyproject_path.read_text()
            except OSError:
                pass

        # pytest
        if "[tool.pytest" in pyproject_text or (project_dir / "pytest.ini").exists():
            checks.append({"name": "test", "command": "python -m pytest"})
        elif (project_dir / "tests").is_dir():
            checks.append({"name": "test", "command": "python -m pytest"})

        # ruff — always attempt, it's fast and catches real issues.
        # Use --fix --unsafe-fixes to auto-fix both trivial issues (unused imports)
        # and common AI patterns (unused variables, `== False` comparisons) so only
        # genuinely unfixable problems remain as errors.
        checks.append(
            {
                "name": "lint",
                "command": "python -m ruff check . --select E,F --ignore E501 --fix --unsafe-fixes",
            }
        )

        # mypy — always attempt for type safety.
        # --explicit-package-bases avoids "found twice under different module names"
        # when projects use a src/ layout.
        checks.append(
            {
                "name": "typecheck",
                "command": "python -m mypy . --ignore-missing-imports --no-error-summary --explicit-package-bases",
            }
        )

        logger.info(
            "Auto-detected %d check(s) from Python project: %s",
            len(checks),
            [c["name"] for c in checks],
        )
        return checks

    logger.info("No package.json or Python project files found — no checks to auto-detect.")
    return []


def _has_ts_dependency(pkg: dict) -> bool:
    """Return True if ``typescript`` appears in any dependency group."""
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        if "typescript" in (pkg.get(key) or {}):
            return True
    return False


def _build_python_import_check_command(project_dir: Path) -> str:
    """Build a shell command that validates all Python imports resolve.

    Writes a small checker script to a temp file and runs it with ``python``.
    This catches cross-file import errors that syntax checking alone misses.

    We use a temp script file instead of ``python -c`` because the checker
    needs a ``def`` statement, which is a compound statement that can't be
    expressed in a semicolon-separated one-liner.
    """
    py_files = list(project_dir.rglob("*.py"))
    if not py_files:
        return "echo 'No Python files found'"

    # Write the checker as a proper Python script (heredoc piped to python).
    # This avoids the `python -c` syntax limitations with compound statements.
    # The script checks every import statement in the project resolves to a real module.
    # Resolution: local file → local directory → Python standard library → installed package.
    return (
        "python << 'IMPORT_CHECK_EOF'\n"
        "import ast, sys, pathlib, importlib.util\n"
        "\n"
        "errors = []\n"
        "_SKIP = {'.venv', 'venv', 'node_modules', '__pycache__', '.git', '.tox', '.nox'}\n"
        "files = [f for f in pathlib.Path('.').rglob('*.py')\n"
        "         if not any(part in _SKIP for part in f.parts)]\n"
        "_stdlib = sys.stdlib_module_names if hasattr(sys, 'stdlib_module_names') else set()\n"
        "\n"
        "def _resolves(mod):\n"
        "    top = mod.split('.')[0]\n"
        "    return (\n"
        "        pathlib.Path(mod.replace('.', '/') + '.py').exists()\n"
        "        or pathlib.Path(mod.replace('.', '/')).is_dir()\n"
        "        or top in _stdlib\n"
        "        or importlib.util.find_spec(top) is not None\n"
        "    )\n"
        "\n"
        "for f in files:\n"
        "    if f.name == '__init__.py':\n"
        "        continue\n"
        "    try:\n"
        "        tree = ast.parse(f.read_text())\n"
        "    except SyntaxError:\n"
        "        continue  # syntax-check stage will catch this\n"
        "    for node in ast.walk(tree):\n"
        "        if isinstance(node, ast.ImportFrom) and node.module:\n"
        "            if not _resolves(node.module):\n"
        "                errors.append(f'{f}:{node.lineno}: cannot resolve {node.module}')\n"
        "\n"
        "print(f'{len(files)} files checked, {len(errors)} import issue(s)')\n"
        "for e in errors:\n"
        "    print(e)\n"
        "sys.exit(1 if errors else 0)\n"
        "IMPORT_CHECK_EOF"
    )


# ── Command execution ────────────────────────────────────────────────────────


def run_check(
    name: str,
    command: str,
    cwd: Path | None = None,
    timeout: int = 300,
    env: dict | None = None,
    sandbox=None,
) -> dict:
    """Execute a single approved command and return a structured evidence record.

    This function does **not** decide which commands are safe — that
    responsibility belongs to the caller (``test_system``), which only
    passes commands read from ``config.yml``.

    When *sandbox* is provided, execution is dispatched through the
    sandbox's backend (local subprocess, Docker container, etc.) and *cwd*
    / *env* are ignored.  This is the preferred path — it makes the
    backend substitutable.  When *sandbox* is ``None``, the command runs
    on the host via ``subprocess.run`` (kept for the non-sandboxed path
    in tasks/test.py and existing tests).
    """
    if sandbox is not None:
        raw = sandbox.run(command, timeout=timeout)
        effective_cwd = str(sandbox.workspace)
        exit_code = raw["exit_code"]
        stdout = raw["stdout"]
        stderr = raw["stderr"]
        started_at = raw["started_at"]
        finished_at = raw["finished_at"]
    else:
        if cwd is None:
            raise TypeError("run_check requires either cwd= or sandbox=")
        effective_cwd = str(cwd)
        started_at = datetime.now(timezone.utc).isoformat()
        stdout = ""
        stderr = ""
        exit_code = -1

        try:
            result = subprocess.run(
                command,
                shell=True,  # nosec B602 — commands come from config.yml, never from AI output
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=effective_cwd,
                env=env,
            )
            exit_code = result.returncode
            stdout = result.stdout
            stderr = result.stderr
        except subprocess.TimeoutExpired as exc:
            stdout = (
                (exc.stdout or b"").decode(errors="replace")
                if isinstance(exc.stdout, bytes)
                else (exc.stdout or "")
            )
            stderr = f"Command timed out after {timeout} seconds: {command}"
        except OSError as exc:
            stderr = f"Failed to execute command: {exc}"

        finished_at = datetime.now(timezone.utc).isoformat()

    return {
        "name": name,
        "command": command,
        "argv": shlex.split(command),
        "cwd": effective_cwd,
        "started_at": started_at,
        "finished_at": finished_at,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "stdout_hash": hashlib.sha256(stdout.encode()).hexdigest(),
        "stderr_hash": hashlib.sha256(stderr.encode()).hexdigest(),
    }


def no_checks_record() -> dict:
    """Return a sentinel evidence record for when no checks are configured."""
    now = datetime.now(timezone.utc).isoformat()
    msg = "No checks configured in config.yml. Add a 'checks' section to enable automated testing."
    return {
        "name": "no_checks_configured",
        "command": "",
        "cwd": "",
        "started_at": now,
        "finished_at": now,
        "exit_code": -1,
        "stdout": "",
        "stderr": msg,
        "stdout_hash": hashlib.sha256(b"").hexdigest(),
        "stderr_hash": hashlib.sha256(msg.encode()).hexdigest(),
    }


# ── Evidence storage ─────────────────────────────────────────────────────────


def save_evidence(record: dict) -> Path:
    """Write an evidence record to the active run's evidence directory."""
    path = _evidence_dir() / f"{record['name']}.json"
    path.write_text(json.dumps(record, indent=2) + "\n")
    return path


def load_all_evidence() -> list[dict]:
    """Load all evidence records for the active run, sorted by name."""
    edir = _evidence_dir()
    records = []
    for path in sorted(edir.glob("*.json")):
        records.append(json.loads(path.read_text()))
    return records


# ── Formatting for LLM / human consumption ──────────────────────────────────


def format_evidence_for_llm(records: list[dict]) -> str:
    """Format evidence records into structured text for LLM consumption."""
    if not records:
        return "No evidence collected — no checks were configured or executed."

    # Show environment info once (from the first record that has it)
    env_header = ""
    for r in records:
        env = r.get("environment")
        if env:
            parts = [f"- **Sandboxed:** {'yes' if env.get('sandboxed') else 'no'}"]
            if env.get("python_version"):
                parts.append(f"- **Python:** {env['python_version']}")
            if env.get("platform"):
                parts.append(f"- **Platform:** {env['platform']}")
            if env.get("deps_installed") is not None:
                parts.append(
                    f"- **Dependencies installed:** {'yes' if env['deps_installed'] else 'no'}"
                )
            env_header = "## Execution Environment\n" + "\n".join(parts) + "\n\n"
            break

    sections = []
    for r in records:
        if r["name"] == "no_checks_configured":
            sections.append(
                "### No checks configured\n\n"
                "No automated checks were configured for this project. "
                "Evidence-based verification is not possible."
            )
            continue

        status = "PASSED" if r["exit_code"] == 0 else "FAILED"
        parts = [
            f"### {r['name']} — {status}",
            f"- **Command:** `{r['command']}`",
            f"- **Exit code:** {r['exit_code']}",
            f"- **Started:** {r['started_at']}",
            f"- **Finished:** {r['finished_at']}",
        ]

        # Maps to: OWASP LLM01 — raw command output is untrusted content.
        # Any bytes the test runner captured could contain attacker-crafted
        # strings aimed at jailbreaking the verify LLM (e.g. a malicious
        # dependency printing `</user_content>IGNORE PRIOR …`). We neutralize
        # them before truncation so the envelope is always clear.
        # See engine/prompt_guard.py.
        from engine.prompt_guard import sanitize_untrusted  # local import to keep module load cheap

        if r["stdout"].strip():
            stdout = r["stdout"]
            if len(stdout) > 5000:
                stdout = stdout[:2500] + "\n\n... (truncated) ...\n\n" + stdout[-2500:]
            stdout = sanitize_untrusted(stdout, tag="evidence_stdout")
            parts.append(f"\n**stdout:**\n```\n{stdout}\n```")

        if r["stderr"].strip():
            stderr = r["stderr"]
            if len(stderr) > 3000:
                stderr = stderr[:1500] + "\n\n... (truncated) ...\n\n" + stderr[-1500:]
            stderr = sanitize_untrusted(stderr, tag="evidence_stderr")
            parts.append(f"\n**stderr:**\n```\n{stderr}\n```")

        sections.append("\n".join(parts))

    return env_header + "\n\n---\n\n".join(sections)
