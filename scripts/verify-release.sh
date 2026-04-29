#!/bin/sh
# Sheaf release verification.
#
# Fetches /v1/version from a Sheaf instance, verifies the corresponding
# Docker image was signed by Sheaf's official CI workflow via sigstore/cosign
# (keyless OIDC, recorded in Rekor), and checks the signed git_sha annotation
# matches what the instance reports.
#
# Usage:
#   verify-release.sh <instance-url>
#   verify-release.sh https://your-sheaf-instance
#
# Exit codes:
#   0  — verified
#   1  — verification failed (signature, mismatch, or unreachable instance)
#   64 — bad usage
#   69 — required tool missing

set -eu

usage() {
    cat <<'EOF' >&2
usage: verify-release.sh <instance-url>

Verifies that the Sheaf instance at <instance-url> is running a Docker image
signed by Sheaf's official CI workflow.

Requires: curl, jq, cosign.
EOF
    exit 64
}

require() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "error: required tool not found: $1" >&2
        echo "  install: $2" >&2
        exit 69
    fi
}

INSTANCE="${1:-}"
[ -z "$INSTANCE" ] && usage

require curl   "https://curl.se/"
require jq     "https://stedolan.github.io/jq/"
require cosign "https://docs.sigstore.dev/cosign/installation/"

INSTANCE="${INSTANCE%/}"
REGISTRY="ghcr.io/sheaf-project"
IMAGE_NAME="sheaf"

echo "→ Fetching $INSTANCE/v1/version"
VERSION_JSON=$(curl -fsS "$INSTANCE/v1/version") || {
    echo "FAIL: could not reach $INSTANCE/v1/version" >&2
    exit 1
}

GIT_COMMIT=$(echo "$VERSION_JSON" | jq -r '.git_commit // empty')
GIT_TAG=$(echo "$VERSION_JSON" | jq -r '.git_tag // empty')
APP_VERSION=$(echo "$VERSION_JSON" | jq -r '.version // empty')

echo "  app version: ${APP_VERSION:-<unknown>}"
echo "  git commit:  ${GIT_COMMIT:-<unknown>}"
echo "  git tag:     ${GIT_TAG:-<unset>}"

if [ -z "$GIT_COMMIT" ]; then
    echo "FAIL: instance reports no git commit (built from source without CI?)" >&2
    exit 1
fi

# Tagged releases pin to the version tag; mainline builds use the sha tag.
if [ -n "$GIT_TAG" ]; then
    REF="${REGISTRY}/${IMAGE_NAME}:${GIT_TAG#v}"
else
    REF="${REGISTRY}/${IMAGE_NAME}:sha-${GIT_COMMIT}"
fi

echo
echo "→ Verifying cosign signature on $REF"

COSIGN_OUT=$(mktemp)
trap 'rm -f "$COSIGN_OUT"' EXIT

cosign verify "$REF" \
    --certificate-identity-regexp "https://github.com/sheaf-project/sheaf/.github/workflows/ci.yml@.*" \
    --certificate-oidc-issuer https://token.actions.githubusercontent.com \
    > "$COSIGN_OUT" 2>/tmp/cosign-stderr || {
    echo "FAIL: cosign verify failed" >&2
    cat /tmp/cosign-stderr >&2
    exit 1
}

# The git_sha annotation is recorded at signing time by CI. If it doesn't
# match what the instance reports, either the instance is lying or it's
# running a different build than the one signed at this tag.
SIGNED_SHA=$(jq -r '.[0].optional.git_sha // empty' "$COSIGN_OUT")

if [ -z "$SIGNED_SHA" ]; then
    echo "FAIL: signature has no git_sha annotation (older signing format?)" >&2
    exit 1
fi

if [ "$SIGNED_SHA" != "$GIT_COMMIT" ]; then
    echo "FAIL: instance reports commit $GIT_COMMIT but signed annotation is $SIGNED_SHA" >&2
    exit 1
fi

echo
echo "✓ PASS: $INSTANCE is running a verified Sheaf build."
echo "  signed by: github.com/sheaf-project/sheaf CI workflow"
echo "  commit:    $GIT_COMMIT"
echo "  ref:       $REF"
