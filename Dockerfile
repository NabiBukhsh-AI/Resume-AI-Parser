# syntax=docker/dockerfile:1.9

# ---------------------------------------------------------------------------
# Build stage: resolve and install dependencies into a self-contained venv.
# ---------------------------------------------------------------------------
FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# Optional extras to install, e.g. "[ui]" for the Streamlit interface. The API image
# stays lean by default; the UI image is built from the same Dockerfile with
# --build-arg EXTRAS='[ui]'.
ARG EXTRAS=""

WORKDIR /app

# Dependency metadata is copied on its own first so the expensive install layer is reused
# whenever only application code changes.
COPY pyproject.toml README.md ./
COPY src ./src

RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv /opt/venv && \
    VIRTUAL_ENV=/opt/venv uv pip install --no-cache ".${EXTRAS}"

# ---------------------------------------------------------------------------
# Runtime stage: no build tools, no package manager, no source tree.
# ---------------------------------------------------------------------------
FROM python:3.13-slim AS runtime

# curl is needed by the healthcheck below; nothing else is installed.
RUN apt-get update && \
    apt-get install --no-install-recommends -y curl && \
    rm -rf /var/lib/apt/lists/* && \
    useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser

COPY --from=builder --chown=appuser:appuser /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    RESUME_PARSER_ENVIRONMENT=production \
    RESUME_PARSER_SERVER__HOST=0.0.0.0 \
    RESUME_PARSER_SERVER__PORT=8000 \
    RESUME_PARSER_OBSERVABILITY__JSON_LOGS=true

WORKDIR /app
USER appuser

EXPOSE 8000

# Readiness, not liveness: this reports 503 when no model has credentials, which is
# exactly when the container should be kept out of a load balancer.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl --fail --silent http://localhost:8000/health/ready || exit 1

# Factory mode, because the app module has no import-time side effects by design.
CMD ["uvicorn", "resume_parser.api.app:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
