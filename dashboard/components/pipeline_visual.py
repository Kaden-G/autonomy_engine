"""Reusable pipeline stage visualization component.

Shows stages as: pending → running → passed/failed based on actual evidence,
not just whether an artifact file exists.
"""

import streamlit as st

from dashboard.theme import (
    BG_SURFACE,
    BORDER,
    FONT_BODY,
    FONT_SMALL,
    RADIUS,
    STATUS_FAILED,
    STATUS_PASSED,
    STATUS_PENDING,
    STATUS_RUNNING,
    TEXT_BODY,
    TEXT_MUTED,
    TEXT_PRIMARY,
)


# Stage definitions: (display_label, trace_key, artifact_status_key)
STAGES = [
    ("Bootstrap", "bootstrap", "intake_complete"),
    ("Design", "design", "has_architecture"),
    ("Implement", "implement", "has_implementation"),
    ("Extract", "extract", "has_build_manifest"),
    ("Test", "test", "has_test_results"),
    ("Verify", "verify", "has_verification"),
]


def render_pipeline(
    pipeline_status: dict,
    trace_stages: list[str] | None = None,
    evidence: list[dict] | None = None,
):
    """Render the pipeline as a horizontal stage indicator.

    Args:
        pipeline_status: dict from data_loader.get_pipeline_status()
        trace_stages: list of stage names from current run's trace (optional)
        evidence: list of evidence records from the run (optional).
            When provided, Test/Verify stages will show red if checks failed.
    """
    # Pre-compute whether the test/verify stages have failures
    has_test_failures = False
    has_verify_failure = False
    if evidence:
        for r in evidence:
            if r.get("name") == "no_checks_configured":
                continue
            if r.get("exit_code", -1) != 0:
                has_test_failures = True
        # Check verification specifically
        has_verify_failure = has_test_failures  # verify fails if tests fail

    cols = st.columns(len(STAGES))

    for i, (label, stage_key, status_key) in enumerate(STAGES):
        with cols[i]:
            has_artifact = pipeline_status.get(status_key, False)
            in_trace = trace_stages and stage_key in trace_stages if trace_stages else False

            # Determine state: passed / failed / running / pending
            if has_artifact:
                # Artifact exists — but did it actually pass?
                if stage_key == "test" and has_test_failures:
                    state = "failed"
                elif stage_key == "verify" and has_verify_failure:
                    state = "failed"
                else:
                    state = "passed"
            elif in_trace:
                state = "running"
            else:
                state = "pending"

            # Map state to visual treatment
            if state == "passed":
                icon = "✓"
                color = STATUS_PASSED
                icon_bg = STATUS_PASSED
                text_color = TEXT_PRIMARY
            elif state == "failed":
                icon = "✗"
                color = STATUS_FAILED
                icon_bg = STATUS_FAILED
                text_color = TEXT_PRIMARY
            elif state == "running":
                icon = "↻"
                color = STATUS_RUNNING
                icon_bg = STATUS_RUNNING
                text_color = TEXT_PRIMARY
            else:
                icon = "·"
                color = STATUS_PENDING
                icon_bg = STATUS_PENDING
                text_color = TEXT_MUTED

            st.markdown(
                f"""<div style="text-align:center; padding:14px 4px;
                    border-radius:{RADIUS}; border:1.5px solid {color};
                    background-color:{color}08;">
                    <div style="width:32px; height:32px; border-radius:50%;
                        background:{icon_bg}18; color:{icon_bg};
                        display:inline-flex; align-items:center; justify-content:center;
                        font-size:18px; font-weight:700;">{icon}</div>
                    <div style="font-size:{FONT_BODY}; font-weight:600; color:{text_color};
                         margin-top:6px;">{label}</div>
                </div>""",
                unsafe_allow_html=True,
            )

    # Connecting arrows
    arrow_html = "".join(
        f'<div style="flex:1; text-align:center; color:{STATUS_PENDING}; font-size:18px;">→</div>'
        for _ in range(len(STAGES) - 1)
    )
    st.markdown(
        f'<div style="display:flex; justify-content:space-between;'
        f' padding:0 40px; margin-top:-6px; margin-bottom:16px;">{arrow_html}</div>',
        unsafe_allow_html=True,
    )
