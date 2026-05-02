# Changelog

All notable changes to Sheaf are documented here. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and Sheaf adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

`v0.x.y` releases are betas — APIs and database schema may still change. The first stable release will be `v1.0.0`.

## [Unreleased]

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
