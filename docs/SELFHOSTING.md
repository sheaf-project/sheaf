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

## Optional dependencies

Sheaf uses optional Python extras for backend integrations. The Docker image includes `s3` by default — add others by editing the `pip install` line in the Dockerfile:

```dockerfile
# Default: only S3
RUN pip install --no-cache-dir ".[s3]"

# With SMTP email:
RUN pip install --no-cache-dir ".[s3,smtp]"

# With SES email:
RUN pip install --no-cache-dir ".[s3,ses]"

# Everything:
RUN pip install --no-cache-dir ".[s3,smtp,ses,sendgrid]"
```

| Extra | Package | Required when |
|-------|---------|---------------|
| `s3` | `boto3` | `STORAGE_BACKEND=s3` |
| `smtp` | `aiosmtplib` | `EMAIL_BACKEND=smtp` |
| `ses` | `boto3` | `EMAIL_BACKEND=ses` |
| `sendgrid` | `httpx` | `EMAIL_BACKEND=sendgrid` |

If a backend is configured but its extra isn't installed, Sheaf will fail on startup with a clear error message telling you which extra to add.

For local development without Docker, install all extras you need:

```bash
pip install -e ".[dev,s3,smtp]"
```

---

## Email

Email is needed for email verification, password reset, and account deletion notifications. Three backends are supported:

### SMTP

Works with any SMTP provider (Mailgun, Postmark, SendGrid, your own mail server, etc.):

```env
EMAIL_BACKEND=smtp
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=apikey
SMTP_PASSWORD=your-api-key
SMTP_FROM=noreply@example.com
SMTP_TLS=true
```

Port 465 uses implicit TLS; all other ports use STARTTLS (when `SMTP_TLS=true`).

**Requires the `smtp` extra** — see [Optional dependencies](#optional-dependencies).

### AWS SES

```env
EMAIL_BACKEND=ses
SES_REGION=us-east-1
SES_FROM=noreply@example.com
# Optional — omit to use IAM role/instance profile credentials:
SES_ACCESS_KEY=...
SES_SECRET_KEY=...
```

**Requires the `ses` extra** — see [Optional dependencies](#optional-dependencies).

#### SES bounce/complaint handling

If you configure an SQS queue to receive SES bounce/complaint notifications (via SNS), Sheaf can automatically suppress sending to addresses that hard-bounce or file complaints:

```env
SES_EVENTS_QUEUE_URL=https://sqs.us-east-1.amazonaws.com/123456789/sheaf-ses-events
```

### SendGrid

```env
EMAIL_BACKEND=sendgrid
SENDGRID_API_KEY=SG.xxxxx
SENDGRID_FROM=noreply@example.com
```

**Requires the `sendgrid` extra** — see [Optional dependencies](#optional-dependencies).

#### SendGrid bounce/complaint handling

Configure a [SendGrid Event Webhook](https://docs.sendgrid.com/for-developers/tracking-events/getting-started-event-webhook) to POST to `/v1/webhooks/sendgrid/events?token=<secret>`. Set the shared secret in your `.env`:

```env
SENDGRID_WEBHOOK_SECRET=your-random-secret-here
```

When configured, Sheaf automatically handles bounce, block, drop, deferred, and spam complaint events. When `SENDGRID_WEBHOOK_SECRET` is empty, the webhook endpoint returns 404.

### Disabling email

```env
EMAIL_BACKEND=none   # default
```

With `EMAIL_BACKEND=none`, email-dependent features (verification, password reset) are unavailable. Sheaf will refuse to start if `EMAIL_VERIFICATION=required` and `EMAIL_BACKEND=none`.

---

## Registration

```env
# open (default) | approval | invite | closed
REGISTRATION_MODE=open

# Accept invite codes even in open/approval mode (default: false).
# In "invite" mode, codes are always required regardless of this setting.
INVITE_CODES_ENABLED=false

# off (default) | required
EMAIL_VERIFICATION=off

# Required when email is enabled — used in verification/reset links
SHEAF_BASE_URL=https://sheaf.example.com
```

| Mode | Behaviour |
|------|-----------|
| `open` | Anyone can register and use their account immediately |
| `approval` | New accounts are held with `pending_approval` status until an admin approves them |
| `invite` | Registration requires a valid invite code (create and manage codes in the admin UI) |
| `closed` | No new registrations allowed |

**Invite codes** can be created and managed in the admin UI under **Invites**. In `approval` mode, users who register with a valid invite code bypass the approval queue. Set `INVITE_CODES_ENABLED=true` to accept invite codes in `open` or `approval` modes (they're always required in `invite` mode).

When `EMAIL_VERIFICATION=required`, new users must verify their email before they can access the API. A verification link is sent on registration. Users can request a new link (rate limited to once per 20 minutes).

**Invalid combinations** (Sheaf will refuse to start):
- `EMAIL_VERIFICATION=required` + `EMAIL_BACKEND=none`

**Warnings** (logged at startup):
- `REGISTRATION_MODE=approval` + `EMAIL_BACKEND=none` — approval notification emails won't be sent

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

## Account deletion

Users can request account deletion from Settings. Deletion has a configurable grace period during which the user can cancel:

```env
# Days between request and actual deletion (default: 7)
ACCOUNT_DELETION_GRACE_DAYS=7

# Days-before-deletion to send reminder emails (default: 5,3,1)
# Only used when EMAIL_BACKEND != none
ACCOUNT_DELETION_REMINDER_DAYS=5,3,1
```

When the grace period expires, the background job runner deletes the account and all associated data (systems, members, fronts, files, sessions, API keys).

---

## Rate limiting

Global per-IP rate limiting is enabled by default:

```env
RATE_LIMIT_ENABLED=true
RATE_LIMIT_GLOBAL_PER_IP=600   # max requests per window
RATE_LIMIT_GLOBAL_WINDOW=60    # window in seconds
```

### Trusted proxies

When Sheaf sits behind a reverse proxy, the connecting IP is the proxy, not the client. Set `TRUSTED_PROXIES` to trust `X-Forwarded-For` headers from specific IPs:

```env
# Comma-separated proxy IPs
TRUSTED_PROXIES=127.0.0.1
```

If empty (default), `X-Forwarded-For` is never read — the direct connecting IP is used. This is safe but means all users behind the proxy share one rate-limit bucket.

---

## External images

By default, member bios and descriptions can reference external image URLs. To restrict images to only hosted uploads (via Content Security Policy):

```env
ALLOW_EXTERNAL_IMAGES=false
```

---

## Image uploads

By default, any authenticated user can upload avatars and bio images. To disable uploads globally (e.g. for a public test instance without a ToS):

```env
ALLOW_IMAGE_UPLOADS=false
```

When disabled:
- Regular users see no upload button/tab in the UI and get HTTP 403 from `POST /v1/files/upload`.
- Admins can upload regardless.
- Any individual user can be allowlisted from the admin users page (**Uploads** column) or via `PATCH /v1/admin/users/{id}` with `{"can_upload_images": true}`. External image URLs are unaffected.

---

## Frontend

The Sheaf web frontend is a React SPA built with Vite. The Docker Compose setup serves the backend API only — you need to build and serve the frontend separately.

### Building

```bash
cd web
npm install
npm run build
```

This produces a static build in `web/dist/`.

### Serving in production

Serve the `web/dist/` directory with any static file server (nginx, Caddy, etc.). Configure your reverse proxy to route API calls to the backend and everything else to the SPA:

**Caddy example:**
```
sheaf.example.com {
    handle /v1/* {
        reverse_proxy localhost:8000
    }
    handle {
        root * /path/to/web/dist
        try_files {path} /index.html
        file_server
    }
}
```

**nginx example:**
```nginx
server {
    listen 443 ssl;
    server_name sheaf.example.com;

    # API
    location /v1/ {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Frontend SPA
    location / {
        root /path/to/web/dist;
        try_files $uri /index.html;
    }
}
```

### Development

For local development, the Vite dev server runs on port 5173 and proxies `/v1/*` requests to the backend at `localhost:8000`:

```bash
cd web
npm run dev
```

---

## Reverse proxy / TLS

Sheaf has no built-in TLS. Use a reverse proxy (nginx, Caddy, Traefik) for HTTPS termination. See the [Frontend](#frontend) section above for split-routing examples that serve both the API and the SPA.

If you don't want uvicorn directly exposed on the network:

```env
SHEAF_HOST=127.0.0.1
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
