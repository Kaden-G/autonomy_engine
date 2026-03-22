"""Project context — "where do files live?" resolved once at startup.

The engine can run against its own directory or a separate project folder.
This module resolves that choice once at startup, then provides path
accessors (get_state_dir, get_config_path, etc.) that every other module
uses.  No module computes paths on its own — they all ask this module.

Thread safety:
    The project directory is stored in ``threading.local()`` so that
    concurrent pipelines targeting different project folders don't
    clobber each other.  Single-threaded usage is unaffected.
"""

import threading
from pathlib import Path

# Engine install location (immutable — no thread-safety concern)
ENGINE_ROOT: Path = Path(__file__).resolve().parent.parent

# Thread-local storage for mutable state.
# Each thread gets its own _project_dir, so concurrent pipeline runs
# targeting different project folders are fully isolated.
_local = threading.local()


def init(project_dir: str | Path | None = None) -> None:
    """Set the active project directory for the current thread.

    Must be called once before any ``get_*`` accessor.  When
    *project_dir* is ``None`` the engine root is used (backward-
    compatible default).
    """
    if project_dir is None:
        _local.project_dir = ENGINE_ROOT
    else:
        _local.project_dir = Path(project_dir).resolve()


def _ensure_init() -> Path:
    """Return the resolved project dir, auto-initializing to ENGINE_ROOT if needed."""
    pd = getattr(_local, "project_dir", None)
    if pd is None:
        _local.project_dir = ENGINE_ROOT
        return ENGINE_ROOT
    return pd


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
