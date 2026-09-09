#!/usr/bin/env bash
# Run the full test suite against multiple server configurations.
#
# Usage: ./run_tests.sh [--no-build] [--jobs N] [config ...]
#
# Spins up an isolated test stack (docker-compose.test.yml), runs pytest
# for each configuration in sequence, then tears everything down.
# Requires Docker and the SHEAF_TEST_DB_URL that points at the test DB.
#
# With no config arguments every configuration runs. Naming one or more
# runs just those: either the full name (selfhosted/rate_limit) or the
# short form (rate_limit). ./run_tests.sh --list prints the names.
#
# --jobs N spreads the selected configurations over N stacks running in
# parallel, each in its own compose project on its own port block. Every
# config's output is captured to a file and replayed one config at a time
# once all slots finish, so nothing interleaves on the terminal. --jobs 1
# (the default) is the classic single-stack sequential run.

set -euo pipefail

# Serial (--jobs 1) runs use the classic fixed project and ports; parallel
# slot workers override these stack variables per slot (see run_slot).
# COMPOSE_PROJECT also reaches pytest as SHEAF_TEST_COMPOSE_PROJECT: the
# import-runner tests `docker compose exec` into the stack and must target
# the project that pytest is pointed at, not a hardcoded name.
COMPOSE_PROJECT="sheaf-test"
COMPOSE="docker compose -p $COMPOSE_PROJECT -f docker-compose.yml -f docker-compose.test.yml"
TEST_URL="http://localhost:8001"
TEST_DB_URL="postgresql+asyncpg://sheaf:sheaftest@localhost:5433/sheaf"
# Most tests drive Redis transitively (sessions, rate limits) via HTTP and
# never touch it from the host. The shield-mode unit tests do though - they
# call get_redis() directly to exercise the state machine and the mass-
# invalidate pass. Expose the test stack's host-mapped Redis URL the same
# way SHEAF_TEST_DB_URL is exposed so those tests work in CI.
TEST_REDIS_URL="redis://localhost:6380/0"
BUILD_FLAG="--build"
# Extra flags for the per-config `up`. Parallel mode sets this to
# --no-build: the app image is built once before the slots fork, and a slot
# must never trigger a build of its own (N racing builds of one tag).
UP_FLAGS=""
# Two slots by default: conservative enough for any dev laptop, still
# roughly halves a full pass. --jobs 1 forces the classic serial path;
# single-config runs (CI's shape) clamp to one slot regardless.
JOBS=2
FAILED=()

# Shared bearer token used by the metrics config row. Random per run so
# it can't accidentally leak into a real deployment.
METRICS_TOKEN="test-metrics-token-$(openssl rand -hex 8 2>/dev/null || echo deadbeef)"

# The conftest fixtures query the DB directly with blind_index() - keyed
# HMAC derived from the encryption key - so the host-side pytest must share
# the same key as the container set in docker-compose.test.yml.
export SHEAF_ENCRYPTION_KEY="0000000000000000000000000000000000000000000000000000000000000000"
export JWT_SECRET_KEY="test-jwt-secret-not-for-production"

# ---------------------------------------------------------------------------
# Configuration table
# ---------------------------------------------------------------------------
# Every configuration this script knows, in run order. Adding a config means
# adding one add_config row below; ALL_CONFIGS and the "[N/TOTAL]" progress
# prefix derive from the table. Field constraints:
#   - compose_extra / pytest_extra are space-separated VAR=VALUE lists, so
#     the values must not contain whitespace (the marks expression, which
#     legitimately does, has a field of its own).
#   - redis controls whether pytest gets SHEAF_TEST_REDIS_URL. Only suites
#     that talk to Redis from the host need it; passing it to every config
#     would let an unrelated suite silently grow a host-side Redis
#     dependency.
#   - target narrows pytest to a path (used instead of a marks expression).

ALL_CONFIGS=()
declare -A CFG_ADMIN CFG_MODE CFG_REDIS CFG_MARKS CFG_TARGET
declare -A CFG_COMPOSE_EXTRA CFG_PYTEST_EXTRA

# add_config NAME ADMIN_AUTH_LEVEL SHEAF_MODE REDIS MARKS TARGET \
#            COMPOSE_EXTRA PYTEST_EXTRA
add_config() {
    local name="$1"
    ALL_CONFIGS+=("$name")
    CFG_ADMIN["$name"]="$2"          # ADMIN_AUTH_LEVEL for the app + pytest
    CFG_MODE["$name"]="$3"           # SHEAF_MODE for the app + pytest
    CFG_REDIS["$name"]="$4"          # 1 = pass SHEAF_TEST_REDIS_URL to pytest
    CFG_MARKS["$name"]="$5"          # pytest -m expression; empty = no filter
    CFG_TARGET["$name"]="$6"         # pytest path target; empty = whole suite
    CFG_COMPOSE_EXTRA["$name"]="$7"  # extra env for compose up
    CFG_PYTEST_EXTRA["$name"]="$8"   # extra env for pytest
}

# 1. Main suite - excludes config-specific marks
add_config "selfhosted/none" none selfhosted 1 \
    "not admin_auth_password and not admin_auth_totp and not saas and not rate_limit" \
    "" "" ""

# 2. Password step-up enforcement
add_config "selfhosted/admin_auth_password" password selfhosted 1 \
    "admin_auth_password" "" "" ""

# 3. TOTP step-up enforcement
add_config "selfhosted/admin_auth_totp" totp selfhosted 1 \
    "admin_auth_totp" "" "" ""

# 4. SaaS mode. The full run (every unmarked test under saas, plus the
# saas-marked ones) is a safety net for an unmarked mode-dependence, but it
# re-runs ~2000 mode-agnostic tests to exercise a handful of saas-specific ones.
# So it is tiered: SHEAF_TEST_SAAS_FULL=true (set by main / release CI) runs the
# full net; otherwise (PRs, local iteration) it runs only the saas-marked tests.
# Export SHEAF_TEST_SAAS_FULL=true locally when you want the full saas run.
SAAS_MARKS="saas"
if [[ "${SHEAF_TEST_SAAS_FULL:-}" == "true" ]]; then SAAS_MARKS=""; fi
add_config "saas/none" none saas 1 "$SAAS_MARKS" "" "" ""

# 5. Rate limiting - low limits so tests can trigger 429s
add_config "selfhosted/rate_limit" none selfhosted 0 "rate_limit" "" \
    "RATE_LIMIT_ENABLED=true RATE_LIMIT_GLOBAL_PER_IP=600 RATE_LIMIT_GLOBAL_WINDOW=60" \
    "SHEAF_TEST_RATE_LIMIT=true"

# 6. Image uploads globally disabled
add_config "selfhosted/uploads_disabled" none selfhosted 0 "uploads_disabled" "" \
    "ALLOW_IMAGE_UPLOADS=false" \
    "SHEAF_TEST_UPLOADS_DISABLED=true"

# 7. Bio images disabled (avatars still allowed)
add_config "selfhosted/bio_uploads_disabled" none selfhosted 0 "bio_uploads_disabled" "" \
    "ALLOW_BIO_IMAGES=false" \
    "SHEAF_TEST_BIO_UPLOADS_DISABLED=true"

# 8. External images disabled (hosted uploads still allowed)
add_config "selfhosted/external_images_disabled" none selfhosted 0 "external_images_disabled" "" \
    "ALLOW_EXTERNAL_IMAGES=false" \
    "SHEAF_TEST_EXTERNAL_IMAGES_DISABLED=true"

# 9. Metrics endpoint - METRICS_BIND=main + a bearer token. Runs only
# the metrics test file so the rest of the suite (which assumes /metrics
# isn't on the app port) doesn't get confused.
add_config "selfhosted/metrics" none selfhosted 1 "" "tests/test_metrics.py" \
    "METRICS_ENABLED=true METRICS_BIND=main METRICS_TOKEN=$METRICS_TOKEN" \
    "SHEAF_TEST_METRICS_TOKEN=$METRICS_TOKEN"

# 10. Public profiles enabled - the anonymous read surface. The stack now
# defaults this on (see docker-compose.test.yml for why), so this row is about
# running the anonymous-surface tests rather than about flipping the setting;
# it sets the value explicitly anyway so the row stands on its own.
add_config "selfhosted/public_profiles" none selfhosted 1 "public_profiles" "" \
    "PUBLIC_PROFILES_ENABLED=true" \
    "SHEAF_TEST_PUBLIC_PROFILES=true"

# 11. Public profiles disabled - the other side of the same switch, and the
# production default. Pins that the anonymous router 404s wholesale, and that
# the owner-side sharing API splits the way it promises with the surface off:
# revoking, rotating, removing and narrowing still work and the audit still
# lists the dormant grants, while EVERY loosening is refused - creating a view,
# adding a member/field/group, re-syncing a group, turning a flag on, and
# publishing. Also pins that a row staged before the switch went off still
# promotes on its own schedule.
add_config "selfhosted/public_profiles_off" none selfhosted 1 "public_profiles_off" "" \
    "PUBLIC_PROFILES_ENABLED=false" \
    "SHEAF_TEST_PUBLIC_PROFILES_OFF=true"

# ---------------------------------------------------------------------------
# Argument parsing / config selection
# ---------------------------------------------------------------------------
# No config args = run everything (the CI path). Named configs run alone,
# accepted as either the full name or the part after the slash.

SELECTED=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-build)
            BUILD_FLAG=""
            ;;
        --list)
            printf '%s\n' "${ALL_CONFIGS[@]}"
            exit 0
            ;;
        --jobs)
            if [[ $# -lt 2 ]]; then
                echo "--jobs needs a value" >&2
                exit 2
            fi
            JOBS="$2"
            shift
            ;;
        --jobs=*)
            JOBS="${1#--jobs=}"
            ;;
        *)
            matches=()
            for c in "${ALL_CONFIGS[@]}"; do
                if [[ "$1" == "$c" ]]; then
                    matches=("$c")
                    break
                fi
                [[ "$1" == "${c#*/}" ]] && matches+=("$c")
            done
            if [[ ${#matches[@]} -eq 0 ]]; then
                echo "Unknown config: $1" >&2
                echo "Known configs:" >&2
                printf '  %s\n' "${ALL_CONFIGS[@]}" >&2
                exit 2
            fi
            if [[ ${#matches[@]} -gt 1 ]]; then
                # "none" is a suffix of more than one config; make the caller
                # say which rather than silently picking one.
                echo "Ambiguous config '$1' matches:" >&2
                printf '  %s\n' "${matches[@]}" >&2
                exit 2
            fi
            SELECTED+=("${matches[0]}")
            ;;
    esac
    shift
done

if ! [[ "$JOBS" =~ ^[1-9][0-9]*$ ]]; then
    echo "--jobs needs a positive integer, got '$JOBS'" >&2
    exit 2
fi

# The configs that will actually run, in canonical table order (which is
# also the order their output prints in, serial or parallel).
ORDERED=()
for c in "${ALL_CONFIGS[@]}"; do
    if [[ ${#SELECTED[@]} -eq 0 ]]; then
        ORDERED+=("$c")
    else
        for s in "${SELECTED[@]}"; do
            if [[ "$s" == "$c" ]]; then
                ORDERED+=("$c")
                break
            fi
        done
    fi
done
TOTAL_CONFIGS=${#ORDERED[@]}

# Each config keeps its canonical position for the "[N/TOTAL]" banner no
# matter which slot runs it, so replayed parallel output reads the same as
# a serial run.
declare -A CFG_INDEX_OF
idx=0
for c in "${ORDERED[@]}"; do
    idx=$((idx + 1))
    CFG_INDEX_OF["$c"]=$idx
done

# More slots than configs just means empty slots; don't allocate them.
if [[ "$JOBS" -gt "$TOTAL_CONFIGS" ]]; then
    JOBS=$TOTAL_CONFIGS
fi

# ---------------------------------------------------------------------------
# Single-runner lock
# ---------------------------------------------------------------------------
# The test stacks use fixed compose project names (sheaf-test, or
# sheaf-test-N per parallel slot) on fixed host ports (8001/5433/6380,
# offset per slot). Two runs from different checkouts race on those ports
# and the shared volumes and wedge each other, so the whole run - all
# slots included - sits under one system-wide advisory lock and concurrent
# runs queue instead of colliding. fd stays open for the life of the
# script; the lock releases automatically on exit. Override the path with
# SHEAF_TEST_LOCK if /tmp isn't shared between your checkouts.
LOCK_FILE="${SHEAF_TEST_LOCK:-/tmp/sheaf-test-stack.lock}"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "Another test run holds the stack lock ($LOCK_FILE); waiting..."
    flock 9
fi
echo "Acquired test-stack lock ($LOCK_FILE)."

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

wait_for_app() {
    local url="$1"
    echo "Waiting for app at $url..."
    for i in $(seq 1 30); do
        if curl -sf "$url/v1/docs" > /dev/null 2>&1; then
            # Docs endpoint passes before DB pool is warm. Wait for a DB-touching
            # endpoint to respond (401 = app is up AND DB is reachable).
            for j in $(seq 1 10); do
                status=$(curl -s -o /dev/null -w "%{http_code}" "$url/v1/auth/me" 2>/dev/null || echo "0")
                if [[ "$status" == "401" ]]; then
                    echo "App ready."
                    return 0
                fi
                sleep 1
            done
            echo "ERROR: /v1/docs is up but the DB-backed endpoint never"
            echo "       returned 401. The DB pool failed to warm up;"
            echo "       proceeding would mask that as test flake."
            $COMPOSE logs app | tail -30
            exit 1
        fi
        sleep 2
    done
    echo "ERROR: app did not become ready in time."
    $COMPOSE logs app | tail -30
    exit 1
}

# Run one configuration against the stack described by the current COMPOSE /
# TEST_URL / TEST_DB_URL / TEST_REDIS_URL values (the serial globals, or a
# slot worker's overrides - which is why this reads those instead of taking
# them as arguments). Prints the banner and the PASSED/FAILED line; the
# return status says which. Callers collect failures.
run_one() {
    local name="$1"
    local index="$2"
    local admin="${CFG_ADMIN[$name]}"
    local mode="${CFG_MODE[$name]}"

    echo ""
    echo "================================================================"
    echo "[${index}/${TOTAL_CONFIGS}] Config: $name  (ADMIN_AUTH_LEVEL=$admin  SHEAF_MODE=$mode)"
    echo "================================================================"

    # Reconfigure the app in place: compose recreates only the app container
    # when its environment changes; db/redis (and their data) stay up.
    local compose_env=(ADMIN_AUTH_LEVEL="$admin" SHEAF_MODE="$mode")
    local extra=()
    if [[ -n "${CFG_COMPOSE_EXTRA[$name]}" ]]; then
        read -r -a extra <<< "${CFG_COMPOSE_EXTRA[$name]}"
        compose_env+=("${extra[@]}")
    fi
    env "${compose_env[@]}" $COMPOSE up -d $UP_FLAGS app || exit 1

    wait_for_app "$TEST_URL"

    # Build pytest args as an array to avoid quoting/word-splitting issues.
    local pytest_args=(-q)
    if [[ -n "${CFG_MARKS[$name]}" ]]; then
        pytest_args+=(-m "${CFG_MARKS[$name]}")
    fi
    if [[ -n "${CFG_TARGET[$name]}" ]]; then
        pytest_args+=("${CFG_TARGET[$name]}")
    fi

    local pytest_env=(SHEAF_TEST_URL="$TEST_URL" SHEAF_TEST_DB_URL="$TEST_DB_URL")
    if [[ "${CFG_REDIS[$name]}" == "1" ]]; then
        pytest_env+=(SHEAF_TEST_REDIS_URL="$TEST_REDIS_URL")
    fi
    pytest_env+=(
        SHEAF_TEST_ADMIN_AUTH_LEVEL="$admin"
        SHEAF_TEST_MODE="$mode"
        SHEAF_TEST_COMPOSE_PROJECT="$COMPOSE_PROJECT"
    )
    if [[ -n "${CFG_PYTEST_EXTRA[$name]}" ]]; then
        read -r -a extra <<< "${CFG_PYTEST_EXTRA[$name]}"
        pytest_env+=("${extra[@]}")
    fi

    if env "${pytest_env[@]}" uv run --extra dev pytest "${pytest_args[@]}"; then
        echo "PASSED: $name"
        return 0
    else
        echo "FAILED: $name"
        return 1
    fi
}

# Parallel slot worker. Runs as a background subshell, so reassigning the
# stack globals here is naturally scoped to the slot. Slot s publishes on
# app 8000+s / db 5432+s / redis 6379+s, which makes slot 1 land exactly on
# the classic serial ports. The exported SHEAF_TEST_*_PORT variables feed
# the port interpolation in docker-compose.test.yml.
run_slot() {
    local slot="$1"
    shift
    local configs=("$@")
    local app_port=$((8000 + slot))

    COMPOSE_PROJECT="sheaf-test-$slot"
    COMPOSE="docker compose -p $COMPOSE_PROJECT -f docker-compose.yml -f docker-compose.test.yml"
    TEST_URL="http://localhost:$app_port"
    TEST_DB_URL="postgresql+asyncpg://sheaf:sheaftest@localhost:$((5432 + slot))/sheaf"
    TEST_REDIS_URL="redis://localhost:$((6379 + slot))/0"
    export SHEAF_TEST_APP_PORT="$app_port"
    export SHEAF_TEST_DB_PORT=$((5432 + slot))
    export SHEAF_TEST_REDIS_PORT=$((6379 + slot))

    # Each slot owns its stack's teardown. INT/TERM route through the EXIT
    # trap so a killed slot still cleans up after itself.
    trap '$COMPOSE down -v --remove-orphans >/dev/null 2>&1 || true' EXIT
    trap 'exit 143' INT TERM

    local cfg safe
    for cfg in "${configs[@]}"; do
        safe="${cfg//\//_}"
        # The subshell contains hard failures (compose up errors,
        # wait_for_app giving up and exiting): they fail this config and
        # the slot moves on, instead of taking the whole slot down with
        # later configs unrecorded.
        if (run_one "$cfg" "${CFG_INDEX_OF[$cfg]}") >"$RUN_DIR/$safe.log" 2>&1; then
            echo "PASSED" >"$RUN_DIR/$safe.result"
            echo "[slot $slot] $cfg: PASSED"
        else
            echo "FAILED" >"$RUN_DIR/$safe.result"
            echo "[slot $slot] $cfg: FAILED"
        fi
    done
}

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if [[ "$JOBS" -eq 1 ]]; then
    # Serial: the classic flow. One fixed project, live streaming output.
    cleanup() {
        echo ""
        echo "Tearing down test stack..."
        $COMPOSE down -v --remove-orphans 2>/dev/null || true
    }
    trap cleanup EXIT

    echo "Starting test stack..."
    ADMIN_AUTH_LEVEL=none SHEAF_MODE=selfhosted \
        $COMPOSE up $BUILD_FLAG -d

    wait_for_app "$TEST_URL"

    for cfg in "${ORDERED[@]}"; do
        if ! run_one "$cfg" "${CFG_INDEX_OF[$cfg]}"; then
            FAILED+=("$cfg")
        fi
    done
else
    # Parallel: partition the configs over the slots, one background worker
    # per slot, then replay the captured output in canonical order.
    UP_FLAGS="--no-build"
    RUN_DIR="$(mktemp -d -t sheaf-test-run.XXXXXX)"

    # selfhosted/none runs the bulk of the suite and dominates wall clock,
    # so when selected it gets slot 1 to itself and everything else
    # round-robins over the remaining slots. Otherwise plain round-robin
    # over all slots. (Names contain no spaces, so a space-separated list
    # per slot is safe.)
    SLOT_LISTS=()
    for ((s = 1; s <= JOBS; s++)); do
        SLOT_LISTS[s]=""
    done
    has_main=0
    for cfg in "${ORDERED[@]}"; do
        if [[ "$cfg" == "selfhosted/none" ]]; then
            has_main=1
        fi
    done
    if [[ $has_main -eq 1 && "$JOBS" -gt 1 ]]; then
        SLOT_LISTS[1]="selfhosted/none"
        rr=2
        for cfg in "${ORDERED[@]}"; do
            if [[ "$cfg" == "selfhosted/none" ]]; then
                continue
            fi
            SLOT_LISTS[rr]="${SLOT_LISTS[rr]} $cfg"
            rr=$((rr + 1))
            if [[ $rr -gt $JOBS ]]; then
                rr=2
            fi
        done
    else
        rr=1
        for cfg in "${ORDERED[@]}"; do
            SLOT_LISTS[rr]="${SLOT_LISTS[rr]} $cfg"
            rr=$((rr + 1))
            if [[ $rr -gt $JOBS ]]; then
                rr=1
            fi
        done
    fi

    SLOT_PIDS=()
    SLOT_IDS=()

    # Slots normally tear their own stacks down; the master pass is the
    # belt-and-braces for the interrupted case (a `down` on an already-gone
    # project is quick and quiet). The log dir goes last: by now its
    # contents have been replayed.
    master_cleanup() {
        local s
        for s in "${SLOT_IDS[@]}"; do
            docker compose -p "sheaf-test-$s" \
                -f docker-compose.yml -f docker-compose.test.yml \
                down -v --remove-orphans 2>/dev/null || true
        done
        rm -rf "$RUN_DIR"
    }
    trap master_cleanup EXIT
    on_interrupt() {
        echo ""
        echo "Interrupted; stopping slot workers..."
        kill "${SLOT_PIDS[@]}" 2>/dev/null || true
        exit 130
    }
    trap on_interrupt INT TERM

    # Build the app image once before forking. docker-compose.test.yml pins
    # image: sheaf-test-app (the tag a plain sheaf-test project build
    # produces anyway), so every slot project runs this one image.
    if [[ -n "$BUILD_FLAG" ]]; then
        echo "Building app image (shared by all slots)..."
        $COMPOSE build app
    fi

    # Warm the uv environment once. Every slot invokes `uv run --extra dev`
    # at roughly the same moment; N concurrent first-time syncs of a cold
    # venv are slow at best, and this is a no-op when the venv is current.
    uv run --extra dev python -c '' >/dev/null

    for ((s = 1; s <= JOBS; s++)); do
        read -r -a slot_cfgs <<< "${SLOT_LISTS[s]}"
        if [[ ${#slot_cfgs[@]} -eq 0 ]]; then
            continue
        fi
        echo "Slot $s (project sheaf-test-$s, app port $((8000 + s))): ${slot_cfgs[*]}"
        run_slot "$s" "${slot_cfgs[@]}" &
        SLOT_PIDS+=($!)
        SLOT_IDS+=("$s")
    done

    echo "Waiting for ${#SLOT_PIDS[@]} slot(s) to finish..."
    for pid in "${SLOT_PIDS[@]}"; do
        wait "$pid" || true
    done

    # Replay every config's captured output, serially, in canonical order.
    echo ""
    echo "All slots finished; per-config output follows."
    for cfg in "${ORDERED[@]}"; do
        safe="${cfg//\//_}"
        if [[ -f "$RUN_DIR/$safe.log" ]]; then
            cat "$RUN_DIR/$safe.log"
        else
            echo ""
            echo "================================================================"
            echo "[${CFG_INDEX_OF[$cfg]}/${TOTAL_CONFIGS}] Config: $cfg"
            echo "================================================================"
            echo "No output captured (slot never reached this config)."
        fi
        if [[ "$(cat "$RUN_DIR/$safe.result" 2>/dev/null)" != "PASSED" ]]; then
            FAILED+=("$cfg")
        fi
    done
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
