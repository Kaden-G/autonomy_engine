"""Autonomy Engine Dashboard — Streamlit entry point.

Launch with:
    streamlit run dashboard/app.py

Or with a specific project directory:
    AUTONOMY_ENGINE_PROJECT_DIR=/path/to/project streamlit run dashboard/app.py
"""

import streamlit as st

from dashboard.data_loader import find_project_dir
from dashboard.pages import audit_trail, benchmarks, config_editor, home, run_inspector


# -- Page Config ----------------------------------------------------------

st.set_page_config(
    page_title="Autonomy Engine",
    page_icon="🏗️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# -- Custom CSS -----------------------------------------------------------

st.markdown(
    """
<style>
    /* Clean sidebar */
    [data-testid="stSidebar"] {
        background-color: #1B3A5C;
    }
    [data-testid="stSidebar"] .stMarkdown p,
    [data-testid="stSidebar"] .stMarkdown h1,
    [data-testid="stSidebar"] .stMarkdown h2,
    [data-testid="stSidebar"] .stMarkdown h3 {
        color: white;
    }
    [data-testid="stSidebar"] .stRadio label p {
        color: white;
        font-size: 15px;
    }

    /* Tighten main content */
    .block-container {
        padding-top: 2rem;
        padding-bottom: 2rem;
    }
</style>
""",
    unsafe_allow_html=True,
)

# -- Project Directory Resolution -----------------------------------------

project_dir = find_project_dir()

if project_dir is None:
    st.error(
        "Could not find the Autonomy Engine project directory. "
        "Set the `AUTONOMY_ENGINE_PROJECT_DIR` environment variable or "
        "run this from the project root."
    )
    st.stop()

# -- Sidebar Navigation --------------------------------------------------

with st.sidebar:
    st.markdown("# 🏗️ Autonomy Engine")
    st.caption(f"Project: `{project_dir.name}`")
    st.divider()

    pages = {
        "Dashboard": "📊",
        "Run Inspector": "🔍",
        "Audit Trail": "🔒",
        "Configuration": "⚙️",
        "Benchmarks": "📈",
    }

    # Use session state for navigation (allows programmatic page switching)
    if "page" not in st.session_state:
        st.session_state["page"] = "Dashboard"

    selected_page = st.radio(
        "Navigation",
        list(pages.keys()),
        index=list(pages.keys()).index(st.session_state["page"]),
        format_func=lambda p: f"{pages[p]} {p}",
        label_visibility="collapsed",
    )
    st.session_state["page"] = selected_page

    st.divider()
    st.caption("Explainability · Auditability · Clarity")

# -- Page Routing ---------------------------------------------------------

if selected_page == "Dashboard":
    home.render(project_dir)
elif selected_page == "Run Inspector":
    run_inspector.render(project_dir)
elif selected_page == "Audit Trail":
    audit_trail.render(project_dir)
elif selected_page == "Configuration":
    config_editor.render(project_dir)
elif selected_page == "Benchmarks":
    benchmarks.render(project_dir)
