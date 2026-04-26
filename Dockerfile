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
COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir streamlit plotly

# --- Application layer ------------------------------------------------------
COPY . .

# Install the engine package in editable mode so imports resolve cleanly.
RUN pip install --no-cache-dir -e .

# --- Runtime configuration ---------------------------------------------------
#
# Streamlit settings:
#   - headless mode (no browser auto-open)
#   - bind to 0.0.0.0 so Docker port-mapping works
#   - disable CORS for local dev convenience
#   - disable XSRF protection (safe behind Docker network)
#
# Engine settings:
#   - JSON-lines logging for structured output in container logs
#
ENV STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    STREAMLIT_SERVER_ENABLE_CORS=false \
    STREAMLIT_SERVER_ENABLE_XSRF_PROTECTION=false \
    AE_LOG_FORMAT=json

EXPOSE 8501

# Health check — Streamlit serves a /_stcore/health endpoint.
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')"

ENTRYPOINT ["streamlit", "run", "dashboard/app.py"]
