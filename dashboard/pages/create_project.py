"""Create Project page — form-based intake for ProjectSpec.

Also includes project management: load a previous project's spec into
the form, or clear all fields to start fresh.
"""

import yaml
import streamlit as st
from pydantic import ValidationError

from engine.context import init as init_context
from intake.renderer import render_all
from intake.schema import Constraints, Domain, Outputs, ProjectInfo, ProjectSpec, Requirements

from dashboard.data_loader import get_state_dir, list_runs, load_project_spec
from dashboard.theme import (
    BG_SURFACE,
    BORDER,
    FONT_BODY,
    FONT_SMALL,
    MUTED,
    PRIMARY,
    RADIUS,
    TEXT_BODY,
    TEXT_MUTED,
    TEXT_PRIMARY,
)

# Keys for all form fields stored in session_state
_FIELDS = {
    "cp_name": "",
    "cp_domain": "software",
    "cp_description": "",
    "cp_functional": "",
    "cp_non_functional": "",
    "cp_tech_stack": "",
    "cp_performance": "",
    "cp_security": "",
    "cp_non_goals": "",
    "cp_acceptance": "",
    "cp_artifacts": "",
}


def _init_session_defaults():
    """Ensure all form keys exist in session_state with defaults."""
    for key, default in _FIELDS.items():
        if key not in st.session_state:
            st.session_state[key] = default


def _parse_lines(text: str) -> list[str]:
    """Split text_area content into a list of non-empty stripped lines."""
    return [line.strip() for line in text.strip().splitlines() if line.strip()]


def _clear_form():
    """Reset all form fields to defaults."""
    for key, default in _FIELDS.items():
        st.session_state[key] = default


def _load_spec_into_form(spec: dict):
    """Populate the form fields from a project_spec.yml dict."""
    project = spec.get("project", {})
    reqs = spec.get("requirements", {})
    constraints = spec.get("constraints", {})
    outputs = spec.get("outputs", {})

    st.session_state["cp_name"] = project.get("name", "")
    st.session_state["cp_domain"] = project.get("domain", "software")
    st.session_state["cp_description"] = project.get("description", "")

    # Lists → newline-joined text
    st.session_state["cp_functional"] = "\n".join(reqs.get("functional", []))
    st.session_state["cp_non_functional"] = "\n".join(reqs.get("non_functional", []))
    st.session_state["cp_tech_stack"] = "\n".join(constraints.get("tech_stack", []))
    st.session_state["cp_performance"] = constraints.get("performance", "") or ""
    st.session_state["cp_security"] = constraints.get("security", "") or ""
    st.session_state["cp_non_goals"] = "\n".join(spec.get("non_goals", []))
    st.session_state["cp_acceptance"] = "\n".join(spec.get("acceptance_criteria", []))
    st.session_state["cp_artifacts"] = "\n".join(outputs.get("expected_artifacts", []))


def render(project_dir):
    st.title("Create Project")

    from dashboard.components.page_header import render_page_description
    render_page_description(
        "Define what you want to build. Fill in the form below — "
        "required fields are marked with *. "
        "Your inputs auto-save, so you can navigate away and come back "
        "without losing work. On submit, the engine generates intake artifacts "
        "that feed into the pipeline. Use the controls below to load a "
        "previous project or start fresh."
    )

    _init_session_defaults()

    # ── Project management bar ──────────────────────────────────────────
    st.subheader("Project Manager")

    current_spec = load_project_spec(project_dir)
    state_dir = get_state_dir(project_dir)
    runs = list_runs(project_dir)

    # Show current project info if one exists
    if current_spec:
        project_name = current_spec.get("project", {}).get("name", "Unknown")
        run_count = len(runs)
        st.markdown(
            f"**Active project:** {project_name} · "
            f"{run_count} run(s) on record"
        )

        # Where are built artifacts?
        build_dir = state_dir / "build"
        if build_dir.exists():
            manifest = build_dir / "MANIFEST.md"
            if manifest.exists():
                st.caption(f"Build output: `{build_dir}`")

    mgmt_col1, mgmt_col2, mgmt_col3 = st.columns(3)

    with mgmt_col1:
        if current_spec and st.button("Load Current Project", use_container_width=True):
            _load_spec_into_form(current_spec)
            st.rerun()

    with mgmt_col2:
        if st.button("Clear Form (Start New)", use_container_width=True):
            _clear_form()
            st.rerun()

    with mgmt_col3:
        # Show past run output directories
        if runs:
            if st.button("View Run History", use_container_width=True):
                st.session_state["page"] = "Inspector"
                st.rerun()

    # Show past runs summary if we have any
    if runs and current_spec:
        with st.expander(f"Run history ({len(runs)} runs)"):
            for run in runs[:10]:
                run_id = run["run_id"]
                started = run.get("started_at", "?")
                if isinstance(started, str) and len(started) > 19:
                    started = started[:19].replace("T", " ")
                stages = " → ".join(run.get("stages", [])) if run.get("stages") else "no stages"
                evidence_count = run.get("evidence_count", 0)

                st.markdown(
                    f"`{run_id[:12]}…` · {started} · {stages} · "
                    f"{evidence_count} evidence records"
                )

    st.divider()

    # ── Create / edit form ──────────────────────────────────────────────
    domain_options = [d.value for d in Domain]

    with st.form("create_project_form"):
        # -- Project Info -------------------------------------------------
        st.subheader("Project Info")
        col1, col2 = st.columns([2, 1])
        with col1:
            name = st.text_input("Project Name *", value=st.session_state["cp_name"])
        with col2:
            domain = st.selectbox(
                "Domain *",
                options=domain_options,
                index=domain_options.index(st.session_state["cp_domain"])
                if st.session_state["cp_domain"] in domain_options
                else 0,
                format_func=lambda d: d.capitalize(),
            )
        description = st.text_area(
            "Description * (min 10 characters)",
            value=st.session_state["cp_description"],
            height=80,
        )

        # -- Requirements -------------------------------------------------
        st.subheader("Requirements")
        functional_raw = st.text_area(
            "Functional Requirements * (one per line, min 1)",
            value=st.session_state["cp_functional"],
            height=120,
        )
        non_functional_raw = st.text_area(
            "Non-Functional Requirements (one per line, optional)",
            value=st.session_state["cp_non_functional"],
            height=80,
        )

        # -- Constraints --------------------------------------------------
        st.subheader("Constraints")
        tech_stack_raw = st.text_area(
            "Tech Stack (one per line, optional)",
            value=st.session_state["cp_tech_stack"],
            height=80,
        )
        col_perf, col_sec = st.columns(2)
        with col_perf:
            performance = st.text_input(
                "Performance Constraint (optional)",
                value=st.session_state["cp_performance"],
            )
        with col_sec:
            security = st.text_input(
                "Security Constraint (required if domain=infra)",
                value=st.session_state["cp_security"],
            )

        # -- Scope --------------------------------------------------------
        st.subheader("Scope & Acceptance")
        non_goals_raw = st.text_area(
            "Non-Goals (one per line, optional)",
            value=st.session_state["cp_non_goals"],
            height=80,
        )
        acceptance_raw = st.text_area(
            "Acceptance Criteria * (one per line, min 1)",
            value=st.session_state["cp_acceptance"],
            height=120,
        )
        artifacts_raw = st.text_area(
            "Expected Artifacts * (one per line, min 1)",
            value=st.session_state["cp_artifacts"],
            height=80,
        )

        submitted = st.form_submit_button("Create Project", type="primary")

    # Always save current inputs back to session_state (persists across navigation)
    st.session_state["cp_name"] = name
    st.session_state["cp_domain"] = domain
    st.session_state["cp_description"] = description
    st.session_state["cp_functional"] = functional_raw
    st.session_state["cp_non_functional"] = non_functional_raw
    st.session_state["cp_tech_stack"] = tech_stack_raw
    st.session_state["cp_performance"] = performance
    st.session_state["cp_security"] = security
    st.session_state["cp_non_goals"] = non_goals_raw
    st.session_state["cp_acceptance"] = acceptance_raw
    st.session_state["cp_artifacts"] = artifacts_raw

    if submitted:
        # Parse text areas into lists
        functional = _parse_lines(functional_raw)
        non_functional = _parse_lines(non_functional_raw)
        tech_stack = _parse_lines(tech_stack_raw)
        non_goals = _parse_lines(non_goals_raw)
        acceptance_criteria = _parse_lines(acceptance_raw)
        expected_artifacts = _parse_lines(artifacts_raw)

        try:
            spec = ProjectSpec(
                project=ProjectInfo(
                    name=name,
                    description=description,
                    domain=Domain(domain),
                ),
                requirements=Requirements(
                    functional=functional,
                    non_functional=non_functional,
                ),
                constraints=Constraints(
                    tech_stack=tech_stack,
                    performance=performance or None,
                    security=security or None,
                ),
                non_goals=non_goals,
                acceptance_criteria=acceptance_criteria,
                outputs=Outputs(expected_artifacts=expected_artifacts),
            )
        except ValidationError as exc:
            st.error("Validation failed:")
            for err in exc.errors():
                field = " -> ".join(str(loc) for loc in err["loc"])
                st.error(f"**{field}**: {err['msg']}")
            return

        # Initialize project context and render artifacts
        init_context(project_dir)
        written = render_all(spec)

        st.success(f"Project created! {len(written)} artifacts written:")
        for path in written:
            st.markdown(f"- `state/{path}`")

        st.info("Head to **Run Pipeline** to start the build.")
        if st.button("Go to Run Pipeline"):
            st.session_state["page"] = "Run Pipeline"
            st.rerun()
