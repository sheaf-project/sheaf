#!/bin/sh
# Generate the shared first-boot secrets, if absent, into /secrets (a volume
# shared with the db and app containers). Run once by the compose `init`
# service before Postgres and the app start.
#
# Persisting these (rather than regenerating per boot) is the point: the JWT
# key logs everyone out if it changes, and the Postgres password must stay
# stable for the app to keep connecting. Set JWT_SECRET_KEY / DATABASE_URL
# yourself to bypass this entirely.
set -e

mkdir -p /secrets

gen() {
    python -c "import secrets; print(secrets.token_hex(32))"
}

if [ ! -s /secrets/postgres_password ]; then
    gen > /secrets/postgres_password
    echo "aio-init: generated /secrets/postgres_password"
fi
if [ ! -s /secrets/jwt_secret ]; then
    gen > /secrets/jwt_secret
    echo "aio-init: generated /secrets/jwt_secret"
fi

# Read by root in both the db (Postgres entrypoint) and app (this image's
# entrypoint) containers, so root-only is enough.
chmod 0400 /secrets/postgres_password /secrets/jwt_secret 2>/dev/null || true
echo "aio-init: secrets ready"
