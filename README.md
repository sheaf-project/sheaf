# Sheaf

[![GitHub Release](https://img.shields.io/github/v/release/sheaf-project/sheaf?include_prereleases&sort=semver&display_name=release&style=plastic&link=https%3A%2F%2Fgithub.com%2Fsheaf-project%2Fsheaf%2Freleases%2F)](https://github.com/sheaf-project/sheaf/releases)
[![Discord](https://img.shields.io/discord/1483687251492868217?style=plastic&logo=discord&label=Discord&link=https%3A%2F%2Fdiscord.com%2Finvite%2FWFaKQPzFx8)](https://discord.com/invite/WFaKQPzFx8)
[![GitHub Actions Workflow Status](https://img.shields.io/github/actions/workflow/status/sheaf-project/sheaf/ci.yml?branch=main&style=plastic&logo=github&label=CI)](https://github.com/sheaf-project/sheaf/actions)

![transrights](https://pride-badges.pony.workers.dev/static/v1?label=trans%20rights&stripeWidth=6&stripeColors=5BCEFA,F5A9B8,FFFFFF,F5A9B8,5BCEFA)
![enbyware](https://pride-badges.pony.workers.dev/static/v1?label=enbyware&labelColor=%23555&stripeWidth=8&stripeColors=FCF434%2CFFFFFF%2C9C59D1%2C2C2C2C)
![pluralmade](https://pride-badges.pony.workers.dev/static/v1?label=plural+made&labelColor=%23555&stripeWidth=8&stripeColors=2e0525%2C553578%2C7675c3%2C89c7b0%2Cf4ecbd)

> *noun*: a bundle; in mathematics, a structure that describes how local pieces fit together into a coherent whole.

Open-source, self-hostable plural system tracking, built with data security and sustainability in mind.

> **Status:** selfhostable; hosted at [app.sheaf.sh](https://app.sheaf.sh), free with open signups. [test.sheaf.sh](https://test.sheaf.sh) remains a public sandbox where data may be wiped at any time. Feedback welcome via [issues](https://github.com/sheaf-project/sheaf/issues) or our [Discord](https://sheaf.sh/discord).

[Android/WearOS](https://github.com/sheaf-project/android) is on the [Play Store](https://play.google.com/store/apps/details?id=systems.lupine.sheaf) (or sideload the APK from [Releases](https://github.com/sheaf-project/android/releases)), and [iOS/WatchOS](https://github.com/sheaf-project/ios) is on the [App Store](https://apps.apple.com/us/app/sheaf-plural-system-tracker/id6766770364). Both ship a watch companion app and complication.

Sheaf supports the [PluralPort](https://github.com/PluralPort/spec) data standard proposal (formerly OpenPlural) as a founding project, and ships import and export for v0.1 today.

## Why

A plural system's records are among the most sensitive data a person can keep, and they need a tracker that will still be running next year and whose data handling they can inspect for themselves. Many of the options are incomplete, closed-source, local-only, or lack credible infrastructure foundations. Sheaf is built by people who are actually paid to run things at scale, with a focus on:

- **Data security** — Email and TOTP secrets are encrypted at rest (application-level). All data is GDPR Article 9 special category data and is treated accordingly.
- **Self-hosting first** — `docker compose up` and you have your own instance
- **Sustainable economics** — Designed from day 1 to support both selfhosting and an optional hosted tier without forking the codebase or using proprietary extensions
- **Contributor accessibility** — Python/FastAPI backend, React frontend.

## Features

- **Web, mobile, and wearable apps** - Sheaf also supports first-class API support for custom clients and integrations, and development of custom or alternative clients for the Sheaf API is encouraged.
- **Members** - Profiles with name, pronouns, description, colour, birthday, avatar, wide banner image, emoji, privacy levels, optional PluralKit ID
- **Custom fronts** — Mark non-counting fronting entities like "Asleep" or "Away" so they show up in the fronter list without inflating member counts
- **Front tracking** — Log switches with cofronters and an optional encrypted free-text status per fronting period
- **Analytics** — Per-member front time, percent of window, session count, longest session, and hour-of-day distribution over a configurable window (7d / 30d / 90d / 1 year). Co-fronting double-counts so individual member stats are accurate.
- **Reminders** — Schedule daily/weekly/monthly pings or fire reminders X minutes after a member fronts. Member-scoped reminders queue while nobody on the list is fronting and drain as a digest when one next switches in. Delivery rides your existing notification channels.
- **Polls** — Run a vote across the system. Each vote is attributed to a specific member who must be in the current front, with a full audit log of cast / change / withdraw events plus a fronting snapshot. Single or multi-choice, results live or hidden until close, hard deadline at creation with auto-purge after retention.
- **Notes** — Lightweight scratchpad per member and per system. Markdown, encrypted at rest, intentionally without revision history or System Safety protection - for "trigger list / fav drink / current med doses" quick reference where journals' versioning is overkill.
- **Messages** — Global system message board plus a per-member wall, so headmates can leave each other notes inside the system. Replies chain (no nested threads), edits keep revision history, deletes are soft and gated by System Safety. Per-member unread counts power the sidebar badge and an opt-in "you have N unread" prompt when you start fronting.
- **Groups** - Organize members into groups, nested up to 8 levels (subsystems), with a drag-to-reparent tree and subtree-inclusive group filtering. Arrows reorder a group among its siblings; the order you set is what every list shows, and it travels with your backups.
- **Relationships** - Typed relationships between members and between subsystems (partner, parent/child, protector, or your own types), with symmetric / directional / either direction modes, mapped as a self-arranging system graph you can pan, zoom, and build on directly
- **Archived members** - Soft-hide a member from lists, pickers, and the front switcher without losing their name anywhere it already appears in history
- **Tags** — Flexible member tagging
- **Custom fields** - Define your own fields (text, number, date, boolean, select), reorderable, with a per-field privacy level that governs whether the field can appear on anything you share
- **Journals** — Per-member or system-wide markdown journal entries with edit history
- **Revision history** — Member bios and journal entries are versioned, with tier-aware retention caps
- **Revision pinning** — Pin specific revisions to protect them from automatic trim, with optional re-auth + grace on unpin
- **Front-history retention** - Opt-in per-system window that ages out old closed fronts. A privacy control rather than a tier limit: off by default, with a 14-day import grace (operator-configurable) so a freshly restored archive is never abruptly deleted, and tightening it takes a deferred, re-auth-gated countdown.
- **Public profiles and share links** - Show part of your system to people outside it, if your instance's operator has turned sharing on. Sharing is built around *views*: a named, curated pick of exactly which members, custom fields, groups and relationships are shown, pointed at an audience by a *grant* - either a public profile at `/p/<system id>`, or an opaque share link at `/s/<token>` that carries nothing identifying the system and can be revoked or rotated whenever you like. Nothing is shown that was not deliberately added to a view, and nothing is reachable until a grant exists. A member can be marked never shareable (can never appear in any view, at all) or fronting-private (may appear, but their front state is never shown). Every way of showing *more* waits out your System Safety grace period, needs re-auth, and needs a one-off "I am 18 or older" confirmation; showing less always takes effect at once. "Preview as visitor" renders the real server-side projection so you can see a view before anyone else does. Backups round-trip your views but never your grants, so restoring one never republishes anything.
- **System Safety** — Optional grace period and re-auth (password / TOTP) on destructive actions (member/journal/group/etc deletion, revision unpin)
- **Realtime front stream** - `GET /v1/fronts/stream` pushes your front changes over Server-Sent Events instead of making you poll, aimed at home automation (Home Assistant, Node-RED) and live UI updates. The client dials out and holds the connection open, so a LAN-only consumer works with no inbound reachability at all. There is an official [Home Assistant integration](https://github.com/sheaf-project/sheaf-ha) built on it.
- **Imports** - SimplyPlural, PluralKit (export file or live via your `pk;token`), Tupperbox, PluralSpace, Prism, Ampersand, PluralPort, Octocon and compatible forks (via their PluralKit-shaped export), and Sheaf's own exports (the JSON, or the with-images zip, which restores your uploaded images too). Granular control over what to bring across; PK switch log is converted to Sheaf front intervals, and the preview tells you what will be deduplicated, shortened, or capped before you commit to it. The import itself runs in the background, so a big file does not tie up your browser, and finishes with a report of what it did. See **[docs/IMPORT.md](docs/IMPORT.md)** for the full migration guide.
- **PluralPort** - Import and export for [PluralPort](https://github.com/PluralPort/spec) v0.1 (formerly OpenPlural; files written against the old name still import), as either a single JSON document or a `.pluralport.zip` bundle carrying image bytes. Sheaf data the draft spec doesn't model yet rides in a namespaced extensions key so a round-trip is lossless, and other apps' unmodellable data is preserved on import and re-emitted on the next export rather than dropped. See **[docs/PLURALPORT.md](docs/PLURALPORT.md)**.
- **File storage** — File uploads with filesystem or S3-compatible backends
- **Data export** — sync JSON (Article 20 portability), async zip with image bytes that imports back as-is, and a separate Article 15 endpoint covering everything we know about your account
- **Front-history export** - Fronting history on its own as CSV (one row per front, with duration and co-fronters), JSON, or ICS to drop into a calendar app
- **Display preferences** - Date format and display timezone as per-account settings that sync across your devices, with a per-device override so a machine in another zone can pin its own
- **2FA** — Optional TOTP with recovery codes
- **API keys** — Scoped, named keys (`sk_…`) for scripts and integrations
- **Account activity log** - A curated record of consequential account actions and automated actions on your data, carrying no member content and no IP, included in the Article 15 access export
- **Admin dashboard** — User management, invite codes, storage audit, background job monitoring, server announcements (inline links, settable expiry), optional step-up auth
- **Abuse investigation tooling** - Append-only security-event log covering the auth funnel with originating IP, with exact-IP and CIDR lookup, a top-failing-IPs credential-stuffing view, and per-account auth timelines. Bounded retention window, and targeted lookups require a stated reason and write an admin audit row.
- **Operator kill switch** - Halt every data-deleting background job in one move while investigating a retention issue, instead of zeroing each job's interval one at a time
- **Registration modes** — Open, approval-required, invite-only, or closed
- **Email verification** — Optional required verification with configurable flow
- **Account deletion** — Self-service with configurable grace period
- **Field-level encryption** — Member names/bios, journal titles/bodies, and revision history encrypted at rest with XChaCha20-Poly1305
- **Appearance** - 15 colour palettes (Classic, OLED, Sepia, Ocean, several pride flags, and more) crossed with light / dark / follow-my-system, defaulting to dark, with Dark Reader compatibility. Your pick can sync across your devices through your account or stay local to one browser, your choice.
- **Image uploads** - Avatars, member banners, and images embedded in bios and journals. An in-browser cropper (with zoom and rotate) frames the image before it is sent, and every accepted upload is re-encoded server-side: EXIF stripped, dimensions capped, decompression bombs refused, animation flattened unless the operator allows it.

## FAQ

See [FAQ.md](FAQ.md)

## Quick Start

For a small single-instance self-host, the all-in-one image bundles the backend, the web UI, and a Caddy reverse proxy with automatic Let's Encrypt HTTPS in one container, alongside Postgres. It generates its own secrets on first start:

```bash
cp .env.example .env
# Set AIO_DOMAIN=sheaf.example.com in .env, and point that domain's DNS here
docker compose -f docker-compose.aio.yml up -d
```

Publish ports 80 and 443 and you are done. Leave `AIO_DOMAIN` unset to serve plain HTTP on port 80 for a LAN instance or one behind your own TLS proxy. No public IP or cannot forward ports? Set `CF_TUNNEL_TOKEN` and the bundled `cloudflared` serves over an outbound-only Cloudflare Tunnel instead. The all-in-one is not horizontally scalable; outgrow it and you move to the split images below.

The secrets it generates on first start are persisted, not regenerated per boot, and they need backing up. See [the table below](#generating-secrets).

For the split backend/frontend images, where you serve the frontend and terminate TLS yourself:

```bash
cp .env.example .env
# Edit .env - at minimum, change POSTGRES_PASSWORD
docker compose up -d
```

The API is available at `http://localhost:8000` with interactive docs at `http://localhost:8000/v1/docs`. `/health` is an always-200 liveness probe; `/health/ready` checks the database and Redis under a tight timeout for use as a readiness gate.

`DATABASE_URL` is optional for the bundled database: setting `POSTGRES_PASSWORD` is enough and Sheaf derives the connection string from it, so the credential cannot drift between the two. Set it explicitly only for an external database or custom driver options.

### Generating secrets

```bash
# JWT secret
python -c "import secrets; print(secrets.token_urlsafe(32))"

# Encryption key (optional — auto-generated on first start if not set)
python -c "import secrets; print(secrets.token_hex(32))"
```

> **Important: back up the secrets, not just the database.** Anything you do not set yourself, Sheaf generates on first start and persists. Those generated files are a backup target alongside your database and uploaded files, and restoring a database without them does not get you a working instance.
>
> | Secret | If unset, generated at | Lose it and |
> |--------|----------------------|-------------|
> | `SHEAF_ENCRYPTION_KEY` | `data/encryption.key` | **All encrypted data is unrecoverable.** Emails, TOTP secrets, member names and bios, journals, notes, status notes. There is no recovery path. |
> | `JWT_SECRET_KEY` | `data/jwt_secret` (split stack) or `/secrets/jwt_secret` (all-in-one) | Every issued token is invalid. Recoverable: everyone just logs in again. |
> | `POSTGRES_PASSWORD` | `/secrets/postgres_password` (all-in-one only) | The app cannot reach its own database until you reset the password. |
>
> Setting them explicitly in `.env` is recommended precisely because it puts them somewhere you already back up. If you let them auto-generate, back up the files.

## Web UI

The web UI is a React SPA in `web/`. For development:

```bash
cd web
npm install
npm run dev
```

This starts Vite's dev server on `http://localhost:5173` with a proxy to the API at `:8000`.

## Architecture

```
sheaf/
├── sheaf/                  # Python backend
│   ├── main.py             # FastAPI app with lifespan management
│   ├── config.py           # Pydantic Settings (twelve-factor config)
│   ├── models/             # SQLAlchemy 2.0 async models
│   ├── schemas/            # Pydantic request/response models
│   ├── api/v1/             # Versioned API routes
│   ├── auth/               # JWT, sessions, TOTP, password hashing
│   ├── storage/            # File storage abstraction (filesystem/S3)
│   └── services/           # Business logic (retention, import)
├── web/                    # React + TypeScript + Vite + Tailwind
├── alembic/                # Database migrations
├── tests/                  # pytest test suite
├── docs/                   # Self-hosting, client dev, import, metrics, PluralPort, build verification
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

**Tech stack:** Python 3.12+, FastAPI, SQLAlchemy 2.0 (async), PostgreSQL 16, Redis, Alembic. Frontend: React 19, TypeScript, Vite, Tailwind CSS v4, shadcn/ui. Field-level encryption with XChaCha20-Poly1305 (libsodium).

## API

All endpoints are under `/v1/`. The OpenAPI spec is auto-generated at `/v1/openapi.json`.

**Auth:** Two methods are supported:
- **JWT bearer tokens** (15min access + 30d refresh) — for interactive clients. `POST /v1/auth/login` returns tokens; pass as `Authorization: Bearer <token>`.
- **API keys** (`sk_…` prefixed) — for scripts and integrations. Create in Settings; pass as `Authorization: Bearer sk_…`. Keys are scoped (e.g. `members:read`, `members:write`) and never expose the plaintext after creation.

Key endpoints:

| Endpoint | Description |
|----------|-------------|
| `POST /v1/auth/register` | Create account |
| `POST /v1/auth/login` | Login, get tokens |
| `GET /v1/auth/me` | Current user info |
| `GET/POST /v1/auth/keys` | List/create API keys |
| `DELETE /v1/auth/keys/{id}` | Revoke API key |
| `GET /v1/systems/me` | Your system profile |
| `GET/POST /v1/members` | List/create members |
| `GET/POST /v1/fronts` | Front history |
| `GET /v1/fronts/current` | Who's fronting now |
| `GET /v1/fronts/stream` | Live front changes over Server-Sent Events |
| `GET/POST /v1/groups` | Groups |
| `PUT /v1/groups/reorder` | Reorder groups |
| `GET /v1/relationship-types` | Relationship types (partner, parent/child, your own) |
| `POST /v1/member-relationships` | Relate two members |
| `POST /v1/group-relationships` | Relate two groups (subsystems) |
| `GET /v1/relationships/graph` | Whole-system relationship graph |
| `GET/POST /v1/tags` | Tags |
| `GET/POST /v1/fields` | Custom field definitions |
| `PUT /v1/members/{id}/fields` | Set custom field values |
| `PUT /v1/fields/reorder` | Reorder custom fields |
| `GET/POST /v1/journals` | List/create journal entries |
| `GET /v1/journals/{id}/revisions` | Edit history for an entry |
| `POST /v1/journals/{id}/pin-revision` | Pin a revision (exempt from trim) |
| `POST /v1/journals/{id}/unpin-revision` | Unpin (immediate or queued behind grace) |
| `GET/PATCH /v1/system/safety` | System Safety settings + pending actions |
| `GET/POST /v1/share-views` | List/create a curated view for sharing |
| `GET/POST /v1/share-grants` | List/create a grant (public profile or share link) |
| `GET /v1/sharing/audit` | What is currently visible to whom, per view |
| `GET /v1/public/systems/{id}` | Anonymous: a published public profile |
| `GET /v1/public/shared/{token}` | Anonymous: a share link |
| `GET /v1/account/activity` | Your account activity log |
| `POST /v1/imports/file` | Queue an import from an export file (`source=` picks the format) |
| `POST /v1/imports/api` | Queue an import of a PluralKit system live via `pk;token` |
| `GET /v1/imports` | Import job history |
| `GET /v1/imports/{id}` | One job's status, counts, and full event report |
| `POST /v1/import/{source}/preview` | Preview a file before committing to it |
| `GET /v1/export` | Export plural system content (sync JSON; `format=pluralport` for PluralPort) |
| `POST /v1/export/jobs` | Queue an async export: full backup with image bytes, a `.pluralport.zip` bundle, or front history as CSV / JSON / ICS |
| `POST /v1/account/data` | Article 15 — everything we know about your account |
| `POST /v1/files/upload` | Upload avatar |

Full interactive docs: `http://your-instance/v1/docs`

**Building a client?** See **[docs/CLIENT_DESIGN.md](docs/CLIENT_DESIGN.md)** for the complete client development guide — auth flows, scopes, session management, client settings storage, and all endpoints.

## Self-Hosting

```bash
cp .env.example .env
# Edit .env - at minimum, change POSTGRES_PASSWORD
docker compose up -d
```

See **[docs/SELFHOSTING.md](docs/SELFHOSTING.md)** for the full guide covering:

- Secrets and encryption key management
- Admin access and step-up authentication
- Optional dependencies (S3, SMTP, SES, SendGrid)
- Email configuration (SMTP / AWS SES / SendGrid) with bounce/complaint handling
- Registration modes (open / approval / invite / closed), email verification, and the optional Altcha captcha
- Account deletion with configurable grace period
- File storage (filesystem / S3) with hotlink protection
- Storage quotas and upload limits
- Revision-history retention caps and pinned-revision tier knobs
- System Safety (destructive-action grace, re-auth, per-category toggles)
- Frontend build and serving
- Reverse proxy setup (nginx, Caddy) and the `SHEAF_BASE_URL` / cookie-Secure relationship
- Rate limiting, per-tier limits, and trusted proxies
- Delivering webhooks and ntfy to your own LAN (`WEBHOOK_ALLOWED_PRIVATE_CIDRS`), and why it is off by default
- Mobile push (FCM / APNs), and why it needs your own app builds
- Public profiles and share links (`PUBLIC_PROFILES_ENABLED`), plus the proxy and log hygiene an anonymous surface needs
- Background jobs, the data-deletion kill switch, and every retention window
- Import job runner and the per-job import caps
- Shield mode (cf-shield), for a break-glass CDN posture
- Custom Support-page text for your own FAQ or house rules (`CUSTOM_SUPPORT_TEXT_FILE`)
- The all-in-one image, including the Cloudflare Tunnel path
- Proxy directives the realtime front stream needs (do not compress SSE)
- Public test / demo mode (periodic non-admin wipe + warning banner)
- Backups

## Verifying your build

Sheaf publishes signed Docker images and a verifiable frontend bundle so users can confirm a running instance corresponds to the public source. Image signatures use [sigstore/cosign](https://github.com/sigstore/cosign) keyless OIDC (no key material to manage; signatures tied to the GitHub Actions workflow identity, recorded in Rekor's public transparency log). The frontend ships with Subresource Integrity hashes and a published build manifest, so a browser-side verifier can confirm byte-for-byte that loaded JavaScript matches the published source.

See **[docs/VERIFYING.md](docs/VERIFYING.md)** for the trust model, how to run `cosign verify`, how to compare the served `build-manifest.json` against your own `npm run build`, and what the design explicitly does *not* claim (no hardware attestation; backend behaviour beyond the served frontend is operator-attested).

## Development

```bash
# Backend
pip install -e ".[dev]"
docker compose up db redis -d
alembic upgrade head
uvicorn sheaf.main:app --reload

# Frontend
cd web && npm install && npm run dev

# Full test suite (spins up an isolated Docker stack, tests all server configs)
./run_tests.sh

# Quick run against an already-running local server
# SHEAF_TEST_DB_URL needed so the admin fixture can reach Postgres directly
SHEAF_TEST_DB_URL=postgresql+asyncpg://sheaf:<POSTGRES_PASSWORD>@localhost:5432/sheaf pytest
```

## Roadmap

Shipped items are listed here for context; the [CHANGELOG](CHANGELOG.md) has the per-release detail.

- [ ] Named fronts - save a named combination of members and make them searchable in the start front dialog
- [ ] CLI similar to [simplyplural-cli](https://github.com/SiteRelEnby/simplyplural-cli)
- [x] Front-change notifications - web push, mobile push (FCM + APNs), webhook (json/discord/slack/plaintext), ntfy, Pushover. Per-channel filters with three-layer member visibility (base + group rules + member overrides), payload sensitivity, debounce, quiet hours.
- [x] Realtime front-change stream over Server-Sent Events, for home automation and live UI
- [x] Home Assistant integration ([sheaf-ha](https://github.com/sheaf-project/sheaf-ha)) - surfaces who's fronting as entities and can drive the front from HA, over the front stream or webhooks
- [x] Journals/notes (per-member, encrypted at rest)
- [x] PluralKit one-shot import (file or live API via `pk;token`)
- [ ] PluralKit bidirectional sync
- [x] Importers for Tupperbox, PluralSpace, Prism, and Ampersand
- [x] PluralPort v0.1 import and export, with lossless round-trip and foreign-extension preservation ([docs/PLURALPORT.md](docs/PLURALPORT.md))
- [x] Member and group relationships with a system graph
- [x] Archived members
- [x] Subgroups (nested groups) with a drag-to-reparent tree
- [x] User-opt-in front-history retention
- [x] Standalone front-history export (CSV / JSON / ICS)
- [x] Account activity log
- [x] Global display-timezone preference
- [ ] Friend/trust system (cross-system visibility controls)
- [x] Public profiles and share links - curated views plus revocable/rotatable grants, off unless the operator sets `PUBLIC_PROFILES_ENABLED`
- [ ] Per-field-per-member privacy overrides
- [x] Storage quotas (per-tier account-wide budget)
- [x] Orphaned file cleanup (images uploaded but never attached to a member/system)
- [x] API keys with granular scopes (for scripts and integrations)
- [x] Admin UI (user management, maintenance operations)
- [x] Security-event log with admin IP / CIDR lookup and credential-stuffing view
- [x] Signed image URLs with S3 presign support (hotlink protection)
- [ ] Custom-defined user tiers by server admin instead of placeholder free/plus/selfhosted
- [x] Android+iOS apps, with Wear OS and watchOS companions, live on the Play Store and App Store
- [x] Prometheus-compatible `/metrics` endpoint ([docs/METRICS.md](docs/METRICS.md))
- [x] All-in-one Docker image with automatic HTTPS and a Cloudflare Tunnel option
- [x] Multi-replica deployments via Postgres advisory-lock leader election
- [ ] Terraform module for cloud deployment
- [ ] More 2FA methods - WebAuthn/YubiKey, email OTP as a "better than nothing" fallback
- [ ] Alternate secrets management methods - AWS Secrets Manager, Vault, others?
- [ ] Accessibility improvements - image alt text support, additional TBD

## License

[AGPL-3.0-or-later](LICENSE)

This means: you can self-host, modify, and run Sheaf however you want. If you run a modified version as a public service, you must share your modifications under the same license.
