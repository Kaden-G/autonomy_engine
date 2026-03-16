"""Run Inspector — detailed view of a single pipeline run."""

import json

import streamlit as st

from dashboard.components.evidence_card import render_evidence_card
from dashboard.components.pipeline_visual import render_pipeline
from dashboard.components.trace_timeline import render_timeline
from dashboard.data_loader import (
    get_pipeline_status,
    list_runs,
    load_artifact,
    load_config_snapshot,
    load_decisions,
    load_evidence,
    load_trace,
)


def render(project_dir):
    st.title("🔍 Run Inspector")

    from dashboard.components.page_header import render_page_description
    render_page_description(
        "Deep-dive into a single pipeline run. Select a run from the dropdown, then explore "
        "five tabs: <strong>Trace Timeline</strong> shows every step the engine took (with model, "
        "cache hits, and timing). <strong>Evidence</strong> displays test results with exit codes "
        "and output. <strong>Decisions</strong> shows any gate choices made during the run. "
        "<strong>Artifacts</strong> lets you read the generated architecture, implementation, "
        "and verification docs. <strong>Config Snapshot</strong> shows the exact settings used."
    )

    # Run selector
    runs = list_runs(project_dir)
    if not runs:
        st.info("No runs found.")
        return

    run_ids = [r["run_id"] for r in runs]
    default_idx = 0
    if "selected_run" in st.session_state and st.session_state["selected_run"] in run_ids:
        default_idx = run_ids.index(st.session_state["selected_run"])

    selected = st.selectbox("Select Run", run_ids, index=default_idx)

    st.divider()

    # Load data
    trace_entries = load_trace(project_dir, selected)
    evidence = load_evidence(project_dir, selected)
    decisions = load_decisions(project_dir, selected)

    # Pipeline visual for this run
    pipeline_status = get_pipeline_status(project_dir)
    trace_stages = [e["task"] for e in trace_entries]
    render_pipeline(pipeline_status, trace_stages)

    st.divider()

    # Tabs for different views
    tab_trace, tab_evidence, tab_decisions, tab_artifacts, tab_config = st.tabs(
        [
            "📋 Trace Timeline",
            "🧪 Evidence",
            "🤝 Decisions",
            "📄 Artifacts",
            "⚙️ Config Snapshot",
        ]
    )

    with tab_trace:
        st.subheader(f"Trace Timeline — {len(trace_entries)} entries")

        # Summary metrics
        model_calls = sum(1 for e in trace_entries if e.get("model"))
        cache_hits = sum(1 for e in trace_entries if e.get("extra", {}).get("cache_hit"))

        mcol1, mcol2, mcol3 = st.columns(3)
        mcol1.metric("Total Entries", len(trace_entries))
        mcol2.metric("Model Calls", model_calls)
        mcol3.metric("Cache Hits", cache_hits)

        render_timeline(trace_entries)

    with tab_evidence:
        st.subheader(f"Evidence Records — {len(evidence)} checks")
        if not evidence:
            st.info("No evidence records for this run.")
        else:
            passed = sum(
                1
                for r in evidence
                if r.get("exit_code") == 0 and r.get("name") != "no_checks_configured"
            )
            failed = sum(
                1
                for r in evidence
                if r.get("exit_code", 0) != 0 and r.get("name") != "no_checks_configured"
            )
            ecol1, ecol2 = st.columns(2)
            ecol1.metric("Passed", passed)
            ecol2.metric("Failed", failed)

            for record in evidence:
                render_evidence_card(record)

    with tab_decisions:
        st.subheader(f"Decision Records — {len(decisions)} gates")
        if not decisions:
            st.info("No decision gates were triggered in this run.")
        else:
            for dec in decisions:
                gate = dec.get("gate", "unknown")
                selected_opt = dec.get("selected", "?")
                actor = dec.get("actor", "unknown")
                ts = dec.get("timestamp", "")
                rationale = dec.get("rationale", "")
                allowed = dec.get("allowed_options", [])

                st.markdown(f"**🚦 {gate}** — Stage: `{dec.get('stage', '?')}`")
                dcol1, dcol2, dcol3 = st.columns(3)
                dcol1.markdown(f"**Selected:** `{selected_opt}`")
                dcol2.markdown(f"**Actor:** `{actor}`")
                dcol3.markdown(f"**Options:** {', '.join(f'`{o}`' for o in allowed)}")
                if rationale:
                    st.caption(f"Rationale: {rationale}")
                st.caption(f"Timestamp: {ts}")
                st.divider()

    with tab_artifacts:
        st.subheader("Pipeline Artifacts")
        artifacts = {
            "Requirements": "inputs/REQUIREMENTS.md",
            "Constraints": "inputs/CONSTRAINTS.md",
            "Non-Goals": "inputs/NON_GOALS.md",
            "Acceptance Criteria": "inputs/ACCEPTANCE_CRITERIA.md",
            "Architecture": "designs/ARCHITECTURE.md",
            "Implementation": "implementations/IMPLEMENTATION.md",
            "Test Results": "tests/TEST_RESULTS.md",
            "Verification": "tests/VERIFICATION.md",
            "Build Manifest": "build/MANIFEST.md",
        }
        for label, path in artifacts.items():
            content = load_artifact(project_dir, path)
            if content:
                with st.expander(f"📄 {label}"):
                    st.markdown(content)
            else:
                st.caption(f"⬜ {label} — not yet generated")

    with tab_config:
        st.subheader("Config Snapshot")
        snapshot = load_config_snapshot(project_dir, selected)
        if snapshot:
            st.code(json.dumps(snapshot, indent=2, default=str), language="yaml")
        else:
            st.info("No config snapshot for this run.")
