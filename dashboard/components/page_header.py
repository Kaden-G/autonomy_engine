"""Reusable page header with contextual description."""

import streamlit as st


def render_page_description(text: str) -> None:
    """Render a muted, compact description block below the page title.

    Use this at the top of each page to orient the user — what this page
    does, what they'll see, and where to look.
    """
    st.markdown(
        f"""<div style="background:#F4F6F9; border-left:3px solid #3498DB;
            border-radius:0 6px 6px 0; padding:10px 14px; margin-bottom:20px;">
            <span style="font-size:13px; color:#5D6D7E; line-height:1.5;">
                {text}
            </span>
        </div>""",
        unsafe_allow_html=True,
    )
