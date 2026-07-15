# ─────────────────────────────────────────────────────────────────────────────
# La Bella Vista — Dockerfile
# Build:  docker build -t restaurant-agent .
# Run:    docker compose up --build   (preferred — handles .env and volumes)
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.12-slim

# Install only what's needed at build time, then clear the apt cache so
# the layer stays small.
RUN apt-get update && apt-get install -y --no-install-recommends \
        sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Non-root user as required by the project spec.
RUN useradd --create-home --shell /bin/bash agent
WORKDIR /app

# Copy and install Python dependencies first so Docker caches this layer
# separately from the source code — a source-only change doesn't re-run pip.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the full project source.
COPY --chown=agent:agent . .

# The db and logs directories must exist and be owned by the non-root
# user before their named volumes are mounted over them — a new named
# volume inherits ownership from whatever's already at that path in the
# image, so without this chown the volume ends up root-owned and the
# non-root `agent` user can't write to it (this bit /app/logs specifically:
# every log_event() call — i.e. every single turn — failed with
# PermissionError until this was added).
RUN mkdir -p /app/db /app/logs && chown -R agent:agent /app/db /app/logs

# Switch to the non-root user for everything that follows.
USER agent

# Environment variables with safe defaults — the real ANTHROPIC_API_KEY is
# always injected at runtime via compose.yaml / .env, never baked in here.
ENV APP_PORT=7860 \
    DATA_DIR=/app/data \
    LOG_PATH=/app/logs/agent_trace.jsonl \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE ${APP_PORT}

# Entrypoint: initialise the database (idempotent — skips seeding if rows
# already exist) then launch the Gradio app.
CMD ["sh", "-c", "python db/init_db.py && python app.py"]

# Health check: polls the Gradio root every 30 s. The container is marked
# healthy once it responds with HTTP 200, which is also when compose
# considers it fully up.
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${APP_PORT}')" \
    || exit 1
