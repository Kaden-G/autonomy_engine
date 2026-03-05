"""Run Pipeline page — launch the autonomous flow and monitor progress."""

import subprocess
import sys
import time

import streamlit as st

from dashboard.components.pipeline_visual import render_pipeline
from dashboard.components.trace_timeline import render_timeline
from dashboard.data_loader import (
    get_latest_run_id,
    get_pipeline_status,
    is_intake_complete,
    list_runs,
    load_trace,
)


def render(project_dir):
    st.title("Run Pipeline")

    # ── Section A: Launch ────────────────────────────────────────────────
    st.subheader("Launch")

    intake_ok = is_intake_complete(project_dir)
    if not intake_ok:
        st.warning(
            "Intake is incomplete. Create a project first before running the pipeline."
        )

    proc = st.session_state.get("pipeline_process")
    is_running = proc is not None and proc.poll() is None

    col_btn, col_status = st.columns([1, 2])
    with col_btn:
        start_disabled = not intake_ok or is_running
        if st.button(
            "Start Pipeline",
            type="primary",
            disabled=start_disabled,
        ):
            # Use the Prefect-free pipeline runner to avoid ephemeral
            # server startup and corrupted migration issues.
            process = subprocess.Popen(
                [
                    sys.executable, "-m", "dashboard.pipeline_runner",
                    "--project-dir", str(project_dir),
                ],
                cwd=str(project_dir)
                if (project_dir / "dashboard").is_dir()
                else str(project_dir.parent),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            st.session_state["pipeline_process"] = process
            st.rerun()

    with col_status:
        if is_running:
            st.info("Pipeline is running...")
        elif proc is not None:
            rc = proc.poll()
            if rc == 0:
                st.success("Pipeline completed successfully.")
            else:
                st.error(f"Pipeline exited with code {rc}.")
                # Show last output lines for debugging
                try:
                    out = proc.stdout.read().decode(errors="replace") if proc.stdout else ""
                    if out:
                        with st.expander("Process output"):
                            st.code(out[-2000:])
                except Exception:
                    pass

    # ── Section B: Live Progress ─────────────────────────────────────────
    st.divider()
    st.subheader("Progress")

    run_id = get_latest_run_id(project_dir)
    if run_id:
        pipeline_status = get_pipeline_status(project_dir)
        trace_entries = load_trace(project_dir, run_id)
        trace_stages = list(dict.fromkeys(e["task"] for e in trace_entries))

        render_pipeline(pipeline_status, trace_stages)

        if trace_entries:
            with st.expander("Trace Timeline", expanded=is_running):
                render_timeline(trace_entries)
        else:
            st.caption("Waiting for trace entries...")
    else:
        st.caption("No runs found yet.")

    # Auto-refresh while running
    if is_running:
        time.sleep(2)
        st.rerun()

    # ── Section C: History ───────────────────────────────────────────────
    st.divider()
    st.subheader("Recent Runs")

    runs = list_runs(project_dir)
    if not runs:
        st.caption("No previous runs.")
    else:
        for run in runs[:5]:
            rid = run["run_id"]
            entries = run["trace_entries"]
            stages = ", ".join(run["stages"]) if run["stages"] else "no stages"
            started = run.get("started_at", "?")
            st.markdown(
                f"**`{rid[:12]}...`** — {entries} trace entries — stages: {stages} — started: {started}"
            )
