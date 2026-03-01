"""Bootstrap task — verify intake artifacts and scaffold run-scoped directories."""

from prefect import task

from engine.context import get_state_dir
from engine.tracer import get_run_id, trace

REQUIRED_FILES = [
    "inputs/project_spec.yml",
    "inputs/REQUIREMENTS.md",
    "inputs/CONSTRAINTS.md",
    "inputs/NON_GOALS.md",
    "inputs/ACCEPTANCE_CRITERIA.md",
]


@task(name="bootstrap")
def bootstrap_project() -> None:
    """Verify all intake artifacts are present, scaffold directories, log bootstrap."""
    state_dir = get_state_dir()

    # Verify all required inputs exist (belt-and-suspenders with flow check)
    missing = [f for f in REQUIRED_FILES if not (state_dir / f).exists()]
    if missing:
        raise RuntimeError(f"Bootstrap failed — missing intake artifacts: {missing}")

    # Ensure global output directories exist
    for subdir in ("designs", "implementations", "tests", "build"):
        (state_dir / subdir).mkdir(parents=True, exist_ok=True)

    # Ensure run-scoped directories exist for evidence and decisions
    run_id = get_run_id()
    run_dir = state_dir / "runs" / run_id
    for subdir in ("evidence", "decisions"):
        (run_dir / subdir).mkdir(parents=True, exist_ok=True)

    present = [f for f in REQUIRED_FILES if (state_dir / f).exists()]

    # Include config snapshot in outputs if it was created by init_run()
    run_dir = state_dir / "runs" / run_id
    outputs = []
    config_snapshot = run_dir / "config_snapshot.yml"
    if config_snapshot.exists():
        outputs.append(str(config_snapshot.relative_to(state_dir)))

    trace(
        task="bootstrap",
        inputs=present,
        outputs=outputs,
    )
