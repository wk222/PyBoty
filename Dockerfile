# Multi-stage lean Docker build for PyBot
FROM python:3.10-slim AS builder

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install modern python packager 'uv'
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

# Copy packaging configuration
COPY pyproject.toml README.md ./

# Create virtual environment and sync dependencies
RUN uv venv .venv
ENV PATH="/app/.venv/bin:${PATH}"
RUN uv pip install -e .[all-llm]

# Final production stage
FROM python:3.10-slim

WORKDIR /app

# Copy virtual environment and source code
COPY --from=builder /app/.venv /app/.venv
COPY . .

ENV PATH="/app/.venv/bin:${PATH}"
ENV PYBOT_WEB_PORT=8000
ENV PYTHONUNBUFFERED=1
ENV PYBOT_RUNTIME_HOME=/app/workspace/.runtime

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" || exit 1

# Start PyBot Web Service
CMD ["python", "service_mode.py"]
