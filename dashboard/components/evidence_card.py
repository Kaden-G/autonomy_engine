"""Reusable evidence record display card."""

import streamlit as st

from dashboard.theme import STATUS_FAILED, STATUS_PASSED, TEXT_BODY, TEXT_MUTED


def render_evidence_card(record: dict):
    """Render a single evidence record as an expandable card."""
    name = record.get("name", "unknown")
    exit_code = record.get("exit_code", -1)
    command = record.get("command", "")

    if name == "no_checks_configured":
        st.info("No automated checks were configured for this project.")
        return

    passed = exit_code == 0
    status_icon = "✓" if passed else "✗"
    status_text = "PASSED" if passed else "FAILED"
    status_color = STATUS_PASSED if passed else STATUS_FAILED

    with st.expander(
        f"{status_icon}  **{name}** — {status_text} (exit {exit_code})",
        expanded=not passed,
    ):
        st.code(command, language="bash")

        col1, col2 = st.columns(2)
        with col1:
            st.caption(f"Started: {record.get('started_at', 'N/A')}")
        with col2:
            st.caption(f"Finished: {record.get('finished_at', 'N/A')}")

        # Environment info
        env = record.get("environment", {})
        if env:
            env_parts = []
            if env.get("sandboxed"):
                env_parts.append("Sandboxed")
            if env.get("python_version"):
                env_parts.append(f"Python {env['python_version']}")
            if env.get("venv_cache_hit") is not None:
                env_parts.append(f"Venv Cache: {'Hit' if env['venv_cache_hit'] else 'Miss'}")
            if env_parts:
                st.caption(" | ".join(env_parts))

        # stdout
        stdout = record.get("stdout", "").strip()
        if stdout:
            st.markdown("**stdout:**")
            display = stdout[:3000] + "\n... (truncated)" if len(stdout) > 3000 else stdout
            st.code(display, language="text")

        # stderr
        stderr = record.get("stderr", "").strip()
        if stderr:
            st.markdown("**stderr:**")
            display = stderr[:2000] + "\n... (truncated)" if len(stderr) > 2000 else stderr
            st.code(display, language="text")

        # Integrity hashes
        if record.get("stdout_hash") or record.get("stderr_hash"):
            with st.expander("🔐 Integrity Hashes"):
                if record.get("stdout_hash"):
                    st.code(
                        f"stdout SHA-256: {record['stdout_hash']}",
                        language="text",
                    )
                if record.get("stderr_hash"):
                    st.code(
                        f"stderr SHA-256: {record['stderr_hash']}",
                        language="text",
                    )
