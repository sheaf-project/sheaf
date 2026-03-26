# Self-Hosting Sheaf

This guide covers everything you need to know to run Sheaf reliably in production.

## Quick start

```bash
cp .env.example .env
# Edit .env — minimum: set POSTGRES_PASSWORD and JWT_SECRET_KEY
docker compose up -d
```

API at `http://localhost:8000`, docs at `http://localhost:8000/v1/docs`.

---

## Required configuration

### Secrets

Generate strong random values for these before going live:

```bash
# JWT secret — used to sign access tokens
python -c "import secrets; print(secrets.token_urlsafe(32))"

# Postgres password
python -c "import secrets; print(secrets.token_hex(16))"
```

Set in `.env`:
```env
JWT_SECRET_KEY=<generated>
POSTGRES_PASSWORD=<generated>
DATABASE_URL=postgresql+asyncpg://sheaf:${POSTGRES_PASSWORD}@db:5432/sheaf
```

### Encryption key

Sheaf encrypts email addresses and TOTP secrets at rest using XChaCha20-Poly1305.

**If not set**, a key is auto-generated on first startup and saved to `data/encryption.key` inside the Docker volume. **Back this file up.** If you lose it, all encrypted data (emails, TOTP secrets) is permanently unrecoverable.

To use your own key (recommended — avoids relying on a file in a volume):

```bash
# Generate
python -c "import secrets; print(secrets.token_hex(32))"
```

```env
SHEAF_ENCRYPTION_KEY=<generated>
```

---

## Admin access

### Granting admin

Set `SHEAF_ADMIN_EMAILS` to a comma-separated list of email addresses:

```env
SHEAF_ADMIN_EMAILS=you@example.com,colleague@example.com
```

**Important:** Admin promotion runs at server startup, not at registration time. The sequence is:

1. User registers their account
2. Server is restarted (or `docker compose restart app`)
3. On startup, Sheaf finds the account by email and sets `is_admin = true`

If you set `SHEAF_ADMIN_EMAILS` before the account exists, nothing happens at that startup — restart again after the account is created.

### What admins can do

- Access the `/admin` section of the web UI (user management, storage audit, maintenance)
- Change user tiers and member limits
- Create `admin:read` / `admin:write` scoped API keys for scripted access
- Trigger retention pruning and orphaned file cleanup across all users

### Admin dashboard step-up authentication

By default any admin session can access the dashboard immediately. For additional protection, require a re-authentication challenge on each new browser session:

```env
# none (default) — immediate access
# password       — re-enter account password
# totp           — enter TOTP code (account must have 2FA enabled)
ADMIN_AUTH_LEVEL=totp
```

The challenge is stored in Redis per-user and valid for 2 hours. Applies to both session-cookie auth and JWT bearer token auth. API keys with `admin:*` scope are exempt and never require step-up.

With `ADMIN_AUTH_LEVEL=totp`: if the admin account does not have TOTP enabled, access to the dashboard is blocked with an explanatory message until 2FA is set up in Settings.

---

## File storage

### Filesystem (default)

Files are stored in `data/files/` inside the Docker volume. No additional config needed.

```env
STORAGE_BACKEND=filesystem
```

### S3-compatible (AWS S3, MinIO, Cloudflare R2, BackBlaze B2, etc.)

```env
STORAGE_BACKEND=s3
S3_BUCKET=sheaf-files
S3_ACCESS_KEY=...
S3_SECRET_KEY=...
S3_REGION=us-east-1
S3_ENDPOINT=https://your-minio.example.com  # Omit for AWS S3
# S3_PRESIGN_ENDPOINT=  # See "Presigned URL endpoint" below
```

### Image serving and hotlink protection

By default (`IMAGE_SERVING=signed`), avatar URLs are HMAC-signed with a short expiry window. This prevents your storage from being used as free image hosting.

| Setting | Behaviour |
|---------|-----------|
| `IMAGE_SERVING=signed` | URLs include an HMAC token and expiry. S3: server redirects to a presigned S3 URL (bucket can be private). Filesystem: token validated on every request. |
| `IMAGE_SERVING=unsigned` | No token required — anyone with a URL can load the file. Use only with a CDN that has hotlink protection. |
| `S3_PUBLIC_URL=https://cdn.example.com` | Avatar URLs resolve directly to your CDN, bypassing the serve endpoint entirely. Combine with `IMAGE_SERVING=unsigned` and Cloudflare hotlink protection rules. |

#### Presigned URL endpoint

When using S3-compatible storage (MinIO, etc.) where `S3_ENDPOINT` is a Docker-internal hostname (e.g. `http://minio:9000`), presigned redirect URLs will point at that internal hostname — which the browser can't reach.

Set `S3_PRESIGN_ENDPOINT` to the externally-reachable URL for your S3 service:

```env
S3_ENDPOINT=http://minio:9000               # Used by the app container
S3_PRESIGN_ENDPOINT=http://localhost:9000    # Used in presigned URLs sent to the browser
```

Not needed for AWS S3 (the endpoint is always publicly reachable) or when using `S3_PUBLIC_URL` / `IMAGE_SERVING=unsigned`.

#### Signed URL expiry window

```env
# Window in seconds (default: 3600 = 1 hour). All requests within the
# same window get the same URL, enabling browser/CDN caching.
# Use a clean divisor of a day (e.g. 1800, 3600, 7200).
FILE_URL_EXPIRY_SECONDS=3600
```

### Storage quotas

Per-tier quotas are enforced at upload time (0 = unlimited):

```env
STORAGE_QUOTA_FREE_MB=50
STORAGE_QUOTA_PLUS_MB=500
STORAGE_QUOTA_SELFHOSTED_MB=0   # unlimited
```

### Upload size limit

```env
MAX_UPLOAD_SIZE_MB=5
```

---

## API keys

Users can create named, scoped API keys from Settings → API Keys. These allow programmatic access without sharing session credentials.

Available scopes: `system:read/write`, `members:read/write/delete`, `fronts:read/write/delete`, `groups:read/write/delete`, `tags:read/write/delete`, `fields:read/write/delete`, `export:read`.

Admin users can additionally create `admin:read` / `admin:write` keys for scripted admin operations.

**Scope rules:**
- `resource:write` implies `resource:read`
- `resource:delete` implies `resource:read`
- `resource:write` does **not** imply `resource:delete` — delete is always explicit

---

## Mode: selfhosted vs saas

```env
SHEAF_MODE=selfhosted   # default
# SHEAF_MODE=saas
```

| Feature | selfhosted | saas |
|---------|-----------|------|
| Insecure defaults | warning | **refuses to start** |
| Free-tier front history pruning | disabled | enabled |
| Tier-based feature gates | disabled | enabled |
| Member limits | configurable per-user | tier-based |

In `saas` mode, Sheaf will refuse to start if `JWT_SECRET_KEY` or `DATABASE_URL` contain default values.

### Front history retention (saas mode only)

```env
FREE_TIER_FRONT_RETENTION_DAYS=30
RETENTION_CHECK_INTERVAL_HOURS=6
```

---

## Member limits

Per-tier member limits (0 = unlimited). These are only enforced in `saas` mode or when overridden per-user via the admin UI.

```env
MEMBER_LIMIT_FREE=512
MEMBER_LIMIT_PLUS=0       # unlimited
MEMBER_LIMIT_SELFHOSTED=0 # unlimited
```

Individual users can have their limit overridden via `PATCH /v1/admin/users/{id}`.

---

## Reverse proxy

Sheaf has no built-in TLS. Sit it behind nginx, Caddy, or Traefik:

**Caddy example:**
```
your-domain.example.com {
    reverse_proxy localhost:8000
}
```

**nginx example:**
```nginx
server {
    listen 443 ssl;
    server_name your-domain.example.com;

    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

After setting up a proxy, set the port to 0 if you don't want uvicorn exposed directly:

```env
SHEAF_PORT=8000
SHEAF_HOST=0.0.0.0
```

---

## Backups

Back up:
1. **PostgreSQL data** — the `pgdata` Docker volume, or use `pg_dump`
2. **File storage** — the `appdata` Docker volume (filesystem backend), or your S3 bucket
3. **Encryption key** — `data/encryption.key` inside `appdata`, or your `SHEAF_ENCRYPTION_KEY` env var

```bash
# Postgres dump
docker compose exec db pg_dump -U sheaf sheaf > sheaf-backup.sql

# Restore
docker compose exec -T db psql -U sheaf sheaf < sheaf-backup.sql
```

---

## MinIO (local S3-compatible storage)

A MinIO service is included in docker-compose for local S3 testing:

```bash
docker compose --profile s3 up -d
```

```env
STORAGE_BACKEND=s3
S3_BUCKET=sheaf-files
S3_ACCESS_KEY=minioadmin
S3_SECRET_KEY=minioadmin
S3_ENDPOINT=http://minio:9000
S3_PRESIGN_ENDPOINT=http://localhost:9000  # So the browser can reach presigned URLs
```

MinIO console at `http://localhost:9001`.
