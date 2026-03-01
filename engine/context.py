"""Project context — resolved paths for engine and project directories.

Single ``init(project_dir)`` call at startup.  All modules import path
accessors from here instead of computing paths at import time.
"""

from pathlib import Path

# Engine install location (immutable)
ENGINE_ROOT: Path = Path(__file__).resolve().parent.parent

# Module-level state — set once via init()
_project_dir: Path | None = None


def init(project_dir: str | Path | None = None) -> None:
    """Set the active project directory.

    Must be called once before any ``get_*`` accessor.  When
    *project_dir* is ``None`` the engine root is used (backward-
    compatible default).
    """
    global _project_dir
    if project_dir is None:
        _project_dir = ENGINE_ROOT
    else:
        _project_dir = Path(project_dir).resolve()


def _ensure_init() -> Path:
    """Return the resolved project dir, auto-initializing to ENGINE_ROOT if needed."""
    global _project_dir
    if _project_dir is None:
        _project_dir = ENGINE_ROOT
    return _project_dir


# ── Path accessors ───────────────────────────────────────────────────────────


def get_project_dir() -> Path:
    """Return the active project directory (set via ``init()``)."""
    return _ensure_init()


def get_state_dir() -> Path:
    """Return ``<project_dir>/state``."""
    return _ensure_init() / "state"


def get_config_path() -> Path:
    """Return ``<project_dir>/config.yml``, falling back to engine default."""
    project = _ensure_init()
    local = project / "config.yml"
    if local.exists() or project != ENGINE_ROOT:
        return local
    return ENGINE_ROOT / "config.yml"


def get_templates_dir() -> Path:
    """Return project-local ``templates/`` if it exists, else engine default."""
    project = _ensure_init()
    local = project / "templates"
    if local.is_dir():
        return local
    return ENGINE_ROOT / "templates"


def get_prompts_dir() -> Path:
    """Return project-local ``templates/prompts/`` if it exists, else engine default."""
    project = _ensure_init()
    local = project / "templates" / "prompts"
    if local.is_dir():
        return local
    return ENGINE_ROOT / "templates" / "prompts"
