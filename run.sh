#!/usr/bin/env bash
# Launch the Autonomy Engine dashboard, pipeline, or intake.
#
# Usage:
#   ./run.sh              — start the dashboard
#   ./run.sh pipeline     — run the pipeline (CLI, no dashboard)
#   ./run.sh intake FILE  — load a spec from YAML
#
# Python resolution order (first hit wins):
#   1. $AE_PYTHON env var (explicit override)
#   2. active venv ($VIRTUAL_ENV/bin/python3)
#   3. python3 on PATH (command -v python3)
#   4. `conda run --no-capture-output python3` fallback

set -e
cd "$(dirname "$0")"

if [ -n "$AE_PYTHON" ] && [ -x "$AE_PYTHON" ]; then
    PYTHON="$AE_PYTHON"
elif [ -n "$VIRTUAL_ENV" ] && [ -x "$VIRTUAL_ENV/bin/python3" ]; then
    PYTHON="$VIRTUAL_ENV/bin/python3"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON="$(command -v python3)"
elif command -v conda >/dev/null 2>&1; then
    PYTHON="conda run --no-capture-output python3"
else
    echo "run.sh: no python3 found on PATH. Set AE_PYTHON, activate a venv, or install python3." >&2
    exit 127
fi

case "${1:-dashboard}" in
    dashboard)
        echo "Starting Autonomy Engine dashboard with: $PYTHON"
        $PYTHON -m streamlit run dashboard/app.py
        ;;
    pipeline)
        shift
        echo "Running pipeline with: $PYTHON"
        $PYTHON graph/pipeline.py "$@"
        ;;
    intake)
        shift
        echo "Running intake with: $PYTHON"
        $PYTHON -m intake.intake "$@"
        ;;
    *)
        echo "Usage: ./run.sh [dashboard|pipeline|intake] [args...]"
        exit 1
        ;;
esac
