# Sheaf

[![GitHub Release](https://img.shields.io/github/v/release/sheaf-project/sheaf?include_prereleases&sort=semver&display_name=release&style=plastic&link=https%3A%2F%2Fgithub.com%2Fsheaf-project%2Fsheaf%2Freleases%2F)](https://github.com/sheaf-project/sheaf/releases)
[![Discord](https://img.shields.io/discord/1483687251492868217?style=plastic&logo=discord&label=Discord&link=https%3A%2F%2Fdiscord.com%2Finvite%2FWFaKQPzFx8)](https://discord.com/invite/WFaKQPzFx8)
[![GitHub Actions Workflow Status](https://img.shields.io/github/actions/workflow/status/sheaf-project/sheaf/ci.yml?branch=main&style=plastic&logo=github&label=CI)](https://github.com/sheaf-project/sheaf/actions)

![transrights](https://pride-badges.pony.workers.dev/static/v1?label=trans%20rights&stripeWidth=6&stripeColors=5BCEFA,F5A9B8,FFFFFF,F5A9B8,5BCEFA)
![enbyware](https://pride-badges.pony.workers.dev/static/v1?label=enbyware&labelColor=%23555&stripeWidth=8&stripeColors=FCF434%2CFFFFFF%2C9C59D1%2C2C2C2C)
![pluralmade](https://pride-badges.pony.workers.dev/static/v1?label=plural+made&labelColor=%23555&stripeWidth=8&stripeColors=2e0525%2C553578%2C7675c3%2C89c7b0%2Cf4ecbd)

> *noun*: a bundle; in mathematics, a structure that describes how local pieces fit together into a coherent whole.

Open-source plural system tracking. A self-hostable replacement for SimplyPlural, built with data security and sustainability in mind.

> **Status:** selfhostable; hosted at [app.sheaf.sh](https://app.sheaf.sh), free with open signups. [test.sheaf.sh](https://test.sheaf.sh) remains a public sandbox where data may be wiped at any time. Feedback welcome via [issues](https://github.com/sheaf-project/sheaf/issues) or our [Discord](https://sheaf.sh/discord).

[Android/WearOS](https://github.com/sheaf-project/android) is on the [Play Store](https://play.google.com/store/apps/details?id=systems.lupine.sheaf) (or sideload the APK from [Releases](https://github.com/sheaf-project/android/releases)), and [iOS/WatchOS](https://github.com/sheaf-project/ios) is on the [App Store](https://apps.apple.com/us/app/sheaf-plural-system-tracker/id6766770364). Both ship a watch companion app and complication.

Sheaf supports the [OpenPlural](https://github.com/skylartaylor/openplural) data standard proposal as a founding project, and ships import and export for v0.1 today.

## Why

SimplyPlural is shutting down. Many alternatives are either incomplete, closed-source, local-only, or lack credible infrastructure foundations. Sheaf is built by people who are actually paid to run things at scale, with a focus on:

- **Data security** — Email and TOTP secrets are encrypted at rest (application-level). All data is GDPR Article 9 special category data and is treated accordingly.
- **Self-hosting first** — `docker compose up` and you have your own instance
- **Sustainable economics** — Designed from day 1 to support both selfhosting and an optional hosted tier without forking the codebase or using proprietary extensions
- **Contributor accessibility** — Python/FastAPI backend, React frontend.

## Features

- **Web, mobile, and wearable apps** - Sheaf also supports first-class API support for custom clients and integrations, and development of custom or alternative clients for the Sheaf API is encouraged.
- **Members** — Profiles with name, pronouns, description, colour, birthday, avatar, emoji, privacy levels, optional PluralKit ID
- **Custom fronts** — Mark non-counting fronting entities like "Asleep" or "Away" so they show up in the fronter list without inflating member counts
- **Front tracking** — Log switches with cofronters and an optional encrypted free-text status per fronting period
- **Analytics** — Per-member front time, percent of window, session count, longest session, and hour-of-day distribution over a configurable window (7d / 30d / 90d / 1 year). Co-fronting double-counts so individual member stats are accurate.
- **Reminders** — Schedule daily/weekly/monthly pings or fire reminders X minutes after a member fronts. Member-scoped reminders queue while nobody on the list is fronting and drain as a digest when one next switches in. Delivery rides your existing notification channels.
- **Polls** — Run a vote across the system. Each vote is attributed to a specific member who must be in the current front, with a full audit log of cast / change / withdraw events plus a fronting snapshot. Single or multi-choice, results live or hidden until close, hard deadline at creation with auto-purge after retention.
- **Notes** — Lightweight scratchpad per member and per system. Markdown, encrypted at rest, intentionally without revision history or System Safety protection - for "trigger list / fav drink / current med doses" quick reference where journals' versioning is overkill.
- **Messages** — Global system message board plus a per-member wall, so headmates can leave each other notes inside the system. Replies chain (no nested threads), edits keep revision history, deletes are soft and gated by System Safety. Per-member unread counts power the sidebar badge and an opt-in "you have N unread" prompt when you start fronting.
- **Groups** — Organize members into groups, nested up to 8 levels (subsystems), with a drag-to-reparent tree and subtree-inclusive group filtering
- **Relationships** - Typed relationships between members and between subsystems (partner, parent/child, protector, or your own types), with symmetric / directional / either direction modes, mapped as a self-arranging system graph you can pan, zoom, and build on directly
- **Archived members** - Soft-hide a member from lists, pickers, and the front switcher without losing their name anywhere it already appears in history
- **Tags** — Flexible member tagging
- **Custom fields** — Define your own fields (text, number, date, boolean, select) with per-field privacy
- **Journals** — Per-member or system-wide markdown journal entries with edit history
- **Revision history** — Member bios and journal entries are versioned, with tier-aware retention caps
- **Revision pinning** — Pin specific revisions to protect them from automatic trim, with optional re-auth + grace on unpin
- **Front-history retention** - Opt-in per-system window that ages out old closed fronts. A privacy control rather than a tier limit: off by default, with a fixed 14-day import grace so a freshly restored archive is never abruptly deleted, and tightening it takes a deferred, re-auth-gated countdown.
- **System Safety** — Optional grace period and re-auth (password / TOTP) on destructive actions (member/journal/group/etc deletion, revision unpin)
- **Realtime front stream** - `GET /v1/fronts/stream` pushes your front changes over Server-Sent Events instead of making you poll, aimed at home automation (Home Assistant, Node-RED) and live UI updates. The client dials out and holds the connection open, so a LAN-only consumer works with no inbound reachability at all. There is an official [Home Assistant integration](https://github.com/sheaf-project/sheaf-ha) built on it.
- **Imports** — SimplyPlural, PluralKit (export file or live via your `pk;token`), Tupperbox, PluralSpace, Prism, Ampersand, OpenPlural, and Sheaf's own exports. Granular control over what to bring across; PK switch log is converted to Sheaf front intervals, and the preview tells you what will be deduplicated, shortened, or capped before you commit to it. See **[docs/IMPORT.md](docs/IMPORT.md)** for the full migration guide.
- **OpenPlural** - Import and export for [OpenPlural](https://github.com/skylartaylor/openplural) v0.1, as either a single JSON document or an `.openplural.zip` bundle carrying image bytes. Sheaf data the draft spec doesn't model yet rides in a namespaced extensions key so a round-trip is lossless, and other apps' unmodellable data is preserved on import and re-emitted on the next export rather than dropped. See **[docs/OPENPLURAL.md](docs/OPENPLURAL.md)**.
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
- **Eye-friendly** — Default dark, with Dark Reader compatibility and a clear light toggle

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
├── docs/                   # Self-hosting and client development guides
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
| `GET /v1/relationship-types` | Relationship types (partner, parent/child, your own) |
| `POST /v1/member-relationships` | Relate two members |
| `POST /v1/group-relationships` | Relate two groups (subsystems) |
| `GET /v1/relationships/graph` | Whole-system relationship graph |
| `GET/POST /v1/tags` | Tags |
| `GET/POST /v1/fields` | Custom field definitions |
| `PUT /v1/members/{id}/fields` | Set custom field values |
| `GET/POST /v1/journals` | List/create journal entries |
| `GET /v1/journals/{id}/revisions` | Edit history for an entry |
| `POST /v1/journals/{id}/pin-revision` | Pin a revision (exempt from trim) |
| `POST /v1/journals/{id}/unpin-revision` | Unpin (immediate or queued behind grace) |
| `GET/PATCH /v1/system/safety` | System Safety settings + pending actions |
| `POST /v1/imports/file` | Import an export file (`source=` picks the format) |
| `POST /v1/imports/api` | Import a PluralKit system live via `pk;token` |
| `GET /v1/imports` | Import job history and status |
| `POST /v1/import/{source}/preview` | Preview a file before committing to it |
| `GET /v1/export` | Export plural system content (sync JSON; `format=openplural` for OpenPlural) |
| `POST /v1/export/jobs` | Queue an async export: full backup with image bytes, an `.openplural.zip` bundle, or front history as CSV / JSON / ICS |
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
- Registration modes (open / approval / invite / closed) and email verification
- Account deletion with configurable grace period
- File storage (filesystem / S3) with hotlink protection
- Storage quotas and upload limits
- Revision-history retention caps and pinned-revision tier knobs
- System Safety (destructive-action grace, re-auth, per-category toggles)
- Frontend build and serving
- Reverse proxy setup (nginx, Caddy) and the `SHEAF_BASE_URL` / cookie-Secure relationship
- Rate limiting, per-tier limits, and trusted proxies
- Delivering webhooks and ntfy to your own LAN (`WEBHOOK_ALLOWED_PRIVATE_CIDRS`), and why it is off by default
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
- [x] OpenPlural v0.1 import and export, with lossless round-trip and foreign-extension preservation ([docs/OPENPLURAL.md](docs/OPENPLURAL.md))
- [x] Member and group relationships with a system graph
- [x] Archived members
- [x] Subgroups (nested groups) with a drag-to-reparent tree
- [x] User-opt-in front-history retention
- [x] Standalone front-history export (CSV / JSON / ICS)
- [x] Account activity log
- [x] Global display-timezone preference
- [ ] Friend/trust system (cross-system visibility controls)
- [ ] Public profiles and share links - curated views plus revocable/rotatable grants (landed behind `PUBLIC_PROFILES_ENABLED`, not yet in a tagged release)
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
