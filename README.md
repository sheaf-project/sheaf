# Sheaf

[![GitHub Release](https://img.shields.io/github/v/release/sheaf-project/sheaf?include_prereleases&sort=semver&display_name=release&style=plastic&link=https%3A%2F%2Fgithub.com%2Fsheaf-project%2Fsheaf%2Freleases%2F)](https://github.com/sheaf-project/sheaf/releases)
[![Discord](https://img.shields.io/discord/1483687251492868217?style=plastic&logo=discord&label=Discord&link=https%3A%2F%2Fdiscord.com%2Finvite%2FWFaKQPzFx8)](https://discord.com/invite/WFaKQPzFx8)
[![GitHub Actions Workflow Status](https://img.shields.io/github/actions/workflow/status/sheaf-project/sheaf/ci.yml?branch=main&style=plastic&logo=github&label=CI)](https://github.com/sheaf-project/sheaf/actions)

![transrights](https://pride-badges.pony.workers.dev/static/v1?label=trans%20rights&stripeWidth=6&stripeColors=5BCEFA,F5A9B8,FFFFFF,F5A9B8,5BCEFA)
![enbyware](https://pride-badges.pony.workers.dev/static/v1?label=enbyware&labelColor=%23555&stripeWidth=8&stripeColors=FCF434%2CFFFFFF%2C9C59D1%2C2C2C2C)
![pluralmade](https://pride-badges.pony.workers.dev/static/v1?label=plural+made&labelColor=%23555&stripeWidth=8&stripeColors=2e0525%2C553578%2C7675c3%2C89c7b0%2Cf4ecbd)

> *noun*: a bundle; in mathematics, a structure that describes how local pieces fit together into a coherent whole.

Open-source plural system tracking. A self-hostable replacement for SimplyPlural, built with data security and sustainability in mind.

> **Status:** selfhostable; hosted app in [open beta](https://test.sheaf.sh). Feedback welcome via [issues](https://github.com/sheaf-project/sheaf/issues) or our [Discord](https://discord.gg/WFaKQPzFx8).

[Android/WearOS](https://github.com/sheaf-project/android) and [iOS/WatchOS](https://github.com/sheaf-project/ios) clients are pending approval; ask on Discord for private test access.

Sheaf supports the [OpenPlural](https://github.com/skylartaylor/openplural) data standard proposal as a founding project, and will be migrating to the format for exports once finalised.

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
- **Groups** — Organize members into groups with nesting (subsystems)
- **Tags** — Flexible member tagging
- **Custom fields** — Define your own fields (text, number, date, boolean, select) with per-field privacy
- **Journals** — Per-member or system-wide markdown journal entries with edit history
- **Revision history** — Member bios and journal entries are versioned, with tier-aware retention caps
- **Revision pinning** — Pin specific revisions to protect them from automatic trim, with optional re-auth + grace on unpin
- **System Safety** — Optional grace period and re-auth (password / TOTP) on destructive actions (member/journal/group/etc deletion, revision unpin)
- **SimplyPlural / PluralKit import** — Import your SP export, your PluralKit export file, or pull live from PluralKit using your `pk;token`. Granular control over what to bring across; PK switch log is converted to Sheaf front intervals. See **[docs/IMPORT.md](docs/IMPORT.md)** for the full migration guide.
- **File storage** — File uploads with filesystem or S3-compatible backends
- **Data export** — sync JSON (Article 20 portability), async zip with image bytes, and a separate Article 15 endpoint covering everything we know about your account
- **2FA** — Optional TOTP with recovery codes
- **API keys** — Scoped, named keys (`sk_…`) for scripts and integrations
- **Admin dashboard** — User management, invite codes, storage audit, background job monitoring, optional step-up auth
- **Registration modes** — Open, approval-required, invite-only, or closed
- **Email verification** — Optional required verification with configurable flow
- **Account deletion** — Self-service with configurable grace period
- **Field-level encryption** — Member names/bios, journal titles/bodies, and revision history encrypted at rest with XChaCha20-Poly1305
- **Eye-friendly** — Default dark, with Dark Reader compatibility and a clear light toggle

## FAQ

See [FAQ.md](FAQ.md)

## Quick Start

```bash
cp .env.example .env
# Edit .env — at minimum, change POSTGRES_PASSWORD and JWT_SECRET_KEY
docker compose up -d
```

The API is available at `http://localhost:8000` with interactive docs at `http://localhost:8000/v1/docs`.

### Generating secrets

```bash
# JWT secret
python -c "import secrets; print(secrets.token_urlsafe(32))"

# Encryption key (optional — auto-generated on first start if not set)
python -c "import secrets; print(secrets.token_hex(32))"
```

> **Important:** Sheaf encrypts sensitive data at rest (emails, TOTP secrets, and all member information). The key is `SHEAF_ENCRYPTION_KEY` — either set it in `.env` (recommended) or let Sheaf auto-generate one on first start, saved to `data/encryption.key` inside the Docker volume. **Either way, this key is a third backup target alongside your database and uploaded files: back it up somewhere safe. If you lose it, all encrypted data is unrecoverable.**

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
| `GET/POST /v1/groups` | Groups |
| `GET/POST /v1/tags` | Tags |
| `GET/POST /v1/fields` | Custom field definitions |
| `PUT /v1/members/{id}/fields` | Set custom field values |
| `GET/POST /v1/journals` | List/create journal entries |
| `GET /v1/journals/{id}/revisions` | Edit history for an entry |
| `POST /v1/journals/{id}/pin-revision` | Pin a revision (exempt from trim) |
| `POST /v1/journals/{id}/unpin-revision` | Unpin (immediate or queued behind grace) |
| `GET/PATCH /v1/system/safety` | System Safety settings + pending actions |
| `POST /v1/import/simplyplural` | Import SP data |
| `POST /v1/import/pluralkit` | Import PK export file |
| `POST /v1/import/pluralkit-api` | Import PK system via token |
| `POST /v1/import/sheaf` | Import Sheaf export |
| `GET /v1/export` | Export plural system content (sync JSON) |
| `POST /v1/export/jobs` | Queue an async backup including image bytes |
| `POST /v1/account/data` | Article 15 — everything we know about your account |
| `POST /v1/files/upload` | Upload avatar |

Full interactive docs: `http://your-instance/v1/docs`

**Building a client?** See **[docs/CLIENT_DESIGN.md](docs/CLIENT_DESIGN.md)** for the complete client development guide — auth flows, scopes, session management, client settings storage, and all endpoints.

## Self-Hosting

```bash
cp .env.example .env
# Edit .env — at minimum, change POSTGRES_PASSWORD and JWT_SECRET_KEY
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
- Rate limiting and trusted proxies
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
- [ ] Named fronts — save a named combination of members and make them searchable in the start front dialog
- [ ] CLI similar to [simplyplural-cli](https://github.com/SiteRelEnby/simplyplural-cli)
- [x] Front-change notifications — web push, mobile push (FCM + APNs), webhook (json/discord/slack/plaintext), ntfy, Pushover. Per-channel filters with three-layer member visibility (base + group rules + member overrides), payload sensitivity, debounce, quiet hours.
- [x] Journals/notes (per-member, encrypted at rest)
- [x] PluralKit one-shot import (file or live API via `pk;token`)
- [ ] PluralKit bidirectional sync
- [ ] Friend/trust system (cross-system visibility controls)
- [ ] Per-field-per-member privacy overrides
- [x] Storage quotas (per-tier account-wide budget)
- [x] Orphaned file cleanup (images uploaded but never attached to a member/system)
- [x] API keys with granular scopes (for scripts and integrations)
- [x] Admin UI (user management, maintenance operations)
- [x] Signed image URLs with S3 presign support (hotlink protection)
- [ ] Custom-defined user tiers by server admin instead of placeholder free/plus/selfhosted
- [ ] Android+iOS apps (in progress — API-first, OpenAPI spec available for client generation)
- [ ] Prometheus-compatible /metrics endpoint
- [ ] Terraform module for cloud deployment
- [ ] More 2FA methods — WebAuthn/YubiKey, email OTP as a "better than nothing" fallback
- [ ] Alternate secrets management methods - AWS Secrets Manager, Vault, others?
- [ ] Accessibility improvements - image alt text support, additional TBD

## License

[AGPL-3.0-or-later](LICENSE)

This means: you can self-host, modify, and run Sheaf however you want. If you run a modified version as a public service, you must share your modifications under the same license.
