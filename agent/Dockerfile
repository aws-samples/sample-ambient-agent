FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# All environment variables in one layer
ENV UV_SYSTEM_PYTHON=1 UV_COMPILE_BYTECODE=1 PYTHONUNBUFFERED=1 \
    AWS_REGION=us-east-1 AWS_DEFAULT_REGION=us-east-1 \
    DOCKER_CONTAINER=1

COPY requirements.txt requirements.txt

# Create non-root user before copying source
RUN useradd -m -u 1000 appuser && \
    uv pip install --no-cache-dir -r requirements.txt && \
    uv pip install --no-cache-dir aws-opentelemetry-distro>=0.10.1

EXPOSE 8080 8000

# Copy entire project as non-root user
COPY --chown=appuser:appuser . .
USER appuser

# Add healthcheck
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health').read()" || exit 1

# Use the full module path
CMD ["opentelemetry-instrument", "python", "-m", "agent"]
