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

Sheaf requires **two** stable long-lived secrets. Both must be set before going live and must remain constant across restarts for the lifetime of the install — changing them has user-facing consequences (see below). Back them up wherever you back up the rest of your deployment.

Generate strong random values:

```bash
# JWT secret — signs sessions, refresh tokens, mail-delivered tokens,
# and keys the password-reset / email-verification HMACs.
python -c "import secrets; print(secrets.token_urlsafe(32))"

# Encryption key — 32 bytes hex. Encrypts emails / TOTP secrets and keys
# the blind-index used to look up users by email at login.
python -c "import secrets; print(secrets.token_hex(32))"

# Postgres password
python -c "import secrets; print(secrets.token_hex(16))"
```

Set in `.env`:
```env
JWT_SECRET_KEY=<generated>
SHEAF_ENCRYPTION_KEY=<generated>
POSTGRES_PASSWORD=<generated>
DATABASE_URL=postgresql+asyncpg://sheaf:${POSTGRES_PASSWORD}@db:5432/sheaf
```

### What each key does — and what happens if you lose or change it

**`JWT_SECRET_KEY`** — signs access tokens, refresh tokens, and keys the HMAC for password-reset and email-verification tokens stored in the DB. If you rotate it:
- All sessions and refresh tokens are invalidated — every user must log in again.
- Any outstanding password-reset or email-verification links become invalid (users request a new one).

Sheaf refuses to start in `saas` mode if this is left at the default, and logs a loud warning in `selfhosted` mode. The default is safe for local dev only.

**`SHEAF_ENCRYPTION_KEY`** — XChaCha20-Poly1305 key used to encrypt emails and TOTP secrets at rest. It **also** keys the blind-index (keyed HMAC-SHA-256 of the normalised email) that the login endpoint uses to look up a user by email. Practical consequences:
- **Losing it is unrecoverable.** Encrypted emails / TOTP secrets can't be decrypted, and the `email_hash` column becomes a set of opaque numbers that match nothing computable without the key. Nobody will be able to log in.
- **Rotating it** requires re-encrypting the email ciphertext AND re-computing every `email_hash` row (see `alembic/versions/k1l2m3n4o5p6_rehash_email_blind_index.py` for the pattern). Don't rotate this casually.

**If not set**, a key is auto-generated on first startup and saved to `data/encryption.key` inside the Docker volume. **Back this file up.** Prefer setting `SHEAF_ENCRYPTION_KEY` explicitly so the key isn't tied to a single volume.

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

Sheaf uses optional Python extras for backend integrations. **The official Docker image bundles all of them** so any storage/email backend works out of the box without a rebuild:

| Extra | Package | Required when |
|-------|---------|---------------|
| `s3` | `boto3` | `STORAGE_BACKEND=s3` |
| `smtp` | `aiosmtplib` | `EMAIL_BACKEND=smtp` |
| `ses` | `boto3` | `EMAIL_BACKEND=ses` |
| `sendgrid` | `httpx` | `EMAIL_BACKEND=sendgrid` |

If you're building a slimmed-down image (e.g. you never want SES), edit the `pip install` line in `Dockerfile.backend`:

```dockerfile
# Default — everything, matches the published image:
RUN pip install --no-cache-dir ".[s3,ses,smtp,sendgrid]"

# Minimal — filesystem storage, SMTP email only:
RUN pip install --no-cache-dir ".[smtp]"
```

If a backend is configured but its extra isn't installed, Sheaf will fail on startup with a clear error message telling you which extra to add.

For local development without Docker, install the extras you need:

```bash
pip install -e ".[dev,s3,smtp]"
```

### Building behind a private PyPI mirror

If you run a PyPI cache or mirror on your network (proxpi, devpi, Artifactory, etc.), the backend Dockerfile picks it up from two optional build args, both empty by default:

```bash
PIP_INDEX_URL=http://pypi.mirror.lan/index/
PIP_EXTRA_INDEX_URL=
```

Drop one (or both) into a gitignored `.env` next to `docker-compose.yml` and re-run `docker compose build`. `PIP_INDEX_URL` replaces PyPI entirely (use this when the mirror itself is a transparent PyPI proxy); `PIP_EXTRA_INDEX_URL` is checked first with PyPI as fallback. Empty values pass through to pip's default behaviour, so leaving them unset means public-PyPI builds as before.

### Build provenance in `/v1/version`

`GET /v1/version` reports the running version plus the commit, tag, and build time it was built from. The official ghcr images set those automatically in CI. For a **local `docker compose build`** they default to empty, because compose can't run git itself; pass them from the host (where git is available) if you want `/v1/version` to identify your build:

```bash
GIT_COMMIT=$(git rev-parse --short HEAD) \
GIT_TAG=$(git describe --tags --always) \
BUILD_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ) \
docker compose up --build -d app
```

Leaving them unset is harmless; only the provenance fields read back as null. See [VERIFYING.md](VERIFYING.md) for the full supply-chain verification story.

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

Plain SMTP has no feedback channel, so by default nothing flags a bounced or complained address - the [deliverability gate](#deliverability-state) simply never trips. If your SMTP provider offers a webhook, wire it up to keep bounce suppression working (see SMTP2GO below).

#### SMTP2GO bounce/complaint feedback

If you send via SMTP2GO (`EMAIL_BACKEND=smtp` pointed at SMTP2GO's relay), add a webhook so delivery/bounce/spam events drive the deliverability state. SMTP2GO does not sign payloads, so the endpoint is guarded by a shared secret in the URL. Set it in `.env`:

```env
SMTP2GO_WEBHOOK_SECRET=your-random-secret-here
```

Then in the SMTP2GO app (Settings -> Webhooks), add a webhook with:
- **URL:** `https://your-instance/v1/webhooks/smtp2go/events?token=your-random-secret-here`
- **Output type:** JSON or Form-encoded (both accepted)
- **Events:** at minimum Delivered, Bounce, and Spam. Delivered is what lets a greylisted first attempt self-heal once the retry lands - see [Deliverability state](#deliverability-state).

When `SMTP2GO_WEBHOOK_SECRET` is empty the endpoint returns 404. For defence in depth, also restrict the endpoint to SMTP2GO's published source IPs (`webhooks.smtp2go.com`) at your reverse proxy, since the secret travels in the URL.

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

When configured, Sheaf automatically handles bounce, block, drop, deferred, spam complaint, and delivered events. When `SENDGRID_WEBHOOK_SECRET` is empty, the webhook endpoint returns 404.

Enable the **Delivered** event in the SendGrid Event Webhook event selection (alongside the bounce/drop/spam events), not just the failure events. Sheaf uses a successful delivery to clear transient soft-bounce state, so without it a greylisted first attempt can leave an address flagged even after the retry delivers. See [Deliverability state](#deliverability-state) below.

### Deliverability state

Bounce and complaint feedback (SES queue or SendGrid webhook above) drives a per-account deliverability state that gates outgoing mail. It is a recoverable lifecycle, not a one-way block:

- **Soft bounces are tolerated.** A soft bounce is transient - greylisting (e.g. an rspamd-based MX defers the first attempt), a full mailbox, a momentary MTA failure. A single one never blocks mail. An address is only flagged undeliverable after `EMAIL_SOFT_BOUNCE_THRESHOLD` soft bounces accumulate *without* an intervening successful delivery, which resets the count.

  ```env
  EMAIL_SOFT_BOUNCE_THRESHOLD=5   # consecutive soft bounces before an address is flagged
  ```

- **A delivery self-heals soft state.** A `delivered`/successful-delivery event clears soft-bounce state back to healthy, so the greylist-then-retry pattern recovers on its own. (It deliberately does not clear a hard bounce or a spam complaint - those are cleared only by the user re-verifying.)

- **Hard bounces and complaints block immediately** and flag the account for revalidation.

- **Users are never silently locked out.** When an address is flagged, the user sees a banner on sign-in prompting them to re-verify or change their email. Re-verifying (the verification email is sent even to a currently-blocked address) clears the block. No admin intervention or manual database edit is required.

Bounce/complaint feedback comes from a provider webhook or queue: the SES SQS handler, the SendGrid Event Webhook, or the SMTP2GO webhook (see [SMTP2GO bounce/complaint feedback](#smtp2go-bouncecomplaint-feedback)). If you run plain SMTP with no such channel wired up, no addresses are ever auto-flagged - the deliverability gate simply never trips, which is a safe (if unfiltered) default.

### Disabling email

```env
EMAIL_BACKEND=none   # default
```

With `EMAIL_BACKEND=none`, email-dependent features (verification, password reset) are unavailable. Sheaf will refuse to start if `EMAIL_VERIFICATION=required` and `EMAIL_BACKEND=none`.

---

## Front-change notifications

System owners can invite recipients (partners, friends, therapists, bots) to receive a notification whenever fronts change. Recipients don't need a Sheaf account — anonymous push subscriptions and webhook URLs both work. Owners pre-configure the entire channel (filters, triggers, payload sensitivity, delivery shaping); recipients only get pinged for what the owner allows.

v1 supports four destination types:
- **Web push** — browser notifications. Recipient redeems a one-time activation link, grants permission, done.
- **Webhook** — POST to a URL with a configurable payload format: `json` (Sheaf's structured schema, HMAC-signed), `discord` (Discord webhook shape with avatar/username), `slack` (Slack webhook shape), or `plaintext` (title + body). SSRF-guarded; private IP ranges and IMDS are rejected at request time and re-validated on every dispatch.
- **ntfy** — POST to any [ntfy](https://ntfy.sh) server (the public one or self-hosted).
- **Pushover** — for the [Pushover](https://pushover.net) mobile app.

Notification setup is per-destination — Sheaf works fine with none configured; only the destination types you set up will be available to owners.

### Web push

Generate a VAPID keypair once and keep it stable across deploys (rotating breaks every browser subscription):

```sh
pip install py-vapid
vapid --gen
```

That writes `private_key.pem` and prints the `applicationServerKey` (base64url public key). Mount the PEM into the container or paste it inline as `VAPID_PRIVATE_KEY`:

```env
VAPID_PUBLIC_KEY=BPaJk...   # the applicationServerKey base64url string
VAPID_PRIVATE_KEY=/app/data/vapid_private.pem  # path or PEM literal
VAPID_SUBJECT=mailto:ops@example.com
```

`VAPID_SUBJECT` is the contact URI surfaced to push services (Mozilla, Google) so they can reach you if a subscription misbehaves. Use a real `mailto:` or `https://` URL.

When VAPID isn't configured, the web_push destination type is unavailable; the dispatcher treats missing VAPID as transient (channels won't auto-disable while you're getting it set up).

### Pushover

Register a Pushover application at <https://pushover.net/apps> and put the resulting app token in:

```env
PUSHOVER_APP_TOKEN=axxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

Each recipient supplies their own user_key per channel at create time. Without `PUSHOVER_APP_TOKEN`, the destination type is rejected with 501.

#### Cost shape

As of May 2026, [Pushover gives each account a pooled 10,000 messages/month free](https://blog.pushover.net/posts/2026/4/app-limits) across all apps the account owns. Paid upgrades are one-off purchases at $50/10k, $115/25k, $200/50k, $300/100k, $1000/500k, applied at the account level.

Sheaf runs one app per instance, so one Sheaf instance = one Pushover account's quota. If you expect to push past 10k/month, the simplest option is a one-off upgrade — they apply automatically since the account quota is what we hit.

[Pushover for Teams](https://pushover.net/teams) is a separate $5/user/month subscription product for organizations; team accounts get a 25k/month free limit instead of 10k, but the team subscription only makes sense if you also need its user-management features. For pure quota expansion an individual one-off upgrade is much cheaper.

Three sets of settings cap shared-app exposure:

```env
PUSHOVER_MAX_PER_MONTH=10000                     # deployment-wide ceiling
PUSHOVER_SHARED_APP_MIN_DEBOUNCE_SECONDS=1800    # 30-min minimum debounce
PUSHOVER_USER_MAX_PER_MONTH_FREE=100             # per-Sheaf-user, by tier
PUSHOVER_USER_MAX_PER_MONTH_PLUS=1000
PUSHOVER_USER_MAX_PER_MONTH_SELF_HOSTED=0        # 0 = unlimited for that tier
```

Three checks happen for every shared-app Pushover delivery:

1. **Per-user tier cap.** Each Sheaf user has a monthly allowance based on their tier. 0 disables the per-user check for that tier. Hitting the user's cap transient-fails their deliveries until the next calendar month. Counter at `pushover:usage:user:{user_id}:YYYY-MM`. Surfaced to the user via `GET /v1/notifications/pushover-usage` and shown on their notifications page.
2. **Deployment-wide cap.** `PUSHOVER_MAX_PER_MONTH` caps total instance usage. Counter at `pushover:usage:YYYY-MM`. Surfaced on `/admin` and at `GET /v1/admin/pushover-usage`. Set to 0 to disable Sheaf-side tracking entirely (Pushover's own 429s become the only ceiling).
3. **Debounce floor.** `PUSHOVER_SHARED_APP_MIN_DEBOUNCE_SECONDS` is enforced at channel create/update time, so users can't configure under it. Without this floor, one chatty system could burn the whole instance quota in a day. 30 minutes is the default. Surfaced via `GET /v1/notifications/server-config` so the UI can render the floor in the channel form.

All three apply only to channels using the deployment's shared `PUSHOVER_APP_TOKEN`. BYO channels (recipient-supplied `destination_config.app_token`) bypass all of them.

#### BYO Pushover app (recipient-side)

Recipients who want their own Pushover quota (or just don't want to compete for the shared one) can create a free Pushover account, register an application at <https://pushover.net/apps/build>, and paste its API token into the channel's "Advanced" config:

- The channel uses the BYO app token instead of the deployment's
- Counts toward the recipient's own account quota (10k/month free, pooled across any other Pushover apps they run), not yours
- Both `PUSHOVER_MAX_PER_MONTH` and `PUSHOVER_SHARED_APP_MIN_DEBOUNCE_SECONDS` no longer apply — the recipient sets debounce wherever they want

This is the pressure-relief valve when you start hitting the shared cap regularly. Document it for power users.

### Discord webhook display

Owners can choose `format=discord` on a webhook channel. These two settings control how the bot renders in Discord:

```env
DISCORD_WEBHOOK_USERNAME=Sheaf
DISCORD_WEBHOOK_AVATAR_URL=  # publicly reachable PNG/JPEG; SVG rejected
```

Empty avatar = falls back to the `/sheaf-icon.png` served by the frontend. Empty username = "Sheaf".

### Webhook plumbing

```env
WEBHOOK_USER_AGENT=Sheaf-Notifications/1.0
```

Identifies Sheaf in webhook receiver logs. Bump the version if you fork or patch the dispatch code so receivers can tell.

### Dispatcher tuning

```env
NOTIFICATIONS_DISPATCH_INTERVAL_SECONDS=5
ACTIVATION_CODE_TTL_DAYS=7

NOTIFICATIONS_CONCURRENCY_WEB_PUSH=10
NOTIFICATIONS_CONCURRENCY_WEBHOOK=5
NOTIFICATIONS_CONCURRENCY_NTFY=5
NOTIFICATIONS_CONCURRENCY_PUSHOVER=5
```

The dispatcher polls the outbox every `NOTIFICATIONS_DISPATCH_INTERVAL_SECONDS`. Lower = snappier delivery, higher = less DB churn. 5s is fine for most self-hosters.

Per-destination concurrency caps how many deliveries run in parallel. Raise if you have many recipients on one destination type and your egress can take it; lower if a downstream is rate-limiting you.

### Aggregation behaviour

A single front change — even if many members move at once — produces **one notification per channel**, not one per affected member. A switch from `{Alice, Bob}` to `{Cara, Dani, Eli}` becomes one message describing all five names (filtered by the channel's per-member visibility rules). This avoids fan-out hitting webhook rate limits and keeps recipients' phones from buzzing N times for what was logically one event.

### Multi-instance deploys

Background work (the job runner, the notification dispatcher, and the import runner) is coordinated by leader election: every replica competes for a Postgres advisory lock, exactly one wins and runs the loops, and the rest stand by. If the leader dies or loses its database connection, a standby takes over within a few seconds. Single-instance deploys are unaffected — the lone process simply always wins.

Underneath the election, work items are still claimed per-row (`SELECT FOR UPDATE SKIP LOCKED` plus lease-based reclaim of claims orphaned by a crashed worker), so even a brief leadership overlap during failover cannot double-deliver.

```env
# Escape hatch: run the loops in every process (the old behaviour).
LEADER_ELECTION=false
```

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

# Public base URL of the instance — used for email links, the JWT issuer
# claim, and to decide whether auth cookies carry the Secure flag. Required
# when email is enabled; otherwise optional. Leave unset (or set to https://...)
# in production. Set to an http:// URL only for plain-HTTP dev/LAN setups —
# see "Reverse proxy / TLS" below.
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
S3_ACCESS_KEY=...     # Omit to use IAM role/instance profile credentials
S3_SECRET_KEY=...     # Omit to use IAM role/instance profile credentials
S3_REGION=us-east-1
S3_ENDPOINT=https://your-minio.example.com  # Omit for AWS S3
# S3_PRESIGN_ENDPOINT=  # See "Presigned URL endpoint" below
```

When `S3_ACCESS_KEY`/`S3_SECRET_KEY` are unset, boto3's default credential chain is used — EC2 instance profile, ECS/EKS task role (IRSA), `~/.aws/credentials`, or the standard `AWS_*` env vars. The IAM identity needs `s3:PutObject`, `s3:GetObject`, and `s3:DeleteObject` on `arn:aws:s3:::your-bucket/*`.

### Image serving paradigms

Image URLs need to balance four things: preventing your instance from being used as free image hosting, CDN caching for performance, privacy (what the CDN sees), and operational cost/complexity. Four supported setups, pick whichever matches your deployment:

#### 1. Filesystem, app-served (default, simplest)

```env
STORAGE_BACKEND=filesystem
IMAGE_SERVING=signed
```

The app serves bytes directly from local disk after validating the HMAC token on each request. No CDN, no extra infra. Fine for single-server deployments and small instances. Every image request hits your app server.

#### 2. S3 + signed presigned URLs (no CDN)

```env
STORAGE_BACKEND=s3
S3_BUCKET=sheaf-files
IMAGE_SERVING=signed
# S3_PUBLIC_URL left unset
```

Bucket stays private. Clients load `/v1/files/{key}?token=…` from your app; the app validates the token and 302s to a short-lived S3 presigned URL. Presigns are cached in Redis within the signing window so repeat loads don't re-sign. No third party sees your image URLs.

Downside: every first-view within a window round-trips through your app for the redirect, and browsers cache the final S3 URL (not the app URL). If you're okay serving images straight from S3's edge performance, this is the privacy-maximising choice.

#### 3. S3 + unsigned + CDN hotlink protection

```env
STORAGE_BACKEND=s3
IMAGE_SERVING=unsigned
S3_PUBLIC_URL=https://images.example.com   # Your CDN hostname
```

Bucket must be publicly readable (or use an origin-access token scoped to your CDN). Clients load images directly from the CDN. Hotlinking is prevented by CDN rules (Cloudflare Referer rules, Page Rules, or a WAF rule checking `X-Sheaf-Client` / Origin).

This is the cheapest setup at scale — the CDN caches aggressively and your app never sees image traffic. Tradeoff: anyone with a leaked URL can fetch the object for as long as it exists in the bucket (no expiry), and the CDN sees every image load.

#### 4. S3 + signed URLs + CDN Worker (private bucket, expiring URLs, CDN-cached)

```env
STORAGE_BACKEND=s3
IMAGE_SERVING=signed
S3_PUBLIC_URL=https://images.example.com   # CDN hostname; Worker lives here
FILE_SIGNING_KEY=...                       # 32+ bytes; shared with the Worker
```

Bucket stays private. Clients load `https://images.example.com/{key}?token=…&expires=…`. A Cloudflare Worker (see `selfhost-utils/cf-image-worker/`) on that hostname validates the HMAC against the same `FILE_SIGNING_KEY`, fetches the object from the private bucket via AWS SigV4, and returns it. The CDN caches by full URL so all requests within a signing window share a cache entry; expired or invalid tokens return 403 at the edge.

This is the combination you want when you need *both* expiring URLs *and* CDN caching — e.g. a public-facing deployment where private images need hotlink protection stronger than a referer check and you don't want your app serving image bytes.

Tradeoffs: one more moving piece (the Worker); the CDN sees image paths and tokens; Worker costs scale with cache-miss rate (typically free-tier for small/mid instances, see the `cf-image-worker/README.md` for numbers). `FILE_SIGNING_KEY` must be set explicitly here — without it the app derives the signing key from `JWT_SECRET_KEY`, which you should never give to a Worker.

> **Privacy note:** paradigms 3 and 4 route image loads through a third-party CDN. If you're CDN-fronting only images (not the API/web UI), this is the split most people hosting publicly as a service may want — performance where it matters, privacy for the data that matters.

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

## Data exports

Three flavours of "give me my data", differing by scope and gating:

| Endpoint | Scope | Gate | Format |
|---|---|---|---|
| `GET /v1/export` | Plural-system content (Article 20) | Session/JWT only | Sync JSON |
| `POST /v1/account/data` | Account identity + audit (Article 15) | Password + TOTP-if-enrolled | Sync JSON |
| `POST /v1/export/jobs` | Plural-system + image bytes | Password + TOTP-if-enrolled | Async zip |

The two POST endpoints **always** require step-up auth regardless of the system's `delete_confirmation` setting — they're the highest-value reads for an attacker with a hijacked session, and we don't let users opt out.

### Async export jobs

The user requests a backup, the worker assembles a zip in the background, the user gets an email when it's ready (and sees the job in Settings → Data export). The file is kept for 72 hours then auto-deleted.

```env
EXPORT_JOB_TTL_HOURS=72                  # how long a built file is kept
EXPORT_MAX_CONCURRENT_PER_USER=1         # one in-flight job per user
```

### S3-backed exports: use a dedicated bucket

If you're running with `STORAGE_BACKEND=s3`, the main `S3_BUCKET` is typically CDN-fronted (e.g. behind Cloudflare for free image egress). **Don't put exports in that bucket** — exports contain decrypted journal content + member names + everything else, and routing them through a CDN means cleartext personal data passes through CDN TLS termination even with presigned URLs (signatures prevent caching, but bytes still flow through CDN's network).

Set up a separate bucket and point Sheaf at it:

```env
S3_EXPORT_BUCKET=sheaf-exports
S3_EXPORT_ENDPOINT=https://s3.eu-central-1.amazonaws.com   # direct, not via CDN
# S3_EXPORT_PRESIGN_ENDPOINT=  # only set if presign URL needs different host
```

Apply an S3 lifecycle expiry rule on the bucket as belt-and-braces — if Sheaf's cleanup worker silently fails, S3 still cleans up:

```json
{
  "Rules": [{
    "ID": "expire-sheaf-exports",
    "Status": "Enabled",
    "Filter": {"Prefix": "exports/"},
    "Expiration": {"Days": 4}
  }]
}
```

(4 days = 72h app TTL + 1 day grace.)

A storage class like Standard-IA is appropriate since objects are short-lived, if you want to save pennies. Block public ACLs on this bucket; downloads always go through Sheaf's authenticated endpoint via presigned URL.

#### Encryption at rest

Set bucket default encryption rather than asking Sheaf to specify it per-object — Sheaf intentionally doesn't pass `ServerSideEncryption` headers, because MinIO rejects them when KMS isn't configured (AWS S3 silently uses the bucket default).

For AWS S3:

```sh
aws s3api put-bucket-encryption \
  --bucket sheaf-exports \
  --server-side-encryption-configuration '{
    "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
  }'
```

For MinIO with KMS configured: set bucket encryption via `mc encrypt set sse-s3 <alias>/sheaf-exports`. For MinIO without KMS: the underlying disk is your encryption boundary; the data is still application-layer-encrypted in the database for sensitive fields, and the export zips are short-lived.

### Filesystem deployments

When `STORAGE_BACKEND=filesystem`, exports live at `/app/data/exports/{user_id}/{job_id}.zip`. Same cleanup worker handles pruning. No CDN concerns since bytes never leave the box.

### Image re-import asymmetry

The export-with-images zip contains JSON references AND the image bytes. The import path accepts JSON only — it does NOT auto-restore image attachments from the zip. The export UI tells users this explicitly. Re-importing the zip into another Sheaf instance brings the text content (members, journals, etc.) but image attachments need to be re-uploaded by hand.

This is intentional: auto re-import would require re-keying every image (server generates UUIDs, not filename-based), rewriting `image_keys` references across members/journals/revisions, re-running quota/dedup/virus-scan/EXIF-stripping, and handling cross-backend migration. Demand is probably low — GDPR compliance and personal backup don't need it.

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

## Revision history retention

Member bios and journal entries capture a revision on every edit. The rolling sweep keeps the newest N revisions per target and trims anything older than M days. Both caps apply per-tier (0 = unlimited):

```env
JOURNAL_MAX_REVISIONS_FREE=10
JOURNAL_MAX_REVISIONS_PLUS=100
JOURNAL_MAX_REVISIONS_SELFHOSTED=0
JOURNAL_MAX_REVISION_DAYS_FREE=30
JOURNAL_MAX_REVISION_DAYS_PLUS=365
JOURNAL_MAX_REVISION_DAYS_SELFHOSTED=0

# How often the sweep runs.
JOURNAL_GC_INTERVAL_HOURS=6

# Notice period before a tier downgrade actually trims older revisions.
TIER_DOWNGRADE_GRACE_DAYS=14
```

Per-system overrides (Settings -> Safety -> Revision retention) can lower the cap below the tier max but never raise it. Reductions made while System Safety is active are deferred through the grace flow before applying.

### Pinned revisions

Pinned revisions are exempt from the rolling sweep, so they form a separate per-target storage budget. The cap applies per journal entry / per member bio (0 = unlimited):

```env
PINNED_REVISION_MAX_PER_TARGET_FREE=3
PINNED_REVISION_MAX_PER_TARGET_PLUS=5
PINNED_REVISION_MAX_PER_TARGET_SELFHOSTED=10
```

The `auto_pin_first_revision` toggle (per-system, default on) auto-pins the first captured revision so casual users get baseline protection against history-spam eviction without UI discovery. The `applies_to_revisions` Safety toggle gates whether unpin requires re-auth + grace.

---

## System Safety

Optional grace + re-auth on destructive actions: member/group/tag/field/front/journal/image deletes, plus revision unpin. Configured per-system in Settings -> Safety, no env vars: each system picks its own grace period (0 disables), auth tier (none / password / TOTP / both), and which categories the policy applies to.

Tightening (longer grace, stronger auth tier, enabling more categories) takes effect immediately. Loosening (shorter grace, weaker auth, disabling categories, lowering revision caps, disabling auto-pin) requires re-auth and is deferred behind the current grace period as a `SafetyChangeRequest` the user can cancel before it finalizes.

Pending destructive actions and pending safety changes are visible in Settings -> Safety. Both can be cancelled before `finalize_after`.

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

### Per-user hit history

When a rate-limit check blocks a request that can be attributed to a logged-in account, the hit (bucket, route, timestamp, IP) is recorded to a short-lived Redis list so an admin can see what that account has tripped recently (Admin -> Users -> Explain account). This is triage data, not analytics: it only records *blocked* checks, it is bounded per user, and it ages out on its own. Nothing is written to Postgres.

```env
RATE_LIMIT_HISTORY_ENABLED=true
RATE_LIMIT_HISTORY_HOURS=48          # retention window
RATE_LIMIT_HISTORY_MAX_ENTRIES=200   # cap per user
```

Set `RATE_LIMIT_HISTORY_ENABLED=false` to record nothing new; anything recorded before the switch remains visible until its retention TTL lapses. Anonymous traffic (failed logins from logged-out clients, the global per-IP backstop) is not attributable to an account and is never recorded here - the Prometheus metrics cover those in aggregate.

### Trusted proxies

When Sheaf sits behind a reverse proxy, the connecting IP is the proxy, not the client. Set `TRUSTED_PROXIES` to trust `X-Forwarded-For` headers from specific IPs:

```env
# Comma-separated IPs and/or CIDR ranges
TRUSTED_PROXIES=127.0.0.1

# Example for docker-compose where the reverse proxy is another container on
# an auto-assigned bridge network (IP varies across `compose up`):
# TRUSTED_PROXIES=127.0.0.1,172.16.0.0/12
```

If empty (default), `X-Forwarded-For` is never read — the direct connecting IP is used. This is safe but means all users behind the proxy share one rate-limit bucket.

Entries accept either a literal IP (`127.0.0.1`, `::1`) or a CIDR range (`172.16.0.0/12`). Invalid entries fail fast at startup rather than silently disabling XFF.

How the header is read: proxies append the peer they saw to whatever the client sent, so the left side of the chain is client-controlled. Sheaf walks `X-Forwarded-For` right to left and uses the first entry that is not itself listed in `TRUSTED_PROXIES` - the first hop the client could not have forged. Two consequences for multi-hop setups:

- List every proxy tier in `TRUSTED_PROXIES` (your edge LB and your local nginx, for example). An intermediate hop that is not listed will be treated as the client.
- A malformed entry in the chain, or a chain consisting only of trusted proxies, falls back to the direct connecting IP.

### Cross-origin request protection (CSRF)

Mutating requests that carry a Sheaf auth cookie and a browser `Origin` header must originate from the host the request was sent to (or `SHEAF_BASE_URL`). Requests without an `Origin` header (curl, scripts, the mobile apps) and pure bearer-token requests are never checked, so API consumers are unaffected.

Single-origin deployments (the normal case) need no configuration. If you legitimately serve the app from more than one origin, list the extras:

```env
# Comma-separated. Scheme optional; the host[:port] is what is compared.
CSRF_TRUSTED_ORIGINS=https://alt.example.net
```

---

## Performance tuning

Two work classes run in bounded worker-thread pools so a burst cannot stall request handling or exhaust memory. The defaults suit a 2 vCPU / 4GB box; raise them on bigger hardware if logins or image-heavy imports queue up:

```env
PASSWORD_HASH_CONCURRENCY=4    # concurrent Argon2 hashes (~64MB RAM each)
IMAGE_NORMALIZE_CONCURRENCY=4  # concurrent image decodes (up to ~100MB each)
```

---

## Metrics

Sheaf exposes a Prometheus-compatible `/metrics` endpoint covering HTTP RED, the auth funnel, rate-limit hits, notification dispatch, email send, job runner, imports/exports, System Safety, cf-shield events, and core data-shape gauges. Defaults bind a separate listener on `127.0.0.1:8090` with no auth - safe for single-node deploys scraped over loopback or a private network, NOT safe to forward through your edge without flipping `METRICS_AUTH=token`.

Full catalog, cardinality rules, multi-worker setup, and scrape-config examples live in [METRICS.md](METRICS.md).

---

## External images

By default, member bios and descriptions can reference external image URLs, and avatars can be set to an arbitrary HTTPS URL. To restrict images to only hosted uploads:

```env
ALLOW_EXTERNAL_IMAGES=false
```

When disabled:
- External `![image](https://…)` embeds are stripped from bios/descriptions on save.
- External avatar URLs are dropped to null on save.
- Importers drop external avatar links carried in other apps' exports (with a warning on the import report), so an import can't bypass the policy.
- The frontend hides the "External URL" option in the image picker and the link-icon button in the avatar picker.
- A CSP `img-src` directive blocks the browser from loading any non-hosted image as defense in depth.

Regardless of this setting, avatar URLs accepted from imports must be plain `http(s)` - other schemes are dropped.

The toggle does not retroactively scrub existing content — it only governs new writes. CSP blocks old references at render time.

---

## Image uploads

By default, any authenticated user can upload avatars and bio images. To disable uploads globally (e.g. for a public test instance without a ToS):

```env
ALLOW_IMAGE_UPLOADS=false
```

When disabled:
- Regular users see no upload button/tab in the UI and get HTTP 403 from `POST /v1/files/upload`.
- Admins can upload regardless.
- Any individual user can be allowlisted from the admin users page (**Uploads** column) or via `PATCH /v1/admin/users/{id}` with `{"can_upload_images": true}`. External image URLs are governed separately by `ALLOW_EXTERNAL_IMAGES`.

To keep avatar uploads on but block bio-image embeds (or vice versa), use the narrower toggle:

```env
ALLOW_BIO_IMAGES=false
```

Per-purpose size caps override `MAX_UPLOAD_SIZE_MB` when set (0 = inherit):

```env
MAX_AVATAR_SIZE_MB=1
MAX_BIO_IMAGE_SIZE_MB=10
```

### Server-side normalization

Every accepted upload is decoded, dimension-capped, EXIF-stripped, and re-encoded through Pillow before it ever lands in storage. This is unconditional - there is no env var to turn it off - and it protects against three classes of issue:

- **Decompression bombs.** A small-on-disk PNG can declare 50000x50000 pixels in its header and OOM a downstream renderer. Sheaf reads the declared dimensions before decoding and rejects anything whose pixel count would exceed `MAX_ANIMATED_DECODED_BYTES` (default 100 MB).
- **Metadata leaks.** Phone photos carry GPS coordinates, camera serial numbers, and capture timestamps in EXIF. Re-encoding through a clean Pillow image drops all of it.
- **Polyglot / parser tricks.** Re-encoding canonicalises the container so anything malicious in the original bytes is normalised out.

Tuning knobs (all optional):

```env
MAX_IMAGE_DIMENSION=4096         # longest edge in px; larger gets downscaled
MAX_ANIMATED_FRAMES=100          # cap for GIF / animated WebP
MAX_ANIMATED_DECODED_BYTES=104857600  # 100 MB decompression-bomb cap
IMAGE_NORMALIZE_CONCURRENCY=4    # concurrent decodes (bounds peak memory)
MAX_IMPORT_RESTORED_IMAGES=20000 # per-import image-restore cap (archive import)
```

The last knob bounds how many images one export-with-images archive import will restore. The storage quota already bounds the restored bytes; this bounds the number of normalization passes a single job can demand, so a crafted zip full of tiny images can't monopolise the import runner. An import that hits the cap completes with a warning and strips the remaining image references. Raise it if your system genuinely has more images than the default.

### Animated avatars

Animated GIF / animated WebP uploads are **flattened to their first frame by default**, regardless of how they were uploaded. To allow animation:

```env
ALLOW_ANIMATED_UPLOADS=true
```

The master switch alone is not enough - eligibility is also per-user. You can either grant it to a specific account from the admin users page (or via `PATCH /v1/admin/users/{id}` with `{"can_upload_animated_images": true}`), or - in a future release - by tier. Admins always bypass the per-user check when the master switch is on. With the master switch off, even admins get flattened uploads.

This split keeps the door open for "animated avatars as a premium feature" without code changes: SaaS deployments flip `ALLOW_ANIMATED_UPLOADS=true` and let the tier rule gate things; self-hosters who don't care just leave it off (today's behaviour) or turn it on for everyone via per-user grants.

---

## Public test / demo instance mode

For a public instance that periodically wipes itself (useful for try-before-you-host demos), Sheaf ships an optional `sheaf_dev` package that registers a scheduled wipe job and a permanent warning banner. It is **excluded from the default image** so production builds physically cannot run it.

### 1. Get the devtools image

CI publishes a separate variant built with `INCLUDE_DEV_TOOLS=true`:

```
ghcr.io/sheaf-project/sheaf-devtools:head      # tip of main
ghcr.io/sheaf-project/sheaf-devtools:vX.Y.Z    # pinned release
ghcr.io/sheaf-project/sheaf-devtools:latest    # latest release
```

Point your compose file at it, e.g.:

```yaml
services:
  app:
    image: ghcr.io/sheaf-project/sheaf-devtools:head
    # (omit the `build:` block)
```

Or build it yourself:

```bash
INCLUDE_DEV_TOOLS=true docker compose build app
```

### 2. Enable the wipe job

```env
DEMO_WIPE_ENABLED=true
DEMO_WIPE_INTERVAL_HOURS=24   # optional; default 24
```

When enabled, every N hours the job deletes all non-admin users along with their systems, members, fronts, uploaded files, sessions, etc. Admins are preserved. The job runner wakes every `JOB_CHECK_INTERVAL_MINUTES` (default 15), so the actual first fire may be up to that much after the interval elapses.

`SHEAF_MODE=saas` acts as a hard safety belt — the wipe job refuses to register even if `sheaf_dev` is installed and `DEMO_WIPE_ENABLED=true`.

### 3. The banner

When `sheaf_dev` is installed, a non-dismissible `ServerAnnouncement` titled **"Development Instance"** is created on first startup and shown site-wide. There is no env var — the banner is tied to the package being present. To remove it, rebuild without `INCLUDE_DEV_TOOLS=true` (and delete the row from the `server_announcements` table if it's already in your DB).

### Recommended companion settings

For a public demo you almost certainly also want:

```env
ALLOW_IMAGE_UPLOADS=false     # otherwise it's free image hosting between wipes
REGISTRATION_MODE=open        # or invite, depending on what you want to demo
EMAIL_VERIFICATION=off        # users come and go, verification loops add friction
```

---

## Support page

The in-app Support page (Support in the sidebar) has two parts. The lower half is static - it links to the Sheaf project's issue tracker and security policy, the same on every instance. The upper half is yours: an operator contact card populated from these optional env vars.

```env
SUPPORT_EMAIL=support@example.com      # mailto link
SUPPORT_URL=https://help.example.com   # your support site / help desk
SUPPORT_NOTE=Hours: Mon-Fri 9-5 UTC.   # free-text note shown above the links
STATUS_URL=https://status.example.com  # your status / uptime page
```

All four are independent and optional. The operator card is hidden entirely if you set none of them, so a bare self-host shows only the project section. `STATUS_URL` lives here (rather than in the static project section) because a self-hosted instance's status page is the operator's, not the project's.

These are surfaced read-only via `GET /v1/auth/config`, alongside `TERMS_URL` / `PRIVACY_URL`; like those, they're read at startup and changing them needs a restart.

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
        # Sheaf only sets security headers on its own /v1/* responses;
        # the SPA document is served by Caddy and needs them here.
        header {
            X-Frame-Options "DENY"
            X-Content-Type-Options "nosniff"
            Referrer-Policy "no-referrer"
            Content-Security-Policy "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https:; frame-ancestors 'none'; object-src 'none'; base-uri 'self'"
        }
    }
}
```

Caddy terminates TLS automatically and passes `X-Forwarded-Proto=https` to the backend, so Sheaf will emit `Strict-Transport-Security` on its own responses. Caddy does not add HSTS to its own static responses by default — add `header Strict-Transport-Security "max-age=31536000; includeSubDomains"` inside the site block once you're confident HTTPS is permanent.

**nginx example:**
```nginx
# Redirect plain HTTP to HTTPS
server {
    listen 80;
    server_name sheaf.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name sheaf.example.com;

    # TLS — modern profile. Adjust paths to your certificates.
    ssl_certificate     /etc/letsencrypt/live/sheaf.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/sheaf.example.com/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 1d;

    # HSTS — only enable after you're confident HTTPS is permanent for this
    # domain. Once browsers receive this, they won't fall back to HTTP.
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;

    # nginx defaults to a 1MB body cap, which 413s imports and uploads.
    # 110m fits the 100MB import limit plus multipart overhead.
    client_max_body_size 110m;

    # API
    location /v1/ {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Frontend SPA. Sheaf only sets security headers on its own /v1/*
    # responses; the SPA document is served by nginx and needs them here.
    location / {
        root /path/to/web/dist;
        try_files $uri /index.html;
        add_header X-Frame-Options "DENY" always;
        add_header X-Content-Type-Options "nosniff" always;
        add_header Referrer-Policy "no-referrer" always;
        add_header Content-Security-Policy "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https:; frame-ancestors 'none'; object-src 'none'; base-uri 'self'" always;
    }
}
```

Sheaf sets security headers (`X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`, `Content-Security-Policy`, `Permissions-Policy`, `Cross-Origin-Opener-Policy`, and conditionally `Strict-Transport-Security`) on its own `/v1/*` responses only. The SPA document and static assets are served by your proxy, which is why the examples above add the headers there too - without them the app page itself ships with no clickjacking or XSS defence-in-depth.

Two notes on the SPA CSP:
- `script-src` needs `'unsafe-inline'` because `index.html` carries a small inline script that applies the saved theme before first paint. A stricter hash-based policy would break on every release that touches that script.
- If you set `ALLOW_EXTERNAL_IMAGES=false`, you can tighten `img-src` to `'self' data: blob:` to match the API's own CSP.

If you serve the SPA from a different host than the API, set `CSRF_TRUSTED_ORIGINS` (see the CSRF section) or cookie-authenticated requests from the app will be rejected.

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

### Cookie Secure flag and `SHEAF_BASE_URL`

Auth cookies (`sheaf_session`, `sheaf_refresh`, trusted-device cookie) are marked `Secure` by default. Browsers silently drop Secure cookies on plain-HTTP origins, which breaks refresh-token rotation and login persistence.

Sheaf decides based on `SHEAF_BASE_URL`:

| `SHEAF_BASE_URL` | Cookie `Secure` flag | Use case |
|---|---|---|
| Unset / empty | **Set** | Production behind HTTPS reverse proxy (default) |
| `https://...` | **Set** | Production with explicit base URL |
| `http://...` | **Not set** | Plain-HTTP dev or trusted LAN only |

If you serve the UI over plain HTTP (e.g. `http://sheaf.lan` for a home setup) and don't set `SHEAF_BASE_URL`, login appears to work but refresh fails the moment the access token expires. Either put TLS in front (recommended) or set `SHEAF_BASE_URL=http://your-host` to opt out of the Secure flag.

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

### Handling backups responsibly

Sheaf stores GDPR Article 9 special-category data, so the dump itself is
sensitive — treat the backup file with the same care as the live database.

- **Encrypt the dump at rest.** Pipe `pg_dump` through `age` or `gpg`
  before it lands on disk, e.g.
  `docker compose exec db pg_dump -U sheaf sheaf | age -r <your-key> > sheaf-backup.sql.age`.
  An unencrypted `.sql` file on a laptop or in object storage is a breach
  waiting to happen.
- **Keep a copy off-host**, and rotate it — a backup on the same machine
  doesn't survive a disk failure or a ransomware event. Rotate old copies
  out so a single leaked snapshot has a bounded lifetime.
- **Back up the encryption key separately from the database.** If both
  live in the same archive, that archive is a single-file total
  compromise. The key (item 3 above) belongs in a different store than
  the dump.
- **Test your restore.** A backup you have never restored is a guess.
  Periodically restore into a throwaway database and confirm the app
  starts and decrypts data. The encryption key and the database must be
  from a consistent point in time, or login (blind-index lookup) breaks.

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
