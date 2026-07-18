#!/bin/sh
# Backend program for the AIO image (run by supervisord as the sheaf user).
# Mirrors the split backend image's CMD: clear stale prometheus multiproc
# files, apply migrations, then run uvicorn on loopback (Caddy fronts it).
set -e

rm -f "${PROMETHEUS_MULTIPROC_DIR}"/*.db 2>/dev/null || true

alembic upgrade head

exec uvicorn sheaf.main:app \
    --host 127.0.0.1 \
    --port "${SHEAF_PORT:-8000}" \
    --proxy-headers \
    --forwarded-allow-ips="${FORWARDED_ALLOW_IPS:-127.0.0.1}"
