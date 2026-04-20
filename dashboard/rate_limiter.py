"""Session-scoped rate limiter for the hosted demo.

Limits the number of pipeline runs per Streamlit session to control API
costs when the app is publicly accessible (e.g. on Streamlit Cloud).

Design decisions:
    - **Session-scoped, not IP-based.**  Streamlit Cloud doesn't expose
      client IPs to the app, and visitor sessions are ephemeral anyway.
      A page refresh resets the counter — this is intentional.  The goal
      is *cost guardrail*, not *abuse prevention*.  A determined user
      refreshing repeatedly still costs less than adding BYOK friction
      that scares away recruiters.  (OWASP: this is a demo, not a
      production API — proportional controls.)
    - **Counter stored in st.session_state.**  No database, no cookies,
      no external dependencies.  Simplest thing that works.
    - **MAX_RUNS_PER_SESSION is configurable** via Streamlit secrets or
      env var for easy tuning without code changes.

RISK: No persistent rate limiting across sessions.  A visitor can reset
their counter by refreshing.  Acceptable for a portfolio demo; for a
production SaaS product, use server-side tracking with Redis or a
database.  (POAM: implement persistent rate limiting if demo traffic
exceeds budget tolerance.)
"""

import os
from datetime import datetime, timezone

import streamlit as st

# Default: 3 pipeline runs per session.  Override via secrets or env var.
_DEFAULT_MAX_RUNS = 3


def _get_max_runs() -> int:
    """Read the run limit from config, with sensible fallback chain."""
    # 1. Environment variable (highest priority — easy override in any host)
    env_val = os.environ.get("DEMO_MAX_RUNS")
    if env_val and env_val.isdigit():
        return int(env_val)

    # 2. Streamlit secrets (for Streamlit Cloud configuration)
    try:
        secret_val = st.secrets.get("DEMO_MAX_RUNS")
        if secret_val is not None:
            return int(secret_val)
    except (FileNotFoundError, KeyError):
        pass

    return _DEFAULT_MAX_RUNS


def check_rate_limit() -> bool:
    """Check whether the current session is within the pipeline run limit.

    Returns ``True`` if the run is allowed (and increments the counter).
    Returns ``False`` if the limit has been reached (and shows a warning).

    Call this *before* spawning the pipeline subprocess.  If it returns
    ``False``, skip the run entirely.
    """
    max_runs = _get_max_runs()

    # Initialise session counters on first call.
    if "demo_run_count" not in st.session_state:
        st.session_state.demo_run_count = 0
        st.session_state.demo_session_start = datetime.now(timezone.utc).isoformat()

    if st.session_state.demo_run_count >= max_runs:
        st.warning(
            f"Demo limit reached ({max_runs} pipeline runs per session). "
            "Clone the repo and use your own API key for unlimited access.\n\n"
            "```bash\n"
            "git clone https://github.com/kaden-g/solo.git\n"
            "cp .env.example .env  # add your key\n"
            "streamlit run dashboard/app.py\n"
            "```"
        )
        return False

    st.session_state.demo_run_count += 1
    return True


def get_remaining_runs() -> int:
    """Return how many pipeline runs the visitor has left in this session."""
    max_runs = _get_max_runs()
    used = st.session_state.get("demo_run_count", 0)
    return max(0, max_runs - used)
