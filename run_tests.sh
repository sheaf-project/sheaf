#!/usr/bin/env bash
# Run the full test suite against multiple server configurations.
#
# Usage: ./run_tests.sh [--no-build]
#
# Spins up an isolated test stack (docker-compose.test.yml), runs pytest
# for each configuration in sequence, then tears everything down.
# Requires Docker and the SHEAF_TEST_DB_URL that points at the test DB.

set -euo pipefail

COMPOSE="docker compose -p sheaf-test -f docker-compose.yml -f docker-compose.test.yml"
TEST_URL="http://localhost:8001"
TEST_DB_URL="postgresql+asyncpg://sheaf:sheaftest@localhost:5433/sheaf"
BUILD_FLAG="--build"
FAILED=()

# The conftest fixtures query the DB directly with blind_index() — keyed
# HMAC derived from the encryption key — so the host-side pytest must share
# the same key as the container set in docker-compose.test.yml.
export SHEAF_ENCRYPTION_KEY="0000000000000000000000000000000000000000000000000000000000000000"
export JWT_SECRET_KEY="test-jwt-secret-not-for-production"

if [[ "${1:-}" == "--no-build" ]]; then
    BUILD_FLAG=""
fi

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

wait_for_app() {
    echo "Waiting for app at $TEST_URL..."
    for i in $(seq 1 30); do
        if curl -sf "$TEST_URL/v1/docs" > /dev/null 2>&1; then
            # Docs endpoint passes before DB pool is warm. Wait for a DB-touching
            # endpoint to respond (401 = app is up AND DB is reachable).
            for j in $(seq 1 10); do
                status=$(curl -s -o /dev/null -w "%{http_code}" "$TEST_URL/v1/auth/me" 2>/dev/null || echo "0")
                if [[ "$status" == "401" ]]; then
                    echo "App ready."
                    return 0
                fi
                sleep 1
            done
            echo "App ready (docs up, DB check timed out — proceeding anyway)."
            return 0
        fi
        sleep 2
    done
    echo "ERROR: app did not become ready in time."
    $COMPOSE logs app | tail -30
    exit 1
}

run_config() {
    local name="$1"
    local admin_auth_level="$2"
    local sheaf_mode="$3"
    local marks_expr="${4:-}"   # marks expression passed to -m; empty = run all

    echo ""
    echo "================================================================"
    echo "Config: $name  (ADMIN_AUTH_LEVEL=$admin_auth_level  SHEAF_MODE=$sheaf_mode)"
    echo "================================================================"

    ADMIN_AUTH_LEVEL="$admin_auth_level" SHEAF_MODE="$sheaf_mode" \
        $COMPOSE up -d app

    wait_for_app

    # Build pytest args as an array to avoid quoting/word-splitting issues.
    local pytest_args=(-q)
    if [[ -n "$marks_expr" ]]; then
        pytest_args+=(-m "$marks_expr")
    fi

    if SHEAF_TEST_URL="$TEST_URL" \
       SHEAF_TEST_DB_URL="$TEST_DB_URL" \
       SHEAF_TEST_ADMIN_AUTH_LEVEL="$admin_auth_level" \
       SHEAF_TEST_MODE="$sheaf_mode" \
       uv run --extra dev pytest "${pytest_args[@]}"; then
        echo "PASSED: $name"
    else
        echo "FAILED: $name"
        FAILED+=("$name")
    fi
}

# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

cleanup() {
    echo ""
    echo "Tearing down test stack..."
    $COMPOSE down -v --remove-orphans 2>/dev/null || true
}
trap cleanup EXIT

echo "Starting test stack..."
ADMIN_AUTH_LEVEL=none SHEAF_MODE=selfhosted \
    $COMPOSE up $BUILD_FLAG -d

wait_for_app

# ---------------------------------------------------------------------------
# Test runs
# ---------------------------------------------------------------------------

# 1. Main suite — excludes config-specific marks
run_config "selfhosted/none" "none" "selfhosted" \
    "not admin_auth_password and not admin_auth_totp and not saas and not rate_limit"

# 2. Password step-up enforcement
run_config "selfhosted/admin_auth_password" "password" "selfhosted" \
    "admin_auth_password"

# 3. TOTP step-up enforcement
run_config "selfhosted/admin_auth_totp" "totp" "selfhosted" \
    "admin_auth_totp"

# 4. SaaS mode — run all tests; conftest skips password/totp marks, runs saas marks
run_config "saas/none" "none" "saas"

# 5. Rate limiting — low limits so tests can trigger 429s
echo ""
echo "================================================================"
echo "Config: selfhosted/rate_limit"
echo "================================================================"

ADMIN_AUTH_LEVEL=none SHEAF_MODE=selfhosted \
    RATE_LIMIT_ENABLED=true RATE_LIMIT_GLOBAL_PER_IP=600 RATE_LIMIT_GLOBAL_WINDOW=60 \
    $COMPOSE up -d app

wait_for_app

if SHEAF_TEST_URL="$TEST_URL" \
   SHEAF_TEST_DB_URL="$TEST_DB_URL" \
   SHEAF_TEST_ADMIN_AUTH_LEVEL=none \
   SHEAF_TEST_MODE=selfhosted \
   SHEAF_TEST_RATE_LIMIT=true \
   uv run --extra dev pytest -q -m "rate_limit"; then
    echo "PASSED: selfhosted/rate_limit"
else
    echo "FAILED: selfhosted/rate_limit"
    FAILED+=("selfhosted/rate_limit")
fi

# 6. Image uploads globally disabled
echo ""
echo "================================================================"
echo "Config: selfhosted/uploads_disabled"
echo "================================================================"

ADMIN_AUTH_LEVEL=none SHEAF_MODE=selfhosted ALLOW_IMAGE_UPLOADS=false \
    $COMPOSE up -d app

wait_for_app

if SHEAF_TEST_URL="$TEST_URL" \
   SHEAF_TEST_DB_URL="$TEST_DB_URL" \
   SHEAF_TEST_ADMIN_AUTH_LEVEL=none \
   SHEAF_TEST_MODE=selfhosted \
   SHEAF_TEST_UPLOADS_DISABLED=true \
   uv run --extra dev pytest -q -m "uploads_disabled"; then
    echo "PASSED: selfhosted/uploads_disabled"
else
    echo "FAILED: selfhosted/uploads_disabled"
    FAILED+=("selfhosted/uploads_disabled")
fi

# 7. Bio images disabled (avatars still allowed)
echo ""
echo "================================================================"
echo "Config: selfhosted/bio_uploads_disabled"
echo "================================================================"

ADMIN_AUTH_LEVEL=none SHEAF_MODE=selfhosted ALLOW_BIO_IMAGES=false \
    $COMPOSE up -d app

wait_for_app

if SHEAF_TEST_URL="$TEST_URL" \
   SHEAF_TEST_DB_URL="$TEST_DB_URL" \
   SHEAF_TEST_ADMIN_AUTH_LEVEL=none \
   SHEAF_TEST_MODE=selfhosted \
   SHEAF_TEST_BIO_UPLOADS_DISABLED=true \
   uv run --extra dev pytest -q -m "bio_uploads_disabled"; then
    echo "PASSED: selfhosted/bio_uploads_disabled"
else
    echo "FAILED: selfhosted/bio_uploads_disabled"
    FAILED+=("selfhosted/bio_uploads_disabled")
fi

# 8. External images disabled (hosted uploads still allowed)
echo ""
echo "================================================================"
echo "Config: selfhosted/external_images_disabled"
echo "================================================================"

ADMIN_AUTH_LEVEL=none SHEAF_MODE=selfhosted ALLOW_EXTERNAL_IMAGES=false \
    $COMPOSE up -d app

wait_for_app

if SHEAF_TEST_URL="$TEST_URL" \
   SHEAF_TEST_DB_URL="$TEST_DB_URL" \
   SHEAF_TEST_ADMIN_AUTH_LEVEL=none \
   SHEAF_TEST_MODE=selfhosted \
   SHEAF_TEST_EXTERNAL_IMAGES_DISABLED=true \
   uv run --extra dev pytest -q -m "external_images_disabled"; then
    echo "PASSED: selfhosted/external_images_disabled"
else
    echo "FAILED: selfhosted/external_images_disabled"
    FAILED+=("selfhosted/external_images_disabled")
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo ""
echo "================================================================"
if [[ ${#FAILED[@]} -eq 0 ]]; then
    echo "All configurations passed."
    exit 0
else
    echo "FAILED configurations:"
    for f in "${FAILED[@]}"; do
        echo "  - $f"
    done
    exit 1
fi
