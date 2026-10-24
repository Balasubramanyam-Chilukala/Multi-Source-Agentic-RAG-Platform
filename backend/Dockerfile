# syntax=docker/dockerfile:1

# ---- Build stage: resolve the locked dependencies into a virtualenv ----
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.17 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# ---- Runtime stage: no build tooling, non-root user ----
FROM python:3.12-slim AS runtime
RUN groupadd --system atlas && useradd --system --gid atlas --no-create-home atlas
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
COPY eval/datasets ./eval/datasets
COPY --chmod=0755 docker-entrypoint.sh ./docker-entrypoint.sh
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000
USER atlas
EXPOSE 8000
# Migrations run via `python -m app.cli migrate` (compose `migrate` service, or the entrypoint
# when RUN_MIGRATIONS_ON_START=true). See docs/deployment.md.
ENTRYPOINT ["./docker-entrypoint.sh"]
