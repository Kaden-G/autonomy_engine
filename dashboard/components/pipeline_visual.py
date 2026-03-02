"""Reusable pipeline stage visualization component."""

import streamlit as st


# Stage definitions in pipeline order
STAGES = [
    ("Bootstrap", "bootstrap", "intake_complete"),
    ("Design", "design", "has_architecture"),
    ("Implement", "implement", "has_implementation"),
    ("Extract", "extract", "has_build_manifest"),
    ("Test", "test", "has_test_results"),
    ("Verify", "verify", "has_verification"),
]


def render_pipeline(pipeline_status: dict, trace_stages: list[str] | None = None):
    """Render the pipeline as a horizontal stage indicator.

    Args:
        pipeline_status: dict from data_loader.get_pipeline_status()
        trace_stages: list of stage names from current run's trace (optional)
    """
    cols = st.columns(len(STAGES))

    for i, (label, stage_key, status_key) in enumerate(STAGES):
        with cols[i]:
            has_artifact = pipeline_status.get(status_key, False)
            in_trace = trace_stages and stage_key in trace_stages if trace_stages else False

            if has_artifact:
                icon = "✅"
                color = "#27AE60"
            elif in_trace:
                icon = "🔄"
                color = "#E67E22"
            else:
                icon = "⬜"
                color = "#BDC3C7"

            st.markdown(
                f"""<div style="text-align:center; padding:12px 4px;
                    border-radius:8px; border:2px solid {color};
                    background-color:{color}15;">
                    <div style="font-size:24px;">{icon}</div>
                    <div style="font-size:13px; font-weight:600; color:#2C3E50;
                         margin-top:4px;">{label}</div>
                </div>""",
                unsafe_allow_html=True,
            )

    # Draw connecting arrows between stages (visual only)
    st.markdown(
        """<div style="display:flex; justify-content:space-between;
            padding:0 40px; margin-top:-8px; margin-bottom:16px;">"""
        + "".join(
            '<div style="flex:1; text-align:center; color:#BDC3C7; font-size:18px;">→</div>'
            for _ in range(len(STAGES) - 1)
        )
        + "</div>",
        unsafe_allow_html=True,
    )
