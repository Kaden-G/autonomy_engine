"""Dashboard theme — centralized colors, fonts, and styling for the web UI.

All visual styling (colors, typography, spacing) is defined here in one place.
Dashboard pages and components import from this module instead of hardcoding
color values, which keeps the UI consistent and makes restyling straightforward.

Design principle: dark-mode native.  All colors are chosen to look right on
dark backgrounds.  Streamlit's built-in dark theme handles standard elements;
this module styles the custom HTML elements (cards, badges, timeline, etc.).
"""

# ── Brand / Sidebar ─────────────────────────────────────────────────────────
SIDEBAR_BG = "#0F2537"            # Deep navy
SIDEBAR_ACCENT = "#3B82F6"        # Bright blue accent for active items
SIDEBAR_TEXT = "#CBD5E1"          # Soft silver for sidebar body text
SIDEBAR_TEXT_ACTIVE = "#FFFFFF"   # White for active/selected item

# ── Semantic palette ────────────────────────────────────────────────────────
PRIMARY = "#3B82F6"               # Blue — primary actions, active states
SUCCESS = "#10B981"               # Green — passed, healthy, complete
WARNING = "#F59E0B"               # Amber — in-progress, caution
ERROR = "#EF4444"                 # Red — failed, broken, critical
MUTED = "#64748B"                 # Slate — disabled, pending, placeholder
INFO = "#6366F1"                  # Indigo — informational highlights

# ── Surface & text (dark-mode native) ───────────────────────────────────────
BG_PAGE = "#0E1117"               # Streamlit's dark mode page background
BG_SURFACE = "rgba(255,255,255,0.05)"   # Subtle surface lift (transparent)
BG_SURFACE_DARK = "rgba(255,255,255,0.08)"  # Slightly more contrast
BORDER = "rgba(255,255,255,0.1)"  # Subtle borders on dark bg
BORDER_FOCUS = "#3B82F6"          # Focused/active borders

TEXT_PRIMARY = "#E2E8F0"          # Headings, high-emphasis (off-white)
TEXT_BODY = "#CBD5E1"             # Body text (soft silver)
TEXT_MUTED = "#64748B"            # Captions, timestamps
TEXT_INVERSE = "#0F172A"          # Dark text on light backgrounds (badges)

# ── Typography ──────────────────────────────────────────────────────────────
FONT_BODY = "14px"               # Every non-header text uses this
FONT_SMALL = "12px"              # Captions, timestamps, hash values
FONT_LABEL = "13px"              # Form labels, chip text
FONT_H1 = "28px"                 # Page titles
FONT_H2 = "20px"                 # Section headers
FONT_H3 = "16px"                 # Subsection headers
LINE_HEIGHT = "1.6"              # Comfortable reading

# ── Pipeline stage colors ───────────────────────────────────────────────────
STAGE_COLORS = {
    "bootstrap": "#3B82F6",       # Blue
    "design": "#8B5CF6",          # Purple
    "implement": "#F59E0B",       # Amber
    "extract": "#06B6D4",         # Cyan
    "test": "#10B981",            # Green
    "verify": "#EF4444",          # Red
    "decision": "#F97316",        # Orange
}

# ── Pipeline stage status ───────────────────────────────────────────────────
STATUS_PASSED = SUCCESS
STATUS_FAILED = ERROR
STATUS_RUNNING = WARNING
STATUS_PENDING = "#334155"        # Dark slate — visually "off" on dark bg

# ── Spacing ─────────────────────────────────────────────────────────────────
RADIUS = "8px"
RADIUS_SM = "4px"
RADIUS_LG = "12px"
PADDING_CARD = "16px"
PADDING_SECTION = "24px"

# ── Global CSS ──────────────────────────────────────────────────────────────
# Injected once in app.py. We intentionally DON'T override Streamlit's
# native text colors for standard elements (p, h1, etc.) — those inherit
# from Streamlit's dark theme. We only style OUR custom HTML and tweak
# spacing/sizing.

GLOBAL_CSS = f"""
<style>
    /* ── Sidebar ── */
    [data-testid="stSidebar"] {{
        background-color: {SIDEBAR_BG};
    }}
    [data-testid="stSidebar"] .stMarkdown p,
    [data-testid="stSidebar"] .stMarkdown h1,
    [data-testid="stSidebar"] .stMarkdown h2,
    [data-testid="stSidebar"] .stMarkdown h3,
    [data-testid="stSidebar"] .stMarkdown li {{
        color: {SIDEBAR_TEXT};
    }}
    [data-testid="stSidebar"] .stRadio label p {{
        color: {SIDEBAR_TEXT};
        font-size: {FONT_BODY};
    }}
    [data-testid="stSidebar"] .stRadio label[data-checked="true"] p {{
        color: {SIDEBAR_TEXT_ACTIVE};
        font-weight: 600;
    }}
    [data-testid="stSidebar"] .stSelectbox label p {{
        color: {SIDEBAR_TEXT};
        font-size: {FONT_SMALL};
    }}
    [data-testid="stSidebar"] hr {{
        border-color: rgba(255,255,255,0.1);
    }}

    /* ── Main content spacing ── */
    .block-container {{
        padding-top: 2rem;
        padding-bottom: 2rem;
    }}

    /* ── Typography sizing (color inherits from Streamlit theme) ── */
    .main .stMarkdown p,
    .main .stMarkdown li,
    .main .stMarkdown td {{
        font-size: {FONT_BODY};
        line-height: {LINE_HEIGHT};
    }}

    /* Tab labels */
    .stTabs [data-baseweb="tab"] p {{
        font-size: {FONT_BODY};
    }}
    .stTabs [aria-selected="true"] p {{
        color: {PRIMARY} !important;
        font-weight: 600;
    }}

    /* Info/warning/error boxes */
    .stAlert p {{
        font-size: {FONT_BODY} !important;
    }}
</style>
"""


# ── Helper functions ────────────────────────────────────────────────────────

def card(content: str, border_color: str = BORDER, bg: str = BG_SURFACE) -> str:
    """Wrap HTML content in a styled card div."""
    return (
        f'<div style="border:1px solid {border_color}; border-radius:{RADIUS};'
        f' padding:{PADDING_CARD}; background:{bg}; margin-bottom:12px;">'
        f'{content}</div>'
    )


def status_badge(label: str, color: str) -> str:
    """Small colored badge (e.g., PASS, FAIL, RUNNING)."""
    return (
        f'<span style="background:{color}; color:white; padding:2px 10px;'
        f' border-radius:12px; font-size:11px; font-weight:600;">{label}</span>'
    )


def chip(text: str) -> str:
    """Inline chip for metadata (model name, cache hit, etc.)."""
    return (
        f'<span style="background:{BG_SURFACE_DARK}; color:{TEXT_BODY};'
        f' padding:2px 8px; border-radius:12px; font-size:{FONT_SMALL};'
        f' margin-right:4px;">{text}</span>'
    )


def section_description(text: str) -> str:
    """Page-top description block with left accent border.
    Dark-mode native — translucent background, light text.
    """
    return (
        f'<div style="background:{BG_SURFACE}; border-left:3px solid {PRIMARY};'
        f' border-radius:0 {RADIUS} {RADIUS} 0; padding:10px 14px;'
        f' margin-bottom:20px;">'
        f'<span style="font-size:{FONT_BODY}; color:{TEXT_BODY};'
        f' line-height:{LINE_HEIGHT};">{text}</span></div>'
    )
