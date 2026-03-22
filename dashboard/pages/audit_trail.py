"""Audit Trail — hash chain visualization and integrity verification."""

import json

import streamlit as st

from dashboard.components.page_header import render_page_description
from dashboard.data_loader import (
    list_runs,
    load_trace,
    verify_trace_integrity,
)
from dashboard.theme import (
    FONT_BODY,
    FONT_SMALL,
    MUTED,
    RADIUS,
    STATUS_FAILED,
    STATUS_PASSED,
    TEXT_MUTED,
    TEXT_PRIMARY,
)


def render(project_dir):
    st.title("Audit Trail")

    render_page_description(
        "Verify the integrity of any pipeline run. Every trace entry is cryptographically "
        "chained to the previous one via SHA-256 — if any entry is modified, inserted, or "
        "deleted, the chain breaks. The Integrity Verification section shows "
        "a pass/fail check. Below, the Hash Chain Visualization displays "
        "each entry's hash linked to the previous one. "
        "Use Export at the bottom to download the raw trace or audit report."
    )

    runs = list_runs(project_dir)
    if not runs:
        st.info("No runs to audit.")
        return

    run_ids = [r["run_id"] for r in runs]
    selected = st.selectbox("Select Run to Audit", run_ids)

    st.divider()

    # Integrity verification
    st.subheader("Chain Integrity Verification")
    is_valid, errors = verify_trace_integrity(project_dir, selected)

    if is_valid:
        st.success("INTEGRITY VERIFIED — Hash chain is intact. No tampering detected.")
    else:
        st.error("INTEGRITY FAILURE — Hash chain is broken. Possible tampering detected.")
        for err in errors:
            st.error(err)

    st.divider()

    # Hash chain visualization
    st.subheader("Hash Chain Visualization")
    entries = load_trace(project_dir, selected)

    if not entries:
        st.info("No trace entries.")
        return

    for i, entry in enumerate(entries):
        seq = entry.get("seq", i)
        task = entry.get("task", "unknown")
        prev_hash = entry.get("prev_hash", "?")
        entry_hash = entry.get("entry_hash", "?")
        ts = entry.get("timestamp", "")

        is_genesis = prev_hash == "0" * 64

        # Check chain link
        if i > 0:
            expected_prev = entries[i - 1].get("entry_hash", "")
            chain_ok = prev_hash == expected_prev
        else:
            chain_ok = is_genesis

        chain_icon = "🔗" if chain_ok else "💔"
        chain_color = STATUS_PASSED if chain_ok else STATUS_FAILED

        st.markdown(
            f"""<div style="border:1px solid {chain_color}; border-radius:{RADIUS};
                    padding:12px; margin-bottom:8px; background:{chain_color}12;">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <div>
                        <span style="font-size:{FONT_BODY}; font-weight:700; color:{TEXT_PRIMARY};">
                            {chain_icon} Entry {seq}: {task.upper()}
                        </span>
                        <span style="color:{TEXT_MUTED}; margin-left:12px; font-size:{FONT_SMALL};">
                            {ts[:19] if ts else ""}
                        </span>
                    </div>
                </div>
                <div style="font-family:monospace; font-size:11px; margin-top:8px; color:{TEXT_MUTED};">
                    prev: {prev_hash[:32]}…
                </div>
                <div style="font-family:monospace; font-size:11px; color:{TEXT_PRIMARY}; font-weight:600;">
                    hash: {entry_hash[:32]}…
                </div>
            </div>""",
            unsafe_allow_html=True,
        )

        # Draw chain arrow between entries
        if i < len(entries) - 1:
            st.markdown(
                f'<div style="text-align:center; color:{MUTED}; font-size:18px;'
                f' margin:-4px 0;">↓</div>',
                unsafe_allow_html=True,
            )

    st.divider()

    # Raw trace export
    st.subheader("Export")
    col1, col2 = st.columns(2)
    with col1:
        trace_json = json.dumps(entries, indent=2)
        st.download_button(
            "Download Trace (JSON)",
            data=trace_json,
            file_name=f"trace_{selected}.json",
            mime="application/json",
        )
    with col2:
        audit_report = {
            "run_id": selected,
            "integrity_valid": is_valid,
            "integrity_errors": errors,
            "entry_count": len(entries),
            "chain_hashes": [
                {
                    "seq": e.get("seq"),
                    "task": e.get("task"),
                    "entry_hash": e.get("entry_hash"),
                }
                for e in entries
            ],
        }
        st.download_button(
            "Download Audit Report (JSON)",
            data=json.dumps(audit_report, indent=2),
            file_name=f"audit_{selected}.json",
            mime="application/json",
        )
