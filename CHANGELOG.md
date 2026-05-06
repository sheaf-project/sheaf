# Changelog

All notable changes to Sheaf are documented here. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and Sheaf adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

`v0.x.y` releases are betas — APIs and database schema may still change. The first stable release will be `v1.0.0`.

## [Unreleased]

### PluralKit import

- New importer accepts both PK data export files (from `pk;export`) and live API pulls using the user's PK token (from `pk;token`). Same preview / options / result schema for both paths so the UI is uniform. Token is forwarded once and never logged or persisted.
- PK switch events (state-change point-in-time records) are converted to Sheaf front intervals via an oldest-to-newest walk: each switch ends the previous open Front and starts a new one with the resolved member set, with empty member sets handled as "nobody fronting" gaps. Members spanning multiple switches end up in multiple Front records, which the existing coalesce-contiguous-fronts feature reassembles on display.
- Member migration covers name, display name, color, pronouns, avatar, description, and birthday (including the PK `0004-MM-DD` year-less sentinel collapsed to `MM-DD`). PK's per-field privacy map is collapsed to Sheaf's tri-level `privacy` enum via the overall `visibility` field, falling back to the most-restrictive flag.
- New nullable `pluralkit_id` column on `members` records each imported member's PK HID, surfaced in the member edit form for users who manually cross-reference between Sheaf and PK.
- API surface: `POST /v1/import/pluralkit[/preview]` (multipart file) and `POST /v1/import/pluralkit-api[/preview]` (JSON body with token), both gated by the existing `import:write` scope.
- Frontend: new "Import from PluralKit" card on the import page with a file-or-token sub-flow and switch-range preview.

### Coalesce contiguous fronting

- New `system.coalesce_contiguous_fronts` toggle (default on). When a member appears in a chain of back-to-back front entries (e.g. solo &rarr; cofront via `replace_fronts=true`), their "fronting since" walks back to the earliest entry in the chain instead of resetting on each new entry. Surfaced as `Front.member_since` on `/v1/fronts/current` — a per-member-id map of effective fronting-since timestamps. Existing `front.started_at` is unchanged; coalescing is a derived view, not a rewrite.
- Settings &rarr; Fronting gains a "Coalesce contiguous fronting" toggle.
- Dashboard and Fronts page badges now show per-member timers ("Alice 8h", "Bob just now") inside each badge instead of one shared "since" at the front level.
- Bug fix as a side effect: `replace_fronts=true` previously set the auto-ended front's `ended_at` and the new front's `started_at` from two separate `datetime.now()` calls a few ms apart, leaving a tiny gap that this feature would have noticed even without the toggle. Both timestamps are now strictly equal.

### Tag membership

- New `PUT /v1/tags/{id}/members` and `PUT /v1/members/{id}/tags` (with `GET` siblings) — symmetric m2m endpoints for managing which members carry which tags. Mirrors the existing groups pattern. Closes a real gap: the `Tag.members` relationship existed in the model and tags were already exported with `member_ids`, but no API surface populated the join (only the Sheaf-import service did, via raw SQL).
- Settings → Members: tag chips on the member view with inline editing.
- Member picker (start-front dialog and friends): tag filter chips alongside the existing group filter; AND together so you can pick e.g. "everyone in Core *and* tagged creative".
- Seed script (`scripts/seed_bulk_system.py`) now scatters tags across members.

### Account & data exports

- `/v1/export` (Article 20, data portability) now includes journal entries, content revisions (bio + journal edit history), system safety settings, retention overrides, system preferences, watch tokens with their notification channels (config only; per-instance state and webhook secrets omitted), and a file inventory listing every uploaded blob's key + size + content type. Bumped to version `2`. Re-importable into another Sheaf instance via the existing import flow.
- New `POST /v1/account/data` (Article 15, right of access) — returns everything Sheaf holds *about* the user account: identity, sessions with IPs, trusted devices, API key audit metadata, TOTP enrolment status, email delivery state, pending safety actions, receiving notification channels, retention trim notices. Always requires password + TOTP-if-enrolled regardless of the system's `delete_confirmation` setting; refuses API-key auth.
- New `POST /v1/export/jobs` — async export including image bytes. Builds a zip in the background, persists to S3 (or local disk on filesystem deployments), notifies via email when ready. 72-hour TTL by default. Same step-up auth as Article 15. Per-user concurrency limit of 1.
- Dedicated S3 bucket settings for exports (`S3_EXPORT_BUCKET`, `S3_EXPORT_ENDPOINT`, `S3_EXPORT_PRESIGN_ENDPOINT`) — operator can put exports in their own bucket with an S3 lifecycle rule and bypass any CDN fronting on the image bucket. Strongly recommended in production since exports contain decrypted personal data.
- Frontend Settings → Data export grows three actions: sync JSON export (existing), full backup with images (new, password-prompted), download account data (Article 15). Recent backups list shows status + download link when ready.

### Front-change notifications

- New `/notifications` surface: owners issue watcher tokens, each carrying one or more channels with independent filters, triggers, payload sensitivity, and delivery shaping.
- Four destination types: web push (VAPID), webhook (json/discord/slack/plaintext, HMAC-signed for json/plaintext, SSRF-guarded), ntfy, Pushover.
- Three-layer per-member visibility resolution at dispatch time (base set + group rules + member overrides), with private-member opt-in and configurable redaction (`count` / `someone` / `suppress`) for invisible co-fronters.
- Aggregated event payload: a single front-change action — even with many members moving — produces one notification per channel, summarising the whole transition. Avoids webhook rate limits and notification fatigue.
- Recipient-side capability URL for unsubscribe; account-bound subscriptions tighten to require the redeemer's session for management.
- System Safety integration: channel deletion and watcher revocation can be safeguarded with grace + re-auth, matching every other destructive action.
- API keys: dedicated `notifications:read|write|delete` scopes (separate from `members`); journals also moved to their own `journals:*` scopes.
- Pushover BYO app token: recipients can paste their own Pushover application token into the channel's "Advanced" config to bypass all shared-app limits — they hit their own Pushover quota instead.
- Pushover monthly quota tracking: new `PUSHOVER_MAX_PER_MONTH` setting (default 10000) caps shared-app deliveries deployment-wide per calendar month; usage surfaced on `/admin` and at `GET /v1/admin/pushover-usage`.
- Per-user-tier Pushover allowance: new `PUSHOVER_USER_MAX_PER_MONTH_{FREE,PLUS,SELF_HOSTED}` settings (defaults 100/1000/0) stop one Sheaf user from monopolising the deployment quota. Surfaced to the user at `GET /v1/notifications/pushover-usage` and shown on their notifications page.
- Pushover shared-app debounce floor: new `PUSHOVER_SHARED_APP_MIN_DEBOUNCE_SECONDS` setting (default 1800) protects the deployment-wide cap from one chatty system burning everyone's quota. Surfaced to the channel form via `GET /v1/notifications/server-config`. BYO channels are exempt.
- Quiet hours respect the channel's timezone instead of UTC-only. The QuietHours schema gained an IANA-validated `tz` field (defaults to `UTC`); the dispatcher computes window boundaries with `zoneinfo` so DST transitions move the window correctly. Frontend gains a tz picker populated from `Intl.supportedValuesOf("timeZone")`, defaulting to the recipient's browser timezone for new configs.

## [v0.1.0] - 2026-04-29

First public beta. The features below are the baseline that subsequent releases build on.

### Plural system tracking

- Members with name, pronouns, role, description, color, avatar, custom fields, tags, groups, and per-member privacy.
- Front log: who's currently fronting, history, and timeline view.
- Journals: per-member and system-wide markdown entries with image embeds, fronting snapshots, revision history with retention.
- System Safety: configurable grace periods on destructive actions (member/journal/image deletes, retention loosening) with re-auth.
- Encrypted at rest: member name, descriptions, journal content, custom field values, email, TOTP secrets — all application-level encrypted; lookups use blind indexes.

### Auth & accounts

- Argon2id password hashing, optional TOTP, trusted-device enrolment.
- HttpOnly refresh-cookie sessions with reuse-detection grace window.
- API keys with per-resource scopes; admin scopes are admin-gated.
- Account deletion with grace period; admin promotion via env-driven email list.

### Self-hosting & operations

- Multi-arch Docker images on GHCR for the backend (`sheaf`) and frontend (`sheaf-web`); `docker compose` reference setup.
- Postgres + Redis required; Alembic runs `upgrade head` on container start.
- Storage adapters: local disk and S3-compatible.
- Email adapters: SMTP, SES, SendGrid (optional dependencies).
- `SHEAF_MODE` flag toggles selfhosted vs SaaS behaviour without forking.

### Build verifiability

- `/v1/version` endpoint reports the running commit, tag, and build time.
- Multi-arch Docker images on GHCR signed via `sigstore/cosign` keyless OIDC.
- SPDX SBOMs published as Sigstore attestations against each image.
- Frontend bundle protected by sha384 SRI integrity attributes.
- `build-manifest.json` listing every dist file's hash, also published as a Sigstore attestation against the `sheaf-web` image.
- `/about` page surfaces backend + frontend build provenance and a manifest summary.
- `scripts/verify-release.sh` automates `/v1/version` → cosign verification.
- See [docs/VERIFYING.md](docs/VERIFYING.md) for the full trust model.

### Releases

- Tag-driven release workflow with a manual approval gate via the `release` GitHub Environment.
- Release assets: signed Docker images on GHCR, frontend tarball, build manifest, SPDX SBOM attestations.
