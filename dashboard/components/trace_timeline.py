"""Reusable trace entry timeline component."""

from datetime import datetime

import streamlit as st

from dashboard.theme import (
    FONT_BODY,
    FONT_SMALL,
    RADIUS,
    STAGE_COLORS,
    TEXT_MUTED,
    TEXT_PRIMARY,
    chip,
)


def render_timeline(entries: list[dict]):
    """Render trace entries as a vertical timeline."""
    if not entries:
        st.info("No trace entries found.")
        return

    for entry in entries:
        task = entry.get("task", "unknown")
        color = STAGE_COLORS.get(task, TEXT_MUTED)
        seq = entry.get("seq", "?")
        ts = entry.get("timestamp", "")
        model = entry.get("model")
        extra = entry.get("extra", {})

        # Parse timestamp for display
        try:
            dt = datetime.fromisoformat(ts)
            time_str = dt.strftime("%H:%M:%S")
        except (ValueError, TypeError):
            time_str = ts[:19] if ts else "?"

        # Build detail chips
        chips = []
        if model:
            chips.append(chip(f"🤖 {model}"))
        if extra.get("cache_hit"):
            chips.append(chip("⚡ Cache Hit"))
        if extra.get("llm_called") is False:
            chips.append(chip("🚫 LLM Skipped"))
        if extra.get("verify_mode"):
            chips.append(chip(f"🔧 {extra['verify_mode']}"))
        if extra.get("sandbox_venv_cache_hit"):
            chips.append(chip("📦 Venv Cached"))

        chip_html = " ".join(chips)

        # Dark-mode: use color at low alpha for bg, full for border & accent
        st.markdown(
            f"""<div style="display:flex; gap:12px; margin-bottom:10px;
                    padding:12px; border-left:4px solid {color};
                    background-color:{color}12; border-radius:0 {RADIUS} {RADIUS} 0;">
                <div style="min-width:32px; text-align:center;">
                    <div style="font-size:18px; font-weight:700; color:{color};">
                        {seq}
                    </div>
                    <div style="font-size:{FONT_SMALL}; color:{TEXT_MUTED};">{time_str}</div>
                </div>
                <div style="flex:1;">
                    <div style="font-weight:600; color:{TEXT_PRIMARY}; font-size:{FONT_BODY};
                         text-transform:uppercase; letter-spacing:0.5px;">
                        {task}
                    </div>
                    <div style="margin-top:4px;">{chip_html}</div>
                </div>
            </div>""",
            unsafe_allow_html=True,
        )
