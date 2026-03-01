"""Bootstrap task — verify intake artifacts exist and initialize run-scoped trace."""

from prefect import task

from engine.context import get_state_dir
from engine.tracer import init_run, trace

REQUIRED_FILES = [
    "inputs/project_spec.yml",
    "inputs/REQUIREMENTS.md",
    "inputs/CONSTRAINTS.md",
    "inputs/NON_GOALS.md",
    "inputs/ACCEPTANCE_CRITERIA.md",
]


@task(name="bootstrap")
def bootstrap_project() -> None:
    """Verify all intake artifacts are present, start a new run, log bootstrap."""
    state_dir = get_state_dir()

    # Verify all required inputs exist (belt-and-suspenders with flow check)
    missing = [f for f in REQUIRED_FILES if not (state_dir / f).exists()]
    if missing:
        raise RuntimeError(f"Bootstrap failed — missing intake artifacts: {missing}")

    # Start a new run (creates state/runs/<run_id>/ and resets hash chain)
    init_run()

    # Ensure output directories exist
    for subdir in ("designs", "implementations", "tests", "build"):
        (state_dir / subdir).mkdir(parents=True, exist_ok=True)

    present = [f for f in REQUIRED_FILES if (state_dir / f).exists()]
    trace(
        task="bootstrap",
        inputs=present,
        outputs=[],
    )
