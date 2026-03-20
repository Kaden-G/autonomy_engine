#!/usr/bin/env bash
# Launch the Autonomy Engine dashboard using conda Python.
#
# Usage:
#   ./run.sh              — start the dashboard
#   ./run.sh pipeline     — run the pipeline (CLI, no dashboard)
#   ./run.sh intake FILE  — load a spec from YAML
#
# This script exists because macOS has multiple Pythons and the
# system Python (/Library/Frameworks/...) has old dependencies.
# Conda's Python has the right versions of prefect + pydantic.

set -e
cd "$(dirname "$0")"

# Use conda's Python explicitly
PYTHON="/usr/local/Caskroom/miniconda/base/bin/python3"

if [ ! -x "$PYTHON" ]; then
    echo "Conda Python not found at $PYTHON"
    echo "Trying 'conda run' instead..."
    PYTHON="conda run --no-capture-output python3"
fi

case "${1:-dashboard}" in
    dashboard)
        echo "Starting Autonomy Engine dashboard..."
        $PYTHON -m streamlit run dashboard/app.py
        ;;
    pipeline)
        shift
        echo "Running pipeline..."
        $PYTHON flows/autonomous_flow.py "$@"
        ;;
    intake)
        shift
        echo "Running intake..."
        $PYTHON -m intake.intake "$@"
        ;;
    *)
        echo "Usage: ./run.sh [dashboard|pipeline|intake] [args...]"
        exit 1
        ;;
esac
