"""Create Project page — form-based intake for ProjectSpec.

Also includes project management: load a previous project's spec into
the form, or clear all fields to start fresh.
"""

import streamlit as st
from pydantic import ValidationError

from engine.context import init as init_context
from intake.renderer import render_all
from intake.schema import Constraints, Domain, Outputs, ProjectInfo, ProjectSpec, Requirements

from dashboard.data_loader import get_state_dir, list_runs, load_project_spec

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


def _run_llm_suggestions() -> None:
    """Call intake._generate_spec_suggestions() with the form's seed fields.

    On success, merge the generated requirements/acceptance/artifacts into the
    form session_state so the user can review and edit. Gated by the shared
    rate limiter so hosted-demo visitors can't exhaust the budget.
    """
    from dashboard.rate_limiter import check_rate_limit
    from intake.intake import _generate_spec_suggestions

    if not check_rate_limit():
        # check_rate_limit() already showed the user a warning.
        return

    seed = {
        "name": st.session_state.get("cp_name", "").strip(),
        "description": st.session_state.get("cp_description", "").strip(),
        "domain": st.session_state.get("cp_domain", "software"),
    }

    with st.spinner("Generating draft from your description…"):
        suggestions = _generate_spec_suggestions(seed)

    if not suggestions:
        st.error(
            "Generation failed — the LLM did not return a valid spec. "
            "Check the logs for details and try again, or fill in the fields manually."
        )
        return

    # Merge generated fields into the form. _generate_spec_suggestions returns
    # lists for the text-area fields; join with newlines so the form renders
    # them naturally. Only overwrite fields the generator actually populated.
    field_map = {
        "functional": "cp_functional",
        "non_functional": "cp_non_functional",
        "tech_stack": "cp_tech_stack",
        "non_goals": "cp_non_goals",
        "acceptance": "cp_acceptance",
        "artifacts": "cp_artifacts",
    }
    for src_key, form_key in field_map.items():
        val = suggestions.get(src_key)
        if isinstance(val, list):
            st.session_state[form_key] = "\n".join(val)
        elif isinstance(val, str) and val.strip():
            st.session_state[form_key] = val

    # Scalar constraint fields sometimes come back as plain strings.
    for src_key, form_key in [("performance", "cp_performance"), ("security", "cp_security")]:
        val = suggestions.get(src_key)
        if isinstance(val, str) and val.strip():
            st.session_state[form_key] = val

    st.success("Draft generated. Review the form below and adjust anything before submitting.")
    st.rerun()


def _parse_uploaded_yaml(uploaded_file) -> dict:
    """Parse an uploaded YAML into a ProjectSpec without side effects.

    Returns ``{"ok": True, "spec": ProjectSpec}`` on success or
    ``{"ok": False, "error": str}`` with a short human-readable error
    string suitable for `st.error()`.
    """
    import yaml

    try:
        raw = uploaded_file.getvalue().decode("utf-8")
    except Exception as e:
        return {"ok": False, "error": f"Could not read file: {e}"}

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        return {"ok": False, "error": f"YAML parse error: {e}"}

    if not isinstance(data, dict):
        return {"ok": False, "error": "YAML must contain a mapping at the top level."}

    try:
        spec = ProjectSpec(**data)
    except ValidationError as e:
        # Collapse pydantic errors into a short bullet list for the UI.
        bullets = "\n".join(
            f"• {'/'.join(str(p) for p in err['loc'])}: {err['msg']}"
            for err in e.errors()
        )
        return {"ok": False, "error": f"Validation failed:\n{bullets}"}

    return {"ok": True, "spec": spec}


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
        st.markdown(f"**Active project:** {project_name} · {run_count} run(s) on record")

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

    # ── LLM-powered intake suggestions ──────────────────────────────────
    # Matches the CLI's `new-project` flow, which offers LLM generation
    # of functional/non-functional/acceptance/artifacts from a project seed.
    # Gated by the session rate limiter on the hosted demo so a visitor
    # can't burn API tokens in a loop.
    with st.expander("Generate draft from description (uses an API call)"):
        st.caption(
            "Fill in Project Name, Description, and Domain below first. "
            "Clicking Generate will call the configured LLM to draft the rest "
            "of the spec. This counts as one action against the demo rate limit."
        )
        if st.button(
            "Generate draft",
            use_container_width=True,
            disabled=not (
                st.session_state.get("cp_name", "").strip()
                and st.session_state.get("cp_description", "").strip()
            ),
        ):
            _run_llm_suggestions()

    # ── YAML spec import + standalone validation ────────────────────────
    # Matches the CLI's `intake from-file` and `intake validate` subcommands.
    with st.expander("Import from YAML / validate-only"):
        uploaded = st.file_uploader(
            "Upload a project_spec.yml",
            type=["yml", "yaml"],
            key="cp_yaml_upload",
            help="Same format as `python -m intake.intake from-file ...`.",
        )
        if uploaded is not None:
            col_a, col_b = st.columns(2)
            parsed = _parse_uploaded_yaml(uploaded)
            with col_a:
                if st.button(
                    "Validate only",
                    use_container_width=True,
                    help="Check schema conformance; don't touch the form or state/.",
                ):
                    if parsed["ok"]:
                        st.success("Spec is valid — no fields were modified.")
                    else:
                        st.error(parsed["error"])
            with col_b:
                if st.button(
                    "Import into form",
                    type="primary",
                    use_container_width=True,
                    help="Populate the form below from the YAML. You still need to submit.",
                ):
                    if parsed["ok"]:
                        _load_spec_into_form(parsed["spec"].model_dump())
                        st.success(f"Imported '{parsed['spec'].project.name}'. Review and submit below.")
                        st.rerun()
                    else:
                        st.error(parsed["error"])

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
                    f"`{run_id[:12]}…` · {started} · {stages} · {evidence_count} evidence records"
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
