"""Evidence runner — execute approved commands and capture structured evidence.

Only pre-configured commands from ``config.yml`` are executed.
The LLM never controls which shell commands run.
"""

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import yaml

from engine.context import get_config_path, get_state_dir
from engine.tracer import get_run_id


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


# ── Command execution ────────────────────────────────────────────────────────

def run_check(
    name: str,
    command: str,
    cwd: Path,
    timeout: int = 300,
    env: dict | None = None,
) -> dict:
    """Execute a single approved command and return a structured evidence record.

    This function does **not** decide which commands are safe — that
    responsibility belongs to the caller (``test_system``), which only
    passes commands read from ``config.yml``.

    *env* is passed to ``subprocess.run``; ``None`` inherits the parent
    process environment.
    """
    started_at = datetime.now(timezone.utc).isoformat()
    stdout = ""
    stderr = ""
    exit_code = -1

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd),
            env=env,
        )
        exit_code = result.returncode
        stdout = result.stdout
        stderr = result.stderr
    except subprocess.TimeoutExpired as exc:
        stdout = (exc.stdout or b"").decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = f"Command timed out after {timeout} seconds: {command}"
    except OSError as exc:
        stderr = f"Failed to execute command: {exc}"

    finished_at = datetime.now(timezone.utc).isoformat()

    return {
        "name": name,
        "command": command,
        "cwd": str(cwd),
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
                parts.append(f"- **Dependencies installed:** {'yes' if env['deps_installed'] else 'no'}")
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

        if r["stdout"].strip():
            stdout = r["stdout"]
            if len(stdout) > 5000:
                stdout = stdout[:2500] + "\n\n... (truncated) ...\n\n" + stdout[-2500:]
            parts.append(f"\n**stdout:**\n```\n{stdout}\n```")

        if r["stderr"].strip():
            stderr = r["stderr"]
            if len(stderr) > 3000:
                stderr = stderr[:1500] + "\n\n... (truncated) ...\n\n" + stderr[-1500:]
            parts.append(f"\n**stderr:**\n```\n{stderr}\n```")

        sections.append("\n".join(parts))

    return env_header + "\n\n---\n\n".join(sections)
