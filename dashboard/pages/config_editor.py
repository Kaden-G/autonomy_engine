"""Configuration viewer — display active config and gate policies."""

import streamlit as st
import yaml

from dashboard.data_loader import load_config, load_gate_policies


def render(project_dir):
    st.title("⚙️ Configuration")

    from dashboard.components.page_header import render_page_description
    render_page_description(
        "View the active settings for this project. <strong>LLM Provider</strong> shows which "
        "model and token budget each stage uses. <strong>Decision Gate Policies</strong> control "
        "what happens at review points (auto-approve, pause for human input, or skip). "
        "<strong>Verify Settings</strong> determines when the LLM is called for the final verdict. "
        "<strong>Sandbox</strong> controls test isolation. To change settings, edit "
        "<code>config.yml</code> directly — changes take effect on the next run."
    )

    config = load_config(project_dir)
    if not config:
        st.warning("No config.yml found.")
        return

    # LLM Settings
    st.subheader("LLM Provider")
    llm = config.get("llm", {})

    col1, col2, col3 = st.columns(3)
    col1.metric("Provider", llm.get("provider", "N/A"))
    col2.metric("Max Tokens", llm.get("max_tokens", "N/A"))
    col3.metric(
        "Default Model",
        llm.get(llm.get("provider", "claude"), {}).get("model", "N/A"),
    )

    # Per-stage models
    models = llm.get("models", {})
    if models:
        st.markdown("**Per-Stage Model Overrides:**")
        for stage, model in models.items():
            st.caption(f"  {stage}: `{model}`")

    st.divider()

    # Decision Gate Policies
    st.subheader("Decision Gate Policies")
    gates = load_gate_policies(project_dir)
    if gates:
        for stage, gate_cfg in gates.items():
            policy = gate_cfg.get("policy", "skip") if isinstance(gate_cfg, dict) else gate_cfg
            default_opt = gate_cfg.get("default_option") if isinstance(gate_cfg, dict) else None

            policy_colors = {"pause": "🟡", "auto": "🟢", "skip": "🔵"}
            icon = policy_colors.get(policy, "⚪")

            st.markdown(
                f"{icon} **{stage}**: `{policy}`"
                + (f" (default: `{default_opt}`)" if default_opt else "")
            )
    else:
        st.info("No gate policies configured.")

    st.divider()

    # Verify Settings
    st.subheader("Verify Settings")
    verify = config.get("verify", {})
    if verify:
        vcol1, vcol2, vcol3 = st.columns(3)
        vcol1.metric("Mode", verify.get("mode", "always_llm"))
        vcol2.metric("LLM on Fail", str(verify.get("llm_on_fail_summary", True)))
        vcol3.metric("LLM on Pass", str(verify.get("llm_on_pass_summary", True)))
    else:
        st.caption("Using defaults (always_llm)")

    st.divider()

    # Sandbox Settings
    st.subheader("Sandbox Settings")
    sandbox = config.get("sandbox", {})
    scol1, scol2 = st.columns(2)
    scol1.metric("Enabled", str(sandbox.get("enabled", True)))
    scol2.metric("Install Deps", str(sandbox.get("install_deps", True)))

    st.divider()

    # Configured Checks
    st.subheader("Approved Check Commands")
    checks = config.get("checks", [])
    if checks:
        for check in checks:
            st.code(
                f"{check.get('name', '?')}: {check.get('command', '?')}",
                language="bash",
            )
    else:
        st.info(
            "No checks configured. Add a `checks` section to config.yml "
            "to enable automated testing."
        )

    st.divider()

    # Raw config
    with st.expander("📝 Raw config.yml"):
        st.code(yaml.dump(config, default_flow_style=False), language="yaml")
