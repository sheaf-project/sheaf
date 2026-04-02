#!/usr/bin/env bash
# Start or stop a disposable dev-mode instance with dev tools included.
#
# Usage:
#   ./run_devmode.sh              # start (or restart) the dev-mode stack
#   ./run_devmode.sh --stop       # stop containers (preserves data)
#   ./run_devmode.sh --down       # tear down and remove volumes
#   ./run_devmode.sh --no-build   # start without rebuilding the image
#
# Ports: app=8002, postgres=5434, redis=6381
# Does not conflict with the normal dev stack (8000) or test suite (8001).
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

echo "Starting dev-mode stack (app=:8002, pg=:5434, redis=:6381)..."
$COMPOSE up -d $BUILD_FLAG

echo "Waiting for app..."
for i in $(seq 1 30); do
    if curl -sf http://localhost:8002/health > /dev/null 2>&1; then
        echo "Dev-mode stack ready at http://localhost:8002"
        echo "  API docs: http://localhost:8002/v1/docs"
        echo "  Postgres: localhost:5434 (sheaf/sheafdev)"
        echo "  Redis:    localhost:6381"
        exit 0
    fi
    sleep 1
done

echo "Timed out waiting for app. Check logs:"
echo "  $COMPOSE logs app"
exit 1
