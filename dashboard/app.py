"""Autonomy Engine Dashboard — the web interface for managing pipeline runs.

This is the main entry point for the dashboard.  It provides a visual interface
for creating projects, launching pipeline runs, viewing audit trails, inspecting
test evidence, and monitoring costs — all without touching the command line.

Launch with:
    streamlit run dashboard/app.py

Or point it at a specific project:
    AUTONOMY_ENGINE_PROJECT_DIR=/path/to/project streamlit run dashboard/app.py
"""

import sys
from pathlib import Path

# Streamlit Cloud runs `streamlit run dashboard/app.py`, which only puts the
# script's directory on sys.path — not the project root. Without this, the
# absolute `dashboard.*` imports below fail with ModuleNotFoundError.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Imports below intentionally follow the sys.path bootstrap above — moving
# them up would break `streamlit run dashboard/app.py` invocations where the
# project root isn't on sys.path.
import streamlit as st  # noqa: E402

from dashboard.components.chunk_recovery import install as install_chunk_recovery  # noqa: E402
from dashboard.data_loader import find_project_dir  # noqa: E402
from dashboard.pages import (  # noqa: E402
    audit_trail,
    benchmarks,
    config_editor,
    create_project,
    home,
    pipeline_explorer,
    run_inspector,
    run_outputs,
    run_pipeline,
)
from dashboard.rate_limiter import get_remaining_runs  # noqa: E402
from dashboard.secrets_bridge import inject_secrets  # noqa: E402
from dashboard.theme import (  # noqa: E402
    GLOBAL_CSS,
    MUTED,
    TEXT_MUTED,
)


# -- Page Config ----------------------------------------------------------

st.set_page_config(
    page_title="Autonomy Engine",
    page_icon="🏗️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# -- Global Theme CSS -----------------------------------------------------

st.markdown(GLOBAL_CSS, unsafe_allow_html=True)

# -- Stale-chunk Recovery -------------------------------------------------
# Auto-reload a tab once when Streamlit fails to fetch a lazy-loaded JS chunk
# (e.g. the user has an `index.html` cached from a previous deploy). See
# dashboard/components/chunk_recovery.py for context.

install_chunk_recovery()

# -- Secrets Bridge -------------------------------------------------------
# On Streamlit Cloud, API keys live in st.secrets.  The engine expects them
# in os.environ (via python-dotenv).  This one-liner bridges the gap so the
# pipeline subprocess inherits the keys.  Locally, .env takes precedence.

inject_secrets()

# -- Demo Banner ----------------------------------------------------------
# Visible to all visitors.  Shows remaining runs so recruiters know they
# have budget to experiment, and links to the repo for full access.

_remaining = get_remaining_runs()
st.info(
    f"**Live Demo** — {_remaining} pipeline run(s) remaining in this session. "
    "[Clone the repo](https://github.com/kaden-g/solo) for unlimited access "
    "with your own API key."
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

# Sidebar layout (top → bottom):
#   🏗️ Autonomy Engine / Project: <name>    brand header
#   Dashboard | Pipeline Explorer |          primary nav
#     Create Project | Run Pipeline
#   ── Security & Ops ──
#   Run Outputs | Inspector | Audit Trail |  secondary nav
#     Configuration | Benchmarks
#   footer tagline
#
# Streamlit's own multipage auto-nav (triggered by the dashboard/pages/ folder)
# is disabled in .streamlit/config.toml so it doesn't render above all this.

PRIMARY_PAGES = ["Dashboard", "Pipeline Explorer", "Create Project", "Run Pipeline"]
SECURITY_PAGES = ["Run Outputs", "Inspector", "Audit Trail", "Configuration", "Benchmarks"]
ALL_PAGES = PRIMARY_PAGES + SECURITY_PAGES

# Icons for each page
PAGE_ICONS = {
    "Dashboard": "📊",
    "Pipeline Explorer": "🗺️",
    "Create Project": "➕",
    "Run Pipeline": "🚀",
    "Run Outputs": "📂",
    "Inspector": "🔍",
    "Audit Trail": "🔒",
    "Configuration": "⚙️",
    "Benchmarks": "📈",
}

# Initialize page state
if "page" not in st.session_state:
    st.session_state["page"] = "Dashboard"

# Handle legacy page names from session state (e.g., "Run Inspector" → "Inspector")
if st.session_state["page"] == "Run Inspector":
    st.session_state["page"] = "Inspector"


# The sidebar is two independent st.radio widgets (primary + security) that
# must behave like one. `page` is the single source of truth:
#
#   * Before the radios render, mirror `page` into both widgets' session-state
#     keys, so in-page navigation (`st.session_state["page"] = X; st.rerun()`)
#     is reflected in the sidebar and only one radio ever shows a selection.
#   * When the user clicks a radio, its on_change callback writes `page` and
#     clears the other radio. Callbacks run before the rerun, so the mirror
#     step above then sees a consistent state.
#
# The previous approach inferred the click by diffing each radio's value
# against a `_last_*` copy. A keyed radio keeps its value across reruns, so
# switching from a primary page to a security page left both radios with a
# selection (two highlighted items), and in-page navigation never updated
# the radios at all.


def _sync_nav_widgets() -> None:
    page = st.session_state["page"]
    st.session_state["nav_primary"] = page if page in PRIMARY_PAGES else None
    st.session_state["nav_security"] = page if page in SECURITY_PAGES else None


def _on_primary_change() -> None:
    choice = st.session_state.get("nav_primary")
    if choice:
        st.session_state["page"] = choice
        st.session_state["nav_security"] = None


def _on_security_change() -> None:
    choice = st.session_state.get("nav_security")
    if choice:
        st.session_state["page"] = choice
        st.session_state["nav_primary"] = None


_sync_nav_widgets()

with st.sidebar:
    # Brand header
    st.markdown(
        """<div style="padding: 8px 0 4px 0;">
            <span style="font-size: 22px; font-weight: 700; color: white;">
                🏗️ Autonomy Engine
            </span>
        </div>""",
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<span style="font-size: 12px; color: {TEXT_MUTED};">Project: {project_dir.name}</span>',
        unsafe_allow_html=True,
    )

    st.markdown("<div style='height: 12px'></div>", unsafe_allow_html=True)

    # ── Primary navigation ──
    st.radio(
        "Main",
        PRIMARY_PAGES,
        index=None,
        format_func=lambda p: f"{PAGE_ICONS[p]}  {p}",
        label_visibility="collapsed",
        key="nav_primary",
        on_change=_on_primary_change,
    )

    # ── Security section ──
    st.markdown(
        f"""<div style="margin-top: 16px; margin-bottom: 8px; padding: 0 0 4px 0;
                border-bottom: 1px solid rgba(255,255,255,0.08);">
            <span style="font-size: 11px; font-weight: 600; color: {MUTED};
                text-transform: uppercase; letter-spacing: 1.5px;">
                Security & Ops
            </span>
        </div>""",
        unsafe_allow_html=True,
    )

    st.radio(
        "Security",
        SECURITY_PAGES,
        index=None,
        format_func=lambda p: f"{PAGE_ICONS[p]}  {p}",
        label_visibility="collapsed",
        key="nav_security",
        on_change=_on_security_change,
    )

    # Footer
    st.markdown("<div style='height: 24px'></div>", unsafe_allow_html=True)
    st.markdown(
        f'<div style="font-size: 11px; color: {MUTED}; opacity: 0.6;">'
        "Explainability · Auditability · Clarity</div>",
        unsafe_allow_html=True,
    )

current_page = st.session_state["page"]

# -- Page Routing ---------------------------------------------------------

if current_page == "Dashboard":
    home.render(project_dir)
elif current_page == "Pipeline Explorer":
    pipeline_explorer.render(project_dir)
elif current_page == "Create Project":
    create_project.render(project_dir)
elif current_page == "Run Pipeline":
    run_pipeline.render(project_dir)
elif current_page == "Run Outputs":
    run_outputs.render(project_dir)
elif current_page == "Inspector":
    run_inspector.render(project_dir)
elif current_page == "Audit Trail":
    audit_trail.render(project_dir)
elif current_page == "Configuration":
    config_editor.render(project_dir)
elif current_page == "Benchmarks":
    benchmarks.render(project_dir)
