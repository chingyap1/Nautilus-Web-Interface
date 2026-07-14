# Phase 0, Finding #9: Aligned Python version with root Dockerfile (3.12-slim).
# Uses uv for dependency resolution to match the root pyproject.toml,
# ensuring consistent NautilusTrader versions across all services.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install uv (the project's package manager — matches root Dockerfile)
RUN pip install --no-cache-dir uv

# Copy the full project tree so uv can resolve dependencies from pyproject.toml.
# This ensures the NWI backend uses the same NautilusTrader version as the agent.
COPY . .

# Install runtime dependencies only (no dev extras)
RUN uv sync --no-dev

# Create data directory and non-root user for security
RUN mkdir -p /app/data \
    && addgroup --system appgroup \
    && adduser --system --ingroup appgroup --no-create-home appuser \
    && chown -R appuser:appgroup /app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

CMD ["uv", "run", "python", "nautilus_fastapi.py"]
