"""Run Pipeline page — launch the autonomous flow and monitor progress."""

import subprocess
import sys
import time

import streamlit as st

from dashboard.components.page_header import render_page_description
from dashboard.rate_limiter import check_rate_limit, get_remaining_runs
from dashboard.components.pipeline_visual import render_pipeline
from dashboard.components.trace_timeline import render_timeline
from dashboard.data_loader import (
    get_latest_run_id,
    get_pipeline_status,
    is_intake_complete,
    list_runs,
    load_evidence,
    load_pending_gate,
    load_run_status,
    load_trace,
)
from dashboard.theme import (
    BG_SURFACE,
    FONT_BODY,
    FONT_SMALL,
    PRIMARY,
    RADIUS,
    RADIUS_LG,
    SUCCESS,
    TEXT_BODY,
    TEXT_MUTED,
    TEXT_PRIMARY,
)


# ── Cost-estimate UI helpers ────────────────────────────────────────────────


def _load_estimate(project_dir):
    """Run the heuristic estimator and cache in session state."""
    if "cost_estimate" not in st.session_state:
        from engine.context import init as init_context
        from engine.cost_estimator import estimate_run, build_tiers

        init_context(str(project_dir))
        estimate = estimate_run(str(project_dir))
        tiers = build_tiers(estimate)
        st.session_state["cost_estimate"] = estimate
        st.session_state["cost_tiers"] = tiers
    return st.session_state["cost_estimate"], st.session_state["cost_tiers"]


def _render_tier_card(tier_name, tiers, estimate, descriptions):
    """Render a single tier option as a styled card."""
    from engine.cost_estimator import TierName

    tier = tiers[tier_name]
    desc = descriptions[tier_name]
    is_premium = tier_name == TierName.PREMIUM

    border_color = PRIMARY if is_premium else SUCCESS
    badge = "FULL" if is_premium else "LEAN"

    total_out = estimate.total_output_tokens(tier_name)
    total_all = estimate.total_input_tokens + total_out

    st.markdown(
        f"""<div style="border:1.5px solid {border_color}; border-radius:{RADIUS_LG};
            padding:20px; background:{border_color}08;">
            <div style="display:flex; align-items:center; gap:8px; margin-bottom:12px;">
                <span style="background:{border_color}; color:white; padding:2px 10px;
                    border-radius:12px; font-size:11px; font-weight:600;">{badge}</span>
                <span style="font-size:18px; font-weight:700; color:{TEXT_PRIMARY};">
                    {desc["label"]}</span>
            </div>
            <div style="font-size:{FONT_BODY}; color:{TEXT_BODY}; margin-bottom:16px;">
                {desc["summary"]}</div>
            <div style="display:flex; gap:24px; margin-bottom:8px;">
                <div>
                    <div style="font-size:{FONT_SMALL}; color:{TEXT_MUTED}; text-transform:uppercase;">
                        Est. Tokens</div>
                    <div style="font-size:20px; font-weight:700; color:{TEXT_PRIMARY};">
                        {total_all:,}</div>
                </div>
                <div>
                    <div style="font-size:{FONT_SMALL}; color:{TEXT_MUTED}; text-transform:uppercase;">
                        Est. Cost</div>
                    <div style="font-size:20px; font-weight:700; color:{border_color};">
                        ${tier.estimated_cost_usd:.4f}</div>
                </div>
            </div>
        </div>""",
        unsafe_allow_html=True,
    )


def _render_tier_details(tier_name, descriptions):
    """Render includes / trade-offs for a tier."""
    desc = descriptions[tier_name]
    for item in desc.get("includes", []):
        st.markdown(f"✓ {item}")
    for item in desc.get("trade_offs", []):
        st.markdown(f"⚠ {item}")


def _render_cost_estimate(project_dir):
    """Show the tier selection UI. Returns 'premium', 'mvp', or None."""
    from engine.cost_estimator import TierName, _TIER_DESCRIPTIONS

    estimate, tiers = _load_estimate(project_dir)
    descriptions = _TIER_DESCRIPTIONS

    premium = tiers[TierName.PREMIUM]
    mvp = tiers[TierName.MVP]
    savings_pct = 0
    if premium.estimated_cost_usd > 0:
        savings_pct = (
            (premium.estimated_cost_usd - mvp.estimated_cost_usd) / premium.estimated_cost_usd * 100
        )

    st.markdown(
        f"""<div style="background:{BG_SURFACE}; border-radius:{RADIUS}; padding:12px 16px;
            margin-bottom:16px; display:flex; align-items:center; gap:8px;">
            <span style="font-size:{FONT_BODY}; color:{TEXT_BODY};">
                MVP saves <strong>~{savings_pct:.0f}%</strong> vs Premium.
                Estimates are based on input size heuristics (±15%).</span>
        </div>""",
        unsafe_allow_html=True,
    )

    # Per-stage breakdown in an expander
    with st.expander("Per-stage token breakdown"):
        header_cols = st.columns([2, 3, 2, 2, 2])
        headers = ["Stage", "Model", "Input", "Out (Premium)", "Out (MVP)"]
        for col, h in zip(header_cols, headers):
            col.caption(h)

        for se in estimate.stages:
            if se.uses_llm:
                c1, c2, c3, c4, c5 = st.columns([2, 3, 2, 2, 2])
                stage_label = f"**{se.stage.title()}**"
                if se.chunked:
                    stage_label += f" ×{se.estimated_chunks}"
                c1.markdown(stage_label)
                c2.code(se.model, language=None)
                c3.markdown(f"`{se.input_tokens:,}`")
                c4.markdown(f"`{se.output_tokens_premium:,}`")
                c5.markdown(f"`{se.output_tokens_mvp:,}`")

    # Tier cards side by side
    col_a, col_b = st.columns(2)
    with col_a:
        _render_tier_card(TierName.PREMIUM, tiers, estimate, descriptions)
        _render_tier_details(TierName.PREMIUM, descriptions)
    with col_b:
        _render_tier_card(TierName.MVP, tiers, estimate, descriptions)
        _render_tier_details(TierName.MVP, descriptions)

    # Selection buttons
    st.markdown("")
    col_pa, col_pb, col_pc = st.columns([1, 1, 1])
    with col_pa:
        if st.button(
            "Start Premium",
            type="primary",
            use_container_width=True,
        ):
            return "premium"
    with col_pb:
        if st.button(
            "Start MVP",
            use_container_width=True,
        ):
            return "mvp"
    with col_pc:
        if st.button("Cancel", use_container_width=True):
            st.session_state.pop("show_tier_selection", None)
            st.session_state.pop("cost_estimate", None)
            st.session_state.pop("cost_tiers", None)
            st.rerun()

    return None


# ── Completion status (evidence-based) ───────────────────────────────────────


def _render_completion_status(project_dir):
    """Show pipeline completion status based on actual test evidence."""
    run_id = get_latest_run_id(project_dir)
    if not run_id:
        st.success("Pipeline completed.")
        return

    evidence = load_evidence(project_dir, run_id)
    if not evidence:
        st.success("Pipeline completed (no test evidence recorded).")
        return

    real_checks = [r for r in evidence if r.get("name") != "no_checks_configured"]
    if not real_checks:
        st.info(
            "Pipeline completed — no automated checks were configured. "
            "Add a `checks` section to `config.yml` to enable test execution."
        )
        return

    passed = sum(1 for r in real_checks if r.get("exit_code") == 0)
    failed = len(real_checks) - passed

    if failed == 0:
        st.success(f"Pipeline completed — all {passed} check(s) passed.")
    else:
        st.error(
            f"Pipeline completed with failures — {failed} of {len(real_checks)} check(s) failed."
        )

    with st.expander("Test Evidence Details", expanded=failed > 0):
        for r in real_checks:
            name = r.get("name", "unnamed")
            exit_code = r.get("exit_code", -1)
            icon = "✓" if exit_code == 0 else "✗"

            st.markdown(f"{icon} **{name}** — exit code `{exit_code}`")

            if exit_code != 0:
                stderr = r.get("stderr", "").strip()
                stdout = r.get("stdout", "").strip()
                diagnostic = stderr or stdout
                if diagnostic:
                    if len(diagnostic) > 1500:
                        diagnostic = diagnostic[:750] + "\n… (truncated) …\n" + diagnostic[-750:]
                    st.code(diagnostic, language=None)

        compliance = next((r for r in real_checks if r.get("name") == "contract-compliance"), None)
        if compliance:
            stdout = compliance.get("stdout", "")
            if "FAIL" in stdout:
                st.warning(
                    "Contract compliance issues detected — output does not fully match the design contract."
                )


# ── Pending-gate form (human-in-the-loop) ───────────────────────────────────


def _resume_graph(project_dir, run_id: str, thread_id: str, choice: str, rationale: str) -> dict:
    """Resume an interrupted graph with the user's decision.

    Runs in-process (not a subprocess) because langgraph is already imported
    and we have the checkpoint path. Returns the final state dict so the
    caller can decide whether the graph paused again at another gate.
    """
    import sqlite3
    from langgraph.checkpoint.sqlite import SqliteSaver
    from langgraph.types import Command
    from graph.pipeline import build_graph, _write_run_status

    checkpoint_db = st.session_state.get("checkpoint_db") or str(
        project_dir / "state" / "checkpoints.sqlite"
    )
    conn = sqlite3.connect(checkpoint_db, check_same_thread=False)
    try:
        saver = SqliteSaver(conn)
        graph = build_graph(checkpointer=saver)
        config = {"configurable": {"thread_id": thread_id}}
        result = graph.invoke(
            Command(resume={"choice": choice, "rationale": rationale}),
            config=config,
        )
        # Keep status.json in sync so the next Streamlit rerun sees the
        # updated state (another gate, error, or complete).
        result["run_id"] = run_id  # _write_run_status reads this
        _write_run_status(result, thread_id=thread_id)
        return result
    finally:
        conn.close()


def _render_pending_gate(project_dir, run_id: str) -> dict | None:
    """If a gate is waiting on human input, render the form. Return the gate dict
    (truthy) while pending so the caller can suppress auto-refresh; None otherwise."""
    status = load_run_status(project_dir, run_id)
    pending = load_pending_gate(project_dir, run_id)
    if not pending or not status or status.get("state") != "paused":
        return None

    st.divider()
    st.subheader("⏸ Human decision required")
    st.markdown(
        f"**Stage:** `{pending.get('stage', '?')}` — **Gate:** `{pending.get('gate', '?')}`"
    )
    message = pending.get("message") or ""
    if message:
        st.caption(message)

    options = pending.get("options") or []
    thread_id = status.get("thread_id") or ""
    form_key = f"gate_form_{run_id}_{pending.get('gate', '')}"

    with st.form(form_key, clear_on_submit=False):
        choice = st.radio(
            "Select an option",
            options=options,
            index=0,
            key=f"{form_key}_choice",
        )
        rationale = st.text_area(
            "Rationale (optional — recorded to the audit log)",
            key=f"{form_key}_rationale",
            height=80,
        )
        submitted = st.form_submit_button("Submit decision", type="primary")

    if submitted:
        try:
            _resume_graph(project_dir, run_id, thread_id, choice, rationale)
        except Exception as e:
            st.error(f"Resume failed: {e}")
        else:
            # Drop any lingering subprocess handle — the graph finished in-process.
            st.session_state.pop("pipeline_process", None)
            st.success(f"Resumed with choice: **{choice}**")
            st.rerun()

    return pending


# ── Main page render ────────────────────────────────────────────────────────


def render(project_dir):
    st.title("Run Pipeline")

    render_page_description(
        "Launch and monitor the autonomous build pipeline. Click Start Pipeline "
        "to see a cost estimate and choose between Premium (full output) "
        "or MVP (lean output, lower cost). Once running, the Progress "
        "section shows live stage completion and a trace timeline. "
        "Check Recent Runs at the bottom for past executions."
    )

    # ── Section A: Launch ────────────────────────────────────────────────
    st.subheader("Launch")

    intake_ok = is_intake_complete(project_dir)
    if not intake_ok:
        st.warning("Intake is incomplete. Create a project first before running the pipeline.")

    proc = st.session_state.get("pipeline_process")
    is_running = proc is not None and proc.poll() is None

    # ── Tier selection flow ─────────────────────────────────────────────
    if st.session_state.get("show_tier_selection") and not is_running:
        selected = _render_cost_estimate(project_dir)
        if selected:
            # ── Demo rate limiter ──────────────────────────────────────
            # Gate the launch: if the visitor has exhausted their session
            # budget, show a friendly message and skip the subprocess.
            if not check_rate_limit():
                st.session_state.pop("show_tier_selection", None)
                st.stop()

            # One checkpoint DB per launch — thread_id is derived inside the
            # graph so concurrent demo visitors don't collide. Persisting to
            # SQLite (not MemorySaver) is what lets the resume-after-gate
            # flow work across the subprocess → Streamlit-process boundary.
            checkpoint_db = str(
                project_dir / "state" / "checkpoints.sqlite"
            )
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "graph.pipeline",
                    "--project-dir",
                    str(project_dir),
                    "--tier",
                    selected,
                    "--checkpoint-db",
                    checkpoint_db,
                ],
                cwd=str(project_dir)
                if (project_dir / "dashboard").is_dir()
                else str(project_dir.parent),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            st.session_state["pipeline_process"] = process
            st.session_state["checkpoint_db"] = checkpoint_db
            st.session_state.pop("show_tier_selection", None)
            st.session_state.pop("cost_estimate", None)
            st.session_state.pop("cost_tiers", None)
            st.rerun()
    else:
        col_btn, col_status = st.columns([1, 2])
        with col_btn:
            start_disabled = not intake_ok or is_running
            if st.button(
                "Start Pipeline",
                type="primary",
                disabled=start_disabled,
            ):
                st.session_state["show_tier_selection"] = True
                st.session_state.pop("cost_estimate", None)
                st.session_state.pop("cost_tiers", None)
                st.rerun()

        with col_status:
            remaining = get_remaining_runs()
            if remaining == 0:
                st.warning("Demo limit reached — refresh or clone the repo for more runs.")
            elif is_running:
                st.info("Pipeline is running…")
            elif proc is not None:
                rc = proc.poll()
                if rc == 0:
                    _render_completion_status(project_dir)
                else:
                    st.error(f"Pipeline exited with code {rc}.")
                    try:
                        out = proc.stdout.read().decode(errors="replace") if proc.stdout else ""
                        if out:
                            with st.expander("Process output"):
                                st.code(out[-2000:])
                    except Exception:
                        pass

    # ── Section B0: Pending human-in-the-loop decision ──────────────────
    # When the LangGraph subprocess hits a `pause` gate, it drops
    # pending_gate.json + status.json and exits. We render a form here;
    # on submit, we resume the graph in-process via Command(resume=...).
    run_id = get_latest_run_id(project_dir)
    pending_gate = _render_pending_gate(project_dir, run_id) if run_id else None

    # ── Section B: Live Progress ─────────────────────────────────────────
    st.divider()
    st.subheader("Progress")

    if run_id:
        pipeline_status = get_pipeline_status(project_dir)
        trace_entries = load_trace(project_dir, run_id)
        evidence = load_evidence(project_dir, run_id)
        trace_stages = list(dict.fromkeys(e["task"] for e in trace_entries))

        render_pipeline(pipeline_status, trace_stages, evidence=evidence)

        if trace_entries:
            with st.expander("Trace Timeline", expanded=is_running):
                render_timeline(trace_entries)
        else:
            st.caption("Waiting for trace entries…")
    else:
        st.caption("No runs found yet.")

    # Auto-refresh while running, but stop while a gate is pending —
    # otherwise the form re-renders under the user as they're filling it in.
    if is_running and not pending_gate:
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
                f"**{rid[:12]}…** — {entries} traces · stages: {stages} · started: {started}"
            )
