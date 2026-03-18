"""Reusable page header with contextual description."""

import streamlit as st

from dashboard.theme import section_description


def render_page_description(text: str) -> None:
    """Render a muted, compact description block below the page title.

    Use this at the top of each page to orient the user — what this page
    does, what they'll see, and where to look.
    """
    st.markdown(section_description(text), unsafe_allow_html=True)
