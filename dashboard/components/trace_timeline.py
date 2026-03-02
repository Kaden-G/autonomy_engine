"""Reusable trace entry timeline component."""

from datetime import datetime

import streamlit as st


TASK_COLORS = {
    "bootstrap": "#3498DB",
    "design": "#9B59B6",
    "implement": "#E67E22",
    "extract": "#1ABC9C",
    "test": "#2ECC71",
    "verify": "#E74C3C",
    "decision": "#F39C12",
}


def render_timeline(entries: list[dict]):
    """Render trace entries as a vertical timeline."""
    if not entries:
        st.info("No trace entries found.")
        return

    for entry in entries:
        task = entry.get("task", "unknown")
        color = TASK_COLORS.get(task, "#95A5A6")
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
            chips.append(f"🤖 {model}")
        if extra.get("cache_hit"):
            chips.append("⚡ Cache Hit")
        if extra.get("llm_called") is False:
            chips.append("🚫 LLM Skipped")
        if extra.get("verify_mode"):
            chips.append(f"🔧 {extra['verify_mode']}")
        if extra.get("sandbox_venv_cache_hit"):
            chips.append("📦 Venv Cached")

        chip_html = " ".join(
            f'<span style="background:#f0f0f0; padding:2px 8px; border-radius:12px; '
            f'font-size:11px; margin-right:4px;">{c}</span>'
            for c in chips
        )

        # Render entry
        st.markdown(
            f"""<div style="display:flex; gap:12px; margin-bottom:12px;
                    padding:12px; border-left:4px solid {color};
                    background-color:{color}08; border-radius:0 8px 8px 0;">
                <div style="min-width:32px; text-align:center;">
                    <div style="font-size:18px; font-weight:700; color:{color};">
                        {seq}
                    </div>
                    <div style="font-size:10px; color:#95A5A6;">{time_str}</div>
                </div>
                <div style="flex:1;">
                    <div style="font-weight:600; color:#2C3E50; font-size:14px;
                         text-transform:uppercase; letter-spacing:0.5px;">
                        {task}
                    </div>
                    <div style="margin-top:4px;">{chip_html}</div>
                </div>
            </div>""",
            unsafe_allow_html=True,
        )
