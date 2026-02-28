"""State directory helpers — read/write files under state/."""

from engine.context import get_state_dir


def load_state_file(name: str) -> str:
    """Read a file from state/ and return its contents."""
    path = get_state_dir() / name
    if not path.exists():
        raise FileNotFoundError(f"State file not found: {path}")
    return path.read_text()


def save_state_file(name: str, content: str) -> None:
    """Write content to a file under state/."""
    path = get_state_dir() / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


