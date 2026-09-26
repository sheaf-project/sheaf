#!/usr/bin/env bash
# Start or stop a disposable dev-mode instance with dev tools included.
#
# Usage:
#   ./run_devmode.sh              # start (or restart) the dev-mode stack
#   ./run_devmode.sh --stop       # stop containers (preserves data)
#   ./run_devmode.sh --down       # tear down and remove volumes
#   ./run_devmode.sh --no-build   # start without rebuilding the image
#
# Ports: app=8050, postgres=5450, redis=6450
# Does not conflict with the normal dev stack (8000) or the test suite (serial
# 8001/5433/6380, parallel slot s on 8000+s/5432+s/6379+s).
#
# To enable periodic demo wipe:
#   DEMO_WIPE_ENABLED=true ./run_devmode.sh

set -euo pipefail

COMPOSE="docker compose -p sheaf-devmode -f docker-compose.yml -f docker-compose.devmode.yml"

if [[ "${1:-}" == "--stop" ]]; then
    echo "Stopping dev-mode stack (data preserved)..."
    $COMPOSE down
    echo "Done."
    exit 0
fi

if [[ "${1:-}" == "--down" ]]; then
    echo "Tearing down dev-mode stack and removing volumes..."
    $COMPOSE down -v
    echo "Done."
    exit 0
fi

BUILD_FLAG="--build"
if [[ "${1:-}" == "--no-build" ]]; then
    BUILD_FLAG=""
fi

echo "Starting dev-mode stack (app=:8050, pg=:5450, redis=:6450)..."
$COMPOSE up -d $BUILD_FLAG

echo "Waiting for app..."
for i in $(seq 1 30); do
    if curl -sf http://localhost:8050/health > /dev/null 2>&1; then
        echo "Dev-mode stack ready at http://localhost:8050"
        echo "  API docs: http://localhost:8050/v1/docs"
        echo "  Postgres: localhost:5450 (sheaf/sheafdev)"
        echo "  Redis:    localhost:6450"
        exit 0
    fi
    sleep 1
done

echo "Timed out waiting for app. Check logs:"
echo "  $COMPOSE logs app"
exit 1
