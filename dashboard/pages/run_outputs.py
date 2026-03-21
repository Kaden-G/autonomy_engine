"""Run Outputs — a stage-by-stage view of every artifact a pipeline run produced.

Unlike the Pipeline Explorer (which explains what *would* happen), this page shows
what *actually* happened in a specific run.  Each stage lists its real output files
with status indicators.  Click any artifact to view its contents inline.

Designed for PMs, ISSOs, and auditors who need to inspect pipeline deliverables
without navigating the filesystem.
"""

import json
from pathlib import Path

import streamlit as st

from dashboard.data_loader import (
    get_intake_status,
    get_pipeline_status,
    get_state_dir,
    list_runs,
    load_artifact,
    load_evidence,
    load_trace,
)
from dashboard.theme import (
    BG_SURFACE,
    BORDER,
    FONT_BODY,
    FONT_SMALL,
    INFO,
    MUTED,
    RADIUS,
    STAGE_COLORS,
    STATUS_FAILED,
    STATUS_PASSED,
    STATUS_PENDING,
    SUCCESS,
    TEXT_BODY,
    TEXT_MUTED,
    TEXT_PRIMARY,
    section_description,
)

# ── Stage output definitions ──────────────────────────────────────────────
# Each stage lists the artifacts it can produce.
# Tuples: (display_name, relative_path, description, file_type)
#   relative_path: from project root's state/ dir, or special prefixes:
#     "run:" → state/runs/<run_id>/...
#     "evidence:" → load evidence records
#   file_type: "md", "json", "jsonl", "yaml", "text", "evidence"

STAGE_OUTPUTS = {
    "intake": {
        "label": "Intake",
        "icon": "📝",
        "color": "#94A3B8",
        "description": "Your project description, captured as structured artifacts",
        "files": [
            ("Project Spec", "inputs/project_spec.yml",
             "The machine-readable source of truth — all requirements, constraints, and goals in one file",
             "yaml"),
            ("Requirements", "inputs/REQUIREMENTS.md",
             "Functional and non-functional requirements rendered for human review",
             "md"),
            ("Constraints", "inputs/CONSTRAINTS.md",
             "Technical constraints and boundaries — languages, frameworks, performance targets",
             "md"),
            ("Non-Goals", "inputs/NON_GOALS.md",
             "Explicit out-of-scope items so the AI knows where to stop",
             "md"),
            ("Acceptance Criteria", "inputs/ACCEPTANCE_CRITERIA.md",
             "Measurable criteria for deciding whether the generated project meets the spec",
             "md"),
        ],
    },
    "bootstrap": {
        "label": "Bootstrap",
        "icon": "🔧",
        "color": STAGE_COLORS["bootstrap"],
        "description": "Run initialization — audit trail, config snapshot, folder structure",
        "files": [
            ("Audit Trace", "run:trace.jsonl",
             "The tamper-evident audit log — every action, model call, and decision recorded with HMAC signatures",
             "jsonl"),
            ("Config Snapshot", "run:config_snapshot.yml",
             "A frozen copy of engine settings at the moment this run started — ensures reproducibility",
             "yaml"),
        ],
    },
    "design": {
        "label": "Design",
        "icon": "📐",
        "color": STAGE_COLORS["design"],
        "description": "AI-generated architecture and the binding contract for implementation",
        "files": [
            ("Architecture", "designs/ARCHITECTURE.md",
             "Human-readable design document — system overview, component breakdown, data flow diagrams",
             "md"),
            ("Design Contract", "designs/DESIGN_CONTRACT.json",
             "The binding blueprint — exact file lists, shared types, dependency maps, per-component budgets",
             "json"),
            ("Design Decision", "run:decisions/design.json",
             "The human gate decision record — who approved the design and when",
             "json"),
        ],
    },
    "implement": {
        "label": "Implement",
        "icon": "⚙️",
        "color": STAGE_COLORS["implement"],
        "description": "AI-generated source code, produced chunk-by-chunk from the design contract",
        "files": [
            ("Implementation", "implementations/IMPLEMENTATION.md",
             "The raw AI output — fenced code blocks with file paths, ready for extraction",
             "md"),
            ("File Manifest", "implementations/FILE_MANIFEST.json",
             "Index of every file the AI intended to produce — names, components, and dependencies",
             "json"),
        ],
    },
    "extract": {
        "label": "Extract",
        "icon": "📦",
        "color": STAGE_COLORS["extract"],
        "description": "Code blocks parsed from AI output into real files on disk",
        "files": [
            ("Build Manifest", "build/MANIFEST.md",
             "Inventory of every extracted file — name, size in bytes, and line count",
             "md"),
        ],
    },
    "test": {
        "label": "Test",
        "icon": "🧪",
        "color": STAGE_COLORS["test"],
        "description": "Automated quality checks run in an isolated sandbox",
        "files": [
            ("Test Results", "tests/TEST_RESULTS.md",
             "Human-readable summary — pass/fail per check with key output excerpts",
             "md"),
            ("Evidence Records", "evidence:",
             "One structured record per check — command, exit code, full output, timing, environment",
             "evidence"),
        ],
    },
    "verify": {
        "label": "Verify",
        "icon": "✅",
        "color": STAGE_COLORS["verify"],
        "description": "Final go/no-go verdict with root-cause analysis for failures",
        "files": [
            ("Verification Report", "tests/VERIFICATION.md",
             "The final verdict — ACCEPTED or REJECTED with rationale and per-category analysis",
             "md"),
            ("Verify Decision", "run:decisions/verify.json",
             "The go/no-go decision captured in the audit trail for this run",
             "json"),
        ],
    },
}

STAGE_ORDER = ["intake", "bootstrap", "design", "implement", "extract", "test", "verify"]


def render(project_dir):
    """Render the Run Outputs page."""
    st.markdown(
        f'<h1 style="font-size:28px; color:{TEXT_PRIMARY}; margin-bottom:4px;">'
        f'📂 Run Outputs</h1>',
        unsafe_allow_html=True,
    )

    st.markdown(
        section_description(
            "Every artifact produced by a pipeline run, organized by stage.  "
            "Select a run, then click any output to view its contents.  "
            "Intake artifacts are shared across all runs; everything else is run-specific."
        ),
        unsafe_allow_html=True,
    )

    # ── Run selector ───────────────────────────────────────────────────────
    runs = list_runs(project_dir)
    if not runs:
        st.info("No pipeline runs found yet. Run the pipeline first to see outputs here.")
        # Still show intake if it exists
        _render_intake_only(project_dir)
        return

    run_ids = [r["run_id"] for r in runs]
    selected_run = st.selectbox(
        "Select a run to inspect",
        run_ids,
        index=0,
        format_func=lambda rid: f"Run {rid} — {_run_summary(runs, rid)}",
        label_visibility="visible",
    )

    st.divider()

    # ── Load run-level data ────────────────────────────────────────────────
    state_dir = get_state_dir(project_dir)
    run_dir = state_dir / "runs" / selected_run
    evidence_records = load_evidence(project_dir, selected_run)

    # ── Render each stage ──────────────────────────────────────────────────
    for stage_key in STAGE_ORDER:
        stage = STAGE_OUTPUTS[stage_key]
        _render_stage_section(
            project_dir, state_dir, run_dir, stage_key, stage,
            evidence_records if stage_key == "test" else None,
        )


def _render_intake_only(project_dir):
    """Show intake artifacts even when no runs exist yet."""
    state_dir = get_state_dir(project_dir)
    stage = STAGE_OUTPUTS["intake"]
    intake_status = get_intake_status(project_dir)
    if any(intake_status.values()):
        st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
        _render_stage_section(project_dir, state_dir, None, "intake", stage, None)


def _run_summary(runs: list[dict], run_id: str) -> str:
    """Build a short summary string for the run selector dropdown."""
    for r in runs:
        if r["run_id"] == run_id:
            stages = r.get("stages", [])
            entry_count = r.get("trace_entries", 0)
            evidence_count = r.get("evidence_count", 0)
            parts = []
            if stages:
                parts.append(f"{len(stages)} stages")
            if evidence_count:
                parts.append(f"{evidence_count} checks")
            parts.append(f"{entry_count} trace entries")
            return ", ".join(parts)
    return ""


def _render_stage_section(
    project_dir: Path,
    state_dir: Path,
    run_dir: Path | None,
    stage_key: str,
    stage: dict,
    evidence_records: list[dict] | None,
):
    """Render a stage header and its output file cards."""
    color = stage["color"]
    files = stage["files"]

    # Count how many files actually exist
    existing = 0
    for _, path, _, ftype in files:
        if ftype == "evidence":
            if evidence_records:
                existing += 1
        elif _resolve_path(state_dir, run_dir, path) is not None:
            existing += 1

    total = len(files)
    count_label = f"{existing}/{total}" if total > 0 else "0"

    # Stage status indicator
    if existing == total and total > 0:
        status_dot = f'<span style="color:{SUCCESS};">●</span>'
    elif existing > 0:
        status_dot = f'<span style="color:{MUTED};">◐</span>'
    else:
        status_dot = f'<span style="color:{MUTED};">○</span>'

    st.markdown(
        f"""<div style="
            display:flex; align-items:center; gap:10px;
            margin-top:24px; margin-bottom:4px;
        ">
            <span style="font-size:22px;">{stage['icon']}</span>
            <span style="font-size:17px; font-weight:600; color:{TEXT_PRIMARY};">
                {stage['label']}
            </span>
            {status_dot}
            <span style="font-size:12px; color:{TEXT_MUTED};">
                {count_label} artifacts
            </span>
        </div>
        <div style="font-size:13px; color:{TEXT_BODY}; margin-bottom:12px; margin-left:34px;">
            {stage['description']}
        </div>""",
        unsafe_allow_html=True,
    )

    # Render each file as a clickable expander
    for display_name, path, description, ftype in files:
        if ftype == "evidence":
            _render_evidence_section(evidence_records, color)
        else:
            _render_file_card(
                project_dir, state_dir, run_dir,
                display_name, path, description, ftype, color,
            )


def _resolve_path(state_dir: Path, run_dir: Path | None, path_spec: str) -> Path | None:
    """Resolve a path spec to an actual filesystem path, or None if it doesn't exist.

    Path specs:
      "run:trace.jsonl"  → <run_dir>/trace.jsonl
      "inputs/foo.md"    → <state_dir>/inputs/foo.md
      "evidence:"        → special (handled separately)
    """
    if path_spec.startswith("evidence:"):
        return None  # handled separately

    if path_spec.startswith("run:"):
        if run_dir is None:
            return None
        rel = path_spec[4:]  # strip "run:"
        full = run_dir / rel
    else:
        full = state_dir / path_spec

    return full if full.exists() else None


def _render_file_card(
    project_dir: Path,
    state_dir: Path,
    run_dir: Path | None,
    display_name: str,
    path_spec: str,
    description: str,
    ftype: str,
    stage_color: str,
):
    """Render a single artifact card with an expander to view contents."""
    resolved = _resolve_path(state_dir, run_dir, path_spec)
    exists = resolved is not None

    # Build the display path for the user (relative, human-readable)
    if path_spec.startswith("run:"):
        run_label = run_dir.name if run_dir else "<run-id>"
        display_path = f"state/runs/{run_label}/{path_spec[4:]}"
    else:
        display_path = f"state/{path_spec}"

    # Status indicator
    if exists:
        status = f'<span style="color:{SUCCESS}; font-size:11px; font-weight:600;">EXISTS</span>'
    else:
        status = f'<span style="color:{MUTED}; font-size:11px; font-weight:600;">NOT YET GENERATED</span>'

    if exists:
        expander_label = f"📄 {display_name}  ·  {display_path}"
        with st.expander(expander_label, expanded=False):
            # File path chip
            st.markdown(
                f'<div style="font-size:11px; color:{MUTED}; font-family:monospace; '
                f'margin-bottom:8px;">📂 {display_path}</div>',
                unsafe_allow_html=True,
            )

            content = resolved.read_text()

            if ftype == "json":
                try:
                    parsed = json.loads(content)
                    st.code(json.dumps(parsed, indent=2), language="json")
                except json.JSONDecodeError:
                    st.code(content, language="json")
            elif ftype == "yaml":
                st.code(content, language="yaml")
            elif ftype == "jsonl":
                _render_jsonl(content)
            elif ftype == "md":
                # Use tabs: rendered + raw
                tab_render, tab_raw = st.tabs(["Rendered", "Raw Markdown"])
                with tab_render:
                    st.markdown(content)
                with tab_raw:
                    st.code(content, language="markdown")
            else:
                st.code(content)
    else:
        # Not generated — show as a muted, non-expandable card
        st.markdown(
            f"""<div style="
                padding:8px 14px; margin-bottom:6px;
                background:{BG_SURFACE}; border:1px solid {BORDER};
                border-left:3px solid {MUTED}; border-radius:0 {RADIUS} {RADIUS} 0;
                opacity:0.6;
            ">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <span style="font-size:13px; color:{TEXT_MUTED};">
                        📄 {display_name}
                    </span>
                    {status}
                </div>
                <div style="font-size:11px; color:{MUTED}; font-family:monospace; margin-top:2px;">
                    📂 {display_path}
                </div>
                <div style="font-size:12px; color:{TEXT_MUTED}; margin-top:4px;">
                    {description}
                </div>
            </div>""",
            unsafe_allow_html=True,
        )


def _render_jsonl(content: str):
    """Render a JSONL file as a sequence of formatted entries."""
    lines = [l for l in content.strip().splitlines() if l.strip()]
    st.markdown(
        f'<div style="font-size:12px; color:{TEXT_MUTED}; margin-bottom:8px;">'
        f'{len(lines)} entries</div>',
        unsafe_allow_html=True,
    )
    # Show first 20 entries, with a note if truncated
    display_lines = lines[:20]
    for i, line in enumerate(display_lines):
        try:
            entry = json.loads(line)
            task = entry.get("task", "?")
            action = entry.get("action", "?")
            ts = entry.get("timestamp", "")[:19]  # trim to seconds
            model = entry.get("model", "")

            label_parts = [f"#{i}", task, action]
            if model:
                label_parts.append(f"model={model}")
            if ts:
                label_parts.append(ts)

            with st.expander(" · ".join(label_parts), expanded=False):
                st.code(json.dumps(entry, indent=2), language="json")
        except json.JSONDecodeError:
            st.code(line, language="json")

    if len(lines) > 20:
        st.caption(f"Showing 20 of {len(lines)} entries. View the full file for all entries.")


def _render_evidence_section(evidence_records: list[dict] | None, stage_color: str):
    """Render evidence records inline — each check as its own expandable card."""
    if not evidence_records:
        st.markdown(
            f"""<div style="
                padding:8px 14px; margin-bottom:6px;
                background:{BG_SURFACE}; border:1px solid {BORDER};
                border-left:3px solid {MUTED}; border-radius:0 {RADIUS} {RADIUS} 0;
                opacity:0.6;
            ">
                <div style="font-size:13px; color:{TEXT_MUTED};">
                    🧪 Evidence Records
                </div>
                <div style="font-size:11px; color:{MUTED}; margin-top:2px;">
                    No evidence records — tests haven't run for this run yet
                </div>
            </div>""",
            unsafe_allow_html=True,
        )
        return

    passed = sum(1 for r in evidence_records
                 if r.get("exit_code") == 0 and r.get("name") != "no_checks_configured")
    failed = sum(1 for r in evidence_records
                 if r.get("exit_code", 0) != 0 and r.get("name") != "no_checks_configured")

    st.markdown(
        f'<div style="font-size:12px; color:{TEXT_MUTED}; margin-bottom:6px; margin-left:4px;">'
        f'🧪 <strong>{len(evidence_records)}</strong> checks — '
        f'<span style="color:{SUCCESS};">{passed} passed</span>'
        f'{f", <span style=&quot;color:{STATUS_FAILED};&quot;>{failed} failed</span>" if failed else ""}'
        f'</div>',
        unsafe_allow_html=True,
    )

    for record in evidence_records:
        name = record.get("name", "unknown")
        exit_code = record.get("exit_code", -1)
        command = record.get("command", "")
        stdout = record.get("stdout", "")
        stderr = record.get("stderr", "")
        duration = record.get("duration_seconds", 0)

        if name == "no_checks_configured":
            continue

        # Status
        if exit_code == 0:
            icon = "✅"
            status_label = "PASS"
            border_color = SUCCESS
        else:
            icon = "❌"
            status_label = f"FAIL (exit {exit_code})"
            border_color = STATUS_FAILED

        with st.expander(f"{icon} {name}  ·  {status_label}", expanded=False):
            # Command
            st.markdown(
                f'<div style="font-size:11px; color:{MUTED}; margin-bottom:4px;">Command:</div>',
                unsafe_allow_html=True,
            )
            st.code(command, language="bash")

            # Duration
            if duration:
                st.markdown(
                    f'<div style="font-size:11px; color:{TEXT_MUTED};">Duration: {duration:.1f}s</div>',
                    unsafe_allow_html=True,
                )

            # Output
            output = stdout or stderr
            if output:
                st.markdown(
                    f'<div style="font-size:11px; color:{MUTED}; margin-top:8px;">Output:</div>',
                    unsafe_allow_html=True,
                )
                # Truncate very long output
                if len(output) > 5000:
                    st.code(output[:5000], language="text")
                    st.caption(f"Output truncated — {len(output):,} characters total")
                else:
                    st.code(output, language="text")

            # Full JSON
            with st.expander("View raw evidence record", expanded=False):
                st.code(json.dumps(record, indent=2, default=str), language="json")
