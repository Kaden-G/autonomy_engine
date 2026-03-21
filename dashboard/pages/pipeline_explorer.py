"""Pipeline Explorer — an interactive visual map of what the engine does, step by step.

This page is designed for people who want to understand the pipeline without reading
code.  It shows each stage as a node in a linear flow, with expandable cards that
explain what goes in, what comes out, and why it matters.  Think of it as the
"user manual as a dashboard page."

No live run data is required — this page is purely educational and always available.
When run data *is* available, it overlays real outputs and status onto the map.
"""

import streamlit as st

from dashboard.theme import (
    BG_SURFACE,
    BG_SURFACE_DARK,
    BORDER,
    FONT_BODY,
    FONT_H3,
    FONT_SMALL,
    INFO,
    MUTED,
    PRIMARY,
    RADIUS,
    RADIUS_LG,
    STAGE_COLORS,
    SUCCESS,
    TEXT_BODY,
    TEXT_MUTED,
    TEXT_PRIMARY,
    WARNING,
    section_description,
)

# ── Stage definitions ──────────────────────────────────────────────────────
# Each stage has: key, label, color, icon, plain-english summary,
# inputs, outputs (with descriptions), and a "why it matters" blurb.

PIPELINE_STAGES = [
    {
        "key": "intake",
        "label": "Intake",
        "icon": "📝",
        "color": "#94A3B8",  # Slate — human-driven, pre-pipeline
        "summary": (
            "You describe what you want to build.  The engine captures your "
            "requirements, goals, non-goals, and tech preferences into a "
            "structured spec that machines can work with."
        ),
        "who": "You (human)",
        "inputs": [
            ("Your idea", "A project description — what it does, who it's for, what's in and out of scope"),
        ],
        "outputs": [
            ("project_spec.yml", "Your requirements in a machine-readable format — the single source of truth for the entire pipeline"),
            ("REQUIREMENTS.md", "A human-friendly rendering of the spec, useful for reviews and stakeholder alignment"),
            ("GOALS.md", "Explicit goals and non-goals so the AI knows where to stop"),
            ("TECH_DECISIONS.md", "Locked-in technology choices (language, framework, database) with rationale"),
            ("SYSTEM_PROMPT.md", "The tailored instructions the AI will receive — shaped by your spec, not generic"),
        ],
        "why": (
            "Without a structured spec, the AI would interpret your description "
            "differently every time.  The intake step eliminates ambiguity before "
            "any code is generated, saving time and tokens downstream."
        ),
    },
    {
        "key": "bootstrap",
        "label": "Bootstrap",
        "icon": "🔧",
        "color": STAGE_COLORS["bootstrap"],
        "summary": (
            "A sanity check before the engine commits resources.  Validates that "
            "intake is complete, initializes the audit trail, and creates the run "
            "folder structure."
        ),
        "who": "Engine (automated)",
        "inputs": [
            ("Intake artifacts", "The five files produced during intake — all must be present"),
        ],
        "outputs": [
            ("Run ID", "A unique identifier for this pipeline execution (timestamp-based)"),
            ("trace.jsonl", "The audit log — every subsequent action is recorded here with a tamper-evident signature"),
            ("HMAC key", "A one-time cryptographic signing key for this run's audit chain (stored securely, not in the log)"),
        ],
        "why": (
            "Starting the audit trail here means even the earliest pipeline actions "
            "are recorded.  If bootstrap fails, nothing was wasted — no AI calls "
            "were made and no tokens were spent."
        ),
    },
    {
        "key": "design",
        "label": "Design",
        "icon": "📐",
        "color": STAGE_COLORS["design"],
        "summary": (
            "The AI reads your spec and creates a software architecture — then locks "
            "it into a binding contract that the implementation stage must follow."
        ),
        "who": "AI + human approval gate",
        "inputs": [
            ("Project spec", "Your validated requirements from intake"),
            ("Tier context", "Budget limits (MVP or Premium) that constrain the design's scope"),
        ],
        "outputs": [
            ("ARCHITECTURE.md", "A human-readable design document — system overview, component breakdown, data flow"),
            ("DESIGN_CONTRACT.json", "The binding blueprint — exact file lists, shared data types, dependency maps, and per-component budgets"),
            ("Decision record", "If a human gate is configured, the approval/redirect decision is recorded in the audit trail"),
        ],
        "why": (
            "The design contract is the engine's primary defense against AI drift.  "
            "Without it, the AI might forget decisions it made on page 1 by the time "
            "it's writing page 10.  The contract forces consistency across all files."
        ),
    },
    {
        "key": "implement",
        "label": "Implement",
        "icon": "⚙️",
        "color": STAGE_COLORS["implement"],
        "summary": (
            "The AI writes code, guided by the design contract.  For larger projects, "
            "code is generated in chunks — each chunk receives the contract's shared "
            "types and dependency rules so cross-file consistency is maintained."
        ),
        "who": "AI (automated, contract-guided)",
        "inputs": [
            ("DESIGN_CONTRACT.json", "The binding blueprint — tells the AI exactly which files to produce and how they relate"),
            ("Canonical types", "Shared data structures injected verbatim into each chunk prompt, preventing the AI from inventing conflicting versions"),
        ],
        "outputs": [
            ("Raw AI output", "The full text response from the AI, containing code blocks with file paths"),
            ("Token usage", "Actual tokens consumed vs. the pre-run estimate — tracked per stage for cost visibility"),
        ],
        "why": (
            "Chunk-by-chunk generation with contract injection is what makes large projects "
            "feasible.  Each chunk is self-contained but consistent with all others because "
            "they share the same type definitions and dependency rules."
        ),
    },
    {
        "key": "extract",
        "label": "Extract",
        "icon": "📦",
        "color": STAGE_COLORS["extract"],
        "summary": (
            "Turns the AI's text output into real files on disk.  Parses code blocks, "
            "validates paths, and writes a standalone project folder.  No AI involved — "
            "this is pure parsing with safety limits."
        ),
        "who": "Engine (automated, no AI)",
        "inputs": [
            ("Raw AI output", "The text containing fenced code blocks with file path headers"),
        ],
        "outputs": [
            ("Project folder", "A complete, standalone directory with all generated source files"),
            ("MANIFEST.md", "An inventory of every extracted file — name, size, and line count"),
            ("Circuit breaker log", "If the output exceeded safety limits (80 files/750KB for MVP, 250 files/5MB for Premium), extraction halts and logs why"),
        ],
        "why": (
            "Path traversal protection happens here — the engine rejects any file path "
            "that tries to escape the project directory (like ../../etc/passwd).  The "
            "circuit breaker prevents runaway AI output from filling your disk."
        ),
    },
    {
        "key": "test",
        "label": "Test",
        "icon": "🧪",
        "color": STAGE_COLORS["test"],
        "summary": (
            "Runs automated quality checks against the extracted project in an isolated "
            "sandbox.  Checks are auto-detected based on project type — you don't need "
            "to configure them."
        ),
        "who": "Engine (automated, sandboxed)",
        "inputs": [
            ("Project folder", "The extracted source code"),
            ("DESIGN_CONTRACT.json", "Used for contract compliance verification"),
        ],
        "outputs": [
            ("Evidence records", "One structured JSON file per check — command run, exit code, full output, timestamps, and environment metadata"),
            ("Contract compliance report", "Missing files, extra files, budget violations, and type integrity results"),
            ("Sandbox metadata", "Python/Node version, installed packages, virtualenv cache status"),
        ],
        "why": (
            "Evidence records are the 'test receipts' — they provide objective, machine-readable "
            "proof of what passed and what failed.  The verification stage, the dashboard, and "
            "audit exports all reference these records."
        ),
    },
    {
        "key": "verify",
        "label": "Verify",
        "icon": "✅",
        "color": STAGE_COLORS["verify"],
        "summary": (
            "The final go/no-go decision.  Analyzes all test evidence and either accepts "
            "the build, rejects it with root-cause analysis, or flags it for human review."
        ),
        "who": "AI or rule-based (configurable)",
        "inputs": [
            ("Evidence records", "All test results from the previous stage"),
            ("Structural classification", "Failures categorized by type: imports, types, lint, build, tests, contract compliance"),
        ],
        "outputs": [
            ("Verification verdict", "ACCEPTED or REJECTED with confidence score and rationale"),
            ("Root-cause analysis", "For rejections: specific diagnosis of what went wrong and which files are affected"),
            ("Audit bundle", "A complete, exportable archive of the entire run — trace, evidence, decisions, config snapshot"),
        ],
        "why": (
            "Three verification modes let you balance thoroughness against cost: "
            "'always_llm' (AI analyzes every run), 'auto' (AI only when results are ambiguous), "
            "or 'never_llm' (pure rule-based, zero AI cost)."
        ),
    },
]


def render(project_dir):
    """Render the Pipeline Explorer page."""
    st.markdown(
        f'<h1 style="font-size:28px; color:{TEXT_PRIMARY}; margin-bottom:4px;">'
        f'🗺️ Pipeline Explorer</h1>',
        unsafe_allow_html=True,
    )

    st.markdown(
        section_description(
            "An interactive map of the Autonomy Engine pipeline — what happens at each stage, "
            "what goes in, what comes out, and why it matters.  Click any stage to expand."
        ),
        unsafe_allow_html=True,
    )

    # ── Visual flow line ───────────────────────────────────────────────────
    _render_flow_overview()

    st.markdown("<div style='height:24px'></div>", unsafe_allow_html=True)

    # ── Expandable stage detail cards ──────────────────────────────────────
    for i, stage in enumerate(PIPELINE_STAGES):
        _render_stage_card(stage, i)


def _render_flow_overview():
    """Render the compact horizontal stage overview with connecting arrows."""
    nodes_html = ""
    for i, stage in enumerate(PIPELINE_STAGES):
        color = stage["color"]
        nodes_html += f"""
            <div style="display:flex; flex-direction:column; align-items:center; min-width:80px;">
                <div style="
                    width:48px; height:48px; border-radius:50%;
                    background:{color}20; border:2px solid {color};
                    display:flex; align-items:center; justify-content:center;
                    font-size:22px;
                ">{stage['icon']}</div>
                <div style="
                    font-size:12px; font-weight:600; color:{TEXT_PRIMARY};
                    margin-top:6px; text-align:center;
                ">{stage['label']}</div>
                <div style="
                    font-size:10px; color:{TEXT_MUTED}; margin-top:2px;
                    text-align:center; max-width:90px;
                ">{stage['who']}</div>
            </div>
        """
        # Arrow between stages (not after last)
        if i < len(PIPELINE_STAGES) - 1:
            nodes_html += f"""
                <div style="
                    display:flex; align-items:center; padding:0 4px;
                    color:{MUTED}; font-size:20px; margin-top:-16px;
                ">→</div>
            """

    st.markdown(
        f"""<div style="
            display:flex; align-items:flex-start; justify-content:center;
            padding:20px 12px; overflow-x:auto;
            background:{BG_SURFACE}; border:1px solid {BORDER};
            border-radius:{RADIUS_LG};
        ">{nodes_html}</div>""",
        unsafe_allow_html=True,
    )


def _render_stage_card(stage: dict, index: int):
    """Render an expandable detail card for a pipeline stage."""
    color = stage["color"]
    key = stage["key"]

    with st.expander(
        f"{stage['icon']}  Stage {index}: {stage['label']}  —  {stage['summary'][:80]}...",
        expanded=False,
    ):
        # Summary
        st.markdown(
            f'<p style="font-size:{FONT_BODY}; color:{TEXT_BODY}; line-height:1.6; '
            f'margin-bottom:16px;">{stage["summary"]}</p>',
            unsafe_allow_html=True,
        )

        # Two-column layout: inputs and outputs
        col_in, col_out = st.columns(2)

        with col_in:
            st.markdown(
                f'<div style="font-size:13px; font-weight:600; color:{color}; '
                f'margin-bottom:8px;">📥 INPUTS</div>',
                unsafe_allow_html=True,
            )
            for name, desc in stage["inputs"]:
                st.markdown(
                    f"""<div style="
                        padding:8px 12px; margin-bottom:6px;
                        background:{BG_SURFACE}; border:1px solid {BORDER};
                        border-left:3px solid {color}; border-radius:0 {RADIUS} {RADIUS} 0;
                    ">
                        <div style="font-size:13px; font-weight:600; color:{TEXT_PRIMARY};">{name}</div>
                        <div style="font-size:12px; color:{TEXT_MUTED}; margin-top:2px;">{desc}</div>
                    </div>""",
                    unsafe_allow_html=True,
                )

        with col_out:
            st.markdown(
                f'<div style="font-size:13px; font-weight:600; color:{SUCCESS}; '
                f'margin-bottom:8px;">📤 OUTPUTS</div>',
                unsafe_allow_html=True,
            )
            for name, desc in stage["outputs"]:
                st.markdown(
                    f"""<div style="
                        padding:8px 12px; margin-bottom:6px;
                        background:{BG_SURFACE}; border:1px solid {BORDER};
                        border-left:3px solid {SUCCESS}; border-radius:0 {RADIUS} {RADIUS} 0;
                    ">
                        <div style="font-size:13px; font-weight:600; color:{TEXT_PRIMARY};">{name}</div>
                        <div style="font-size:12px; color:{TEXT_MUTED}; margin-top:2px;">{desc}</div>
                    </div>""",
                    unsafe_allow_html=True,
                )

        # "Why it matters" callout
        st.markdown(
            f"""<div style="
                margin-top:12px; padding:10px 14px;
                background:{INFO}10; border:1px solid {INFO}30;
                border-radius:{RADIUS};
            ">
                <span style="font-size:12px; font-weight:600; color:{INFO};">
                    💡 WHY THIS MATTERS
                </span>
                <p style="font-size:13px; color:{TEXT_BODY}; margin-top:4px; margin-bottom:0; line-height:1.5;">
                    {stage['why']}
                </p>
            </div>""",
            unsafe_allow_html=True,
        )
