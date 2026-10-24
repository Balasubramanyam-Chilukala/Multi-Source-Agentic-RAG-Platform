#!/bin/sh
# Container entrypoint.
#
# Migrations are forward-only and reviewed in PRs. They run here only when explicitly enabled
# (RUN_MIGRATIONS_ON_START=true), which is the documented strategy for hosts without a separate
# release/pre-deploy step (e.g. Render's free tier). See docs/deployment.md.
set -eu

if [ "${RUN_MIGRATIONS_ON_START:-false}" = "true" ]; then
  python -m app.cli migrate
fi

if [ -n "${DEMO_SEED_PASSWORD:-}" ]; then
  python -m app.cli seed-demo --email "${DEMO_SEED_EMAIL:-demo@atlas.example}" --password "$DEMO_SEED_PASSWORD"
fi

exec uvicorn --factory app.main:create_app --host 0.0.0.0 --port "${PORT:-8000}" \
  --proxy-headers --forwarded-allow-ips="${FORWARDED_ALLOW_IPS:-127.0.0.1}"
