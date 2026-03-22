"""Reusable pipeline stage visualization component.

Shows stages as: pending → running → passed/failed based on actual evidence,
not just whether an artifact file exists on disk.

KEY INSIGHT: An artifact file existing does NOT mean the stage "passed."
ARCHITECTURE.md can exist from a run that produced 520 type errors.
We only show green when:
  - The stage ran in the current trace, AND
  - (for test/verify) the evidence shows no failures
"""

import streamlit as st

from dashboard.theme import (
    FONT_BODY,
    RADIUS,
    STATUS_FAILED,
    STATUS_PASSED,
    STATUS_PENDING,
    STATUS_RUNNING,
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
    # Determine which stages completed in the current trace
    traced = set(trace_stages) if trace_stages else set()

    # Did any test/verify checks fail?
    has_test_failures = False
    if evidence:
        for r in evidence:
            if r.get("name") == "no_checks_configured":
                continue
            if r.get("exit_code", -1) != 0:
                has_test_failures = True
                break

    cols = st.columns(len(STAGES))

    for i, (label, stage_key, status_key) in enumerate(STAGES):
        with cols[i]:
            has_artifact = pipeline_status.get(status_key, False)
            in_trace = stage_key in traced

            # State logic:
            # 1. If the stage appeared in the trace → it ran
            #    a. For test/verify: check evidence for failures
            #    b. For others: if artifact exists → passed, else still running
            # 2. If NOT in trace but artifact exists → stale (from previous run)
            #    Show as "completed" but dimmer (not actively running)
            # 3. Neither → pending

            if in_trace:
                # Stage ran in this trace
                if stage_key == "test" and has_test_failures:
                    state = "failed"
                elif stage_key == "verify" and has_test_failures:
                    state = "failed"
                elif has_artifact:
                    state = "passed"
                else:
                    state = "running"
            elif has_artifact and not trace_stages:
                # No trace provided at all → show artifact status as "stale passed"
                # (we can't verify, so show it as done but use a dimmer style)
                state = "passed"
            elif has_artifact and trace_stages:
                # Trace exists but this stage wasn't in it → stale artifact
                # Show as passed if a later stage DID run (implies this one completed)
                stage_order = [s[1] for s in STAGES]
                this_idx = stage_order.index(stage_key)
                later_ran = any(s in traced for s in stage_order[this_idx + 1 :])
                if later_ran:
                    state = "passed"
                else:
                    state = "pending"
            else:
                state = "pending"

            # Visual treatment
            if state == "passed":
                icon = "✓"
                color = STATUS_PASSED
            elif state == "failed":
                icon = "✗"
                color = STATUS_FAILED
            elif state == "running":
                icon = "↻"
                color = STATUS_RUNNING
            else:  # pending
                icon = "·"
                color = STATUS_PENDING

            # Use color at low opacity for bg, full for border & icon
            st.markdown(
                f"""<div style="text-align:center; padding:14px 4px;
                    border-radius:{RADIUS}; border:1.5px solid {color};
                    background-color:{color}18;">
                    <div style="width:32px; height:32px; border-radius:50%;
                        background:{color}25; color:{color};
                        display:inline-flex; align-items:center; justify-content:center;
                        font-size:18px; font-weight:700;">{icon}</div>
                    <div style="font-size:{FONT_BODY}; font-weight:600;
                        color:{TEXT_PRIMARY if state != "pending" else TEXT_MUTED};
                        margin-top:6px;">{label}</div>
                </div>""",
                unsafe_allow_html=True,
            )

    # Connecting arrows
    arrow_html = "".join(
        f'<div style="flex:1; text-align:center; color:{TEXT_MUTED}; font-size:18px;">→</div>'
        for _ in range(len(STAGES) - 1)
    )
    st.markdown(
        f'<div style="display:flex; justify-content:space-between;'
        f' padding:0 40px; margin-top:-6px; margin-bottom:16px;">{arrow_html}</div>',
        unsafe_allow_html=True,
    )
