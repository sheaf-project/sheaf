#!/bin/sh
# All-in-one entrypoint. Runs as root (so Caddy can bind 80/443); supervisord
# drops each program to its own user.
#
# Two modes:
#   generate-secrets  -> the compose `init` service: create the shared
#                        first-boot secrets if absent, then exit.
#   (default)         -> the app service: wire runtime config from those
#                        secrets, then exec supervisord (redis + backend + Caddy).
set -e

if [ "$1" = "generate-secrets" ]; then
    exec /usr/local/bin/aio-init-secrets.sh
fi

# supervisord runs the app as the `sheaf` user but does NOT reset HOME (it stays
# root's /root, which sheaf can't traverse). Point it at the sheaf-owned home so
# libpq/asyncpg's ~/.postgresql client-cert lookup resolves cleanly instead of
# EACCES, and cloudflared/etc. have a writable home. Inherited by every
# supervised program.
export HOME=/home/sheaf

# JWT signing key: read the shared file the init service generated when the
# operator didn't set one. Persisted, not regenerated - rotating it logs
# everyone out.
if [ -z "${JWT_SECRET_KEY:-}" ] && [ -f /secrets/jwt_secret ]; then
    JWT_SECRET_KEY="$(cat /secrets/jwt_secret)"
    export JWT_SECRET_KEY
fi

# Database URL: build it from the shared Postgres password unless the operator
# supplied a full DATABASE_URL (e.g. pointing at an external database).
if [ -z "${DATABASE_URL:-}" ] && [ -f /secrets/postgres_password ]; then
    PW="$(cat /secrets/postgres_password)"
    DATABASE_URL="postgresql+asyncpg://sheaf:${PW}@${POSTGRES_HOST:-db}:5432/sheaf"
    export DATABASE_URL
fi

# Public base URL / cookie Secure flag + email links: derive from the domain
# when set. Left alone if the operator set SHEAF_BASE_URL explicitly.
if [ -z "${SHEAF_BASE_URL:-}" ] && [ -n "${AIO_DOMAIN:-}" ]; then
    export SHEAF_BASE_URL="https://${AIO_DOMAIN}"
fi

# Ingress mode. Two ways to reach the box:
#   1. Cloudflare Tunnel (CF_TUNNEL_TOKEN set): Cloudflare terminates TLS and
#      connects OUT to us, so Caddy just serves plain HTTP on loopback:80 and
#      cloudflared forwards to it. No inbound ports / port-forwarding needed -
#      point the tunnel's public hostname at http://localhost:80 in the
#      Cloudflare dashboard.
#   2. Direct: a domain gets automatic HTTPS from Caddy (publish 80 + 443);
#      no domain means plain HTTP on :80 (LAN / behind your own proxy).
mkdir -p /etc/supervisor/conf.d
if [ -n "${CF_TUNNEL_TOKEN:-}" ]; then
    export AIO_SITE_ADDRESS=":80"
    cp /etc/supervisor/available/cloudflared.conf /etc/supervisor/conf.d/cloudflared.conf
else
    rm -f /etc/supervisor/conf.d/cloudflared.conf 2>/dev/null || true
    if [ -n "${AIO_DOMAIN:-}" ]; then
        export AIO_SITE_ADDRESS="${AIO_DOMAIN}"
    else
        export AIO_SITE_ADDRESS=":80"
    fi
fi

# ACME endpoint: production Let's Encrypt by default. Override AIO_ACME_CA to
# the LE staging directory to dry-run cert issuance without hitting production
# rate limits. Defaulted here (not just in compose) so a raw `docker run` also
# gets a valid CA.
export AIO_ACME_CA="${AIO_ACME_CA:-https://acme-v02.api.letsencrypt.org/directory}"

# Render Caddy's ACME global options. The email line is emitted only when set:
# an empty `email` directive is a Caddyfile parse error, so we omit it rather
# than pass one through.
{
    echo "acme_ca ${AIO_ACME_CA}"
    [ -n "${AIO_ACME_EMAIL:-}" ] && echo "email ${AIO_ACME_EMAIL}"
    true
} > /etc/caddy/acme.conf

# Caddy proxies to uvicorn on loopback; trust it so X-Forwarded-For is read.
export TRUSTED_PROXIES="${TRUSTED_PROXIES:-127.0.0.1}"
export FORWARDED_ALLOW_IPS="${FORWARDED_ALLOW_IPS:-127.0.0.1}"

# Redis: run the bundled instance unless REDIS_URL points at an external one.
mkdir -p /etc/supervisor/conf.d
if [ -z "${REDIS_URL:-}" ]; then
    export REDIS_URL="redis://127.0.0.1:6379/0"
    cp /etc/supervisor/available/redis.conf /etc/supervisor/conf.d/redis.conf
else
    rm -f /etc/supervisor/conf.d/redis.conf 2>/dev/null || true
fi

# A fresh named volume comes up root-owned; hand the data dir to the app user
# (encryption key, uploaded files, Caddy cert storage live here).
chown -R sheaf:sheaf /app/data 2>/dev/null || true

exec supervisord -c /etc/supervisor/supervisord.conf
