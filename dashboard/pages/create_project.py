"""Create Project page — form-based intake for ProjectSpec."""

import streamlit as st
from pydantic import ValidationError

from engine.context import init as init_context
from intake.renderer import render_all
from intake.schema import Constraints, Domain, Outputs, ProjectInfo, ProjectSpec, Requirements

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


def render(project_dir):
    st.title("Create Project")

    from dashboard.components.page_header import render_page_description
    render_page_description(
        "Define what you want to build. Fill in the form below — "
        "<strong>required fields</strong> are marked with *. "
        "Your inputs auto-save, so you can navigate away and come back "
        "without losing work. On submit, the engine generates intake artifacts "
        "(requirements, constraints, acceptance criteria) that feed into the pipeline. "
        "After creating your project, head to <strong>Run Pipeline</strong> to start the build."
    )

    _init_session_defaults()

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
