# --------------------------------------------------------------------------
# Autonomy Engine — Dashboard Container
#
# Multi-stage build: keeps the final image lean by separating dependency
# installation from the application layer.  Only the lock file is used for
# the install step so Docker can cache the layer until deps actually change.
#
# Why not Alpine?  Many Python wheels (numpy, pandas — Streamlit deps) ship
# pre-built for Debian but not for musl libc, so Alpine builds are slower
# and less reliable.
# --------------------------------------------------------------------------

FROM python:3.11-slim AS base

# --- OS-level deps (git needed for setuptools_scm in some transitive deps) ---
RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# --- Dependency layer (cached until lock file changes) ----------------------
# Install strictly from the pinned lock file so the deployed image is
# reproducible. A previous revision tacked on an unpinned `pip install streamlit
# plotly` here, which left the install state ambiguous — both packages are
# already pinned in requirements.txt, so the second call was a no-op at best
# and a source of chunk-hash drift between deploys at worst (the symptom being
# "Failed to fetch dynamically imported module" for Streamlit's lazy-loaded
# SyntaxHighlighter chunk).
COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.txt

# --- Application layer ------------------------------------------------------
COPY . .

# Install the engine package in editable mode so imports resolve cleanly.
RUN pip install --no-cache-dir -e .

# .dockerignore excludes state/ so runtime artifacts don't bloat the image,
# but dashboard.data_loader.find_project_dir() uses state/'s presence as the
# project-root marker. Pre-create an empty state/ so the dashboard finds itself.
RUN mkdir -p state

# --- Runtime configuration ---------------------------------------------------
#
# Streamlit settings:
#   - headless mode (no browser auto-open)
#   - bind to 0.0.0.0 so the platform port-mapping works (Fly, Render, etc.)
#   - XSRF protection ENABLED (this image gets deployed to public URLs;
#     "safe behind Docker network" is no longer the deployment posture).
#     Note: we deliberately DON'T set STREAMLIT_SERVER_ENABLE_CORS=false —
#     Streamlit auto-overrides it to true when XSRF is on (and warns loudly
#     about the incompatibility), so leaving it at the default keeps logs
#     clean without changing security posture.
#
# Engine settings:
#   - JSON-lines logging for structured output in container logs
#   - AUTONOMY_ENGINE_PROJECT_DIR pinned so find_project_dir() is robust
#     against future cwd changes
#
ENV STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    STREAMLIT_SERVER_ENABLE_XSRF_PROTECTION=true \
    AE_LOG_FORMAT=json \
    AUTONOMY_ENGINE_PROJECT_DIR=/app

EXPOSE 8501

# Health check — Streamlit serves a /_stcore/health endpoint.
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')"

ENTRYPOINT ["streamlit", "run", "dashboard/app.py"]
