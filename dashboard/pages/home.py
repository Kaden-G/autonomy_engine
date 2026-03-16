"""Dashboard home page — pipeline overview and recent runs."""

import streamlit as st

from dashboard.components.pipeline_visual import render_pipeline
from dashboard.data_loader import (
    get_cache_stats,
    get_intake_status,
    get_pipeline_status,
    list_runs,
    load_project_spec,
)


def render(project_dir):
    st.title("🏗️ Autonomy Engine Dashboard")

    from dashboard.components.page_header import render_page_description
    render_page_description(
        "Your project at a glance. The <strong>pipeline status</strong> bar shows which stages "
        "have completed (green) or are pending (gray). Below, <strong>Recent Runs</strong> "
        "lists past executions — expand any run to see its trace count and jump to the "
        "Run Inspector. The <strong>Cache</strong> panel shows how many LLM responses are "
        "cached (cached calls are free and instant on re-runs)."
    )

    # Project info
    spec = load_project_spec(project_dir)
    if spec:
        project = spec.get("project", {})
        st.markdown(
            f"**Project:** {project.get('name', 'Unknown')} · "
            f"**Domain:** {project.get('domain', 'N/A')} · "
            f"**Description:** {project.get('description', '')}"
        )
    else:
        st.warning("No project spec found. Run intake first.")

    st.divider()

    # Pipeline status
    st.subheader("Pipeline Status")
    pipeline_status = get_pipeline_status(project_dir)
    render_pipeline(pipeline_status)

    # Intake check
    intake = get_intake_status(project_dir)
    if not all(intake.values()):
        missing = [k for k, v in intake.items() if not v]
        st.warning(f"Intake incomplete. Missing: {', '.join(missing)}")

    st.divider()

    # Recent runs and cache stats side by side
    col1, col2 = st.columns([3, 1])

    with col1:
        st.subheader("Recent Runs")
        runs = list_runs(project_dir)
        if not runs:
            st.info("No runs found. Execute the pipeline to see results here.")
        else:
            for run in runs[:10]:
                run_id = run["run_id"]
                started = run.get("started_at", "Unknown")
                if isinstance(started, str) and len(started) > 19:
                    started = started[:19].replace("T", " ")
                stages = " → ".join(run.get("stages", []))
                entries = run["trace_entries"]
                evidence = run["evidence_count"]
                decisions = run["decision_count"]

                with st.expander(
                    f"🔹 **{run_id}** — {entries} trace entries · "
                    f"{evidence} evidence · {decisions} decisions"
                ):
                    st.caption(f"Started: {started}")
                    st.caption(f"Stages: {stages}")
                    if st.button("Inspect Run →", key=f"inspect_{run_id}"):
                        st.session_state["selected_run"] = run_id
                        st.session_state["page"] = "Run Inspector"
                        st.rerun()

    with col2:
        st.subheader("Cache")
        cache = get_cache_stats(project_dir)
        st.metric("Cached Responses", cache["total_entries"])
        if cache.get("by_stage"):
            for stage, count in cache["by_stage"].items():
                st.caption(f"{stage}: {count}")
