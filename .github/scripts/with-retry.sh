#!/usr/bin/env bash
# Run a command with bounded retries + exponential backoff.
#
# Used by CI to wrap cosign sign / attest calls. The GitHub Actions OIDC
# token endpoint occasionally returns a non-JSON error body under load,
# which cosign surfaces as "invalid character 'u' looking for beginning
# of value" and fails the job outright. The signing operation itself is
# idempotent at the registry layer (same digest, same identity, same
# annotations), so a blind retry is safe.
#
# Usage:  ./with-retry.sh <command> [args...]
# Tuning: WITH_RETRY_MAX_ATTEMPTS (default 4), WITH_RETRY_INITIAL_DELAY_S
#         (default 5). Delay doubles between attempts.

set -u

max_attempts="${WITH_RETRY_MAX_ATTEMPTS:-4}"
delay="${WITH_RETRY_INITIAL_DELAY_S:-5}"
attempt=1

while true; do
    # Capture the command's exit code directly. Using `if "$@"; then` would
    # clobber $? because the `if` construct itself completes successfully.
    "$@"
    rc=$?
    if (( rc == 0 )); then
        exit 0
    fi
    if (( attempt >= max_attempts )); then
        echo "::error::command failed after ${attempt} attempts (rc=${rc}): $*"
        exit "$rc"
    fi
    echo "::warning::attempt ${attempt}/${max_attempts} failed (rc=${rc}), retrying in ${delay}s: $*"
    sleep "$delay"
    delay=$(( delay * 2 ))
    attempt=$(( attempt + 1 ))
done
