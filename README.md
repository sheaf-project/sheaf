# Sheaf

> *noun*: a bundle; in mathematics, a structure that describes how local pieces fit together into a coherent whole.

Open-source plural system tracking. A self-hostable replacement for SimplyPlural, built with data security and sustainability in mind.

## Why

SimplyPlural is shutting down. Many alternatives are either incomplete, closed-source, local-only, or lack credible infrastructure foundations. Sheaf is built by people who are actually paid to run things at scale, with a focus on:

- **Data security** — Email and TOTP secrets are encrypted at rest (application-level). All data is GDPR Article 9 special category data and is treated accordingly.
- **Self-hosting first** — `docker compose up` and you have your own instance
- **Sustainable economics** — Designed from day 1 to support both selfhosting and an optional hosted tier without forking the codebase or using proprietary extensions
- **Contributor accessibility** — Python/FastAPI backend, React frontend.

## Features

- **Members** — Profiles with name, pronouns, description, colour, birthday, avatar, privacy levels
- **Front tracking** — Log switches, including cofronters and custom fronts
- **Groups** — Organize members into groups with nesting (subsystems)
- **Tags** — Flexible member tagging
- **Custom fields** — Define your own fields (text, number, date, boolean, select) with per-field privacy
- **SimplyPlural import** — Import your SP export with granular control (select specific members, toggle front history, etc.)
- **File storage** — File uploads with filesystem or S3-compatible backends
- **Data export**
- **2FA** — Optional TOTP
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

> **Important:** If you let Sheaf auto-generate the encryption key, it's saved to `data/encryption.key` inside the Docker volume. **Back this up.** If you lose it, all encrypted data (emails, TOTP secrets) is unrecoverable.

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
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

**Tech stack:** Python 3.12+, FastAPI, SQLAlchemy 2.0 (async), PostgreSQL 16, Redis, Alembic. Frontend: React 19, TypeScript, Vite, Tailwind CSS v4, shadcn/ui.

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
| `POST /v1/import/simplyplural` | Import SP data |
| `POST /v1/import/sheaf` | Import Sheaf export |
| `GET /v1/export` | Export all data |
| `POST /v1/files/upload` | Upload avatar |

Full interactive docs: `http://your-instance/v1/docs`

## Self-Hosting

```bash
cp .env.example .env
# Edit .env — at minimum, change POSTGRES_PASSWORD and JWT_SECRET_KEY
docker compose up -d
```

See **[docs/SELFHOSTING.md](docs/SELFHOSTING.md)** for the full guide covering:

- Secrets and encryption key management
- Admin access and step-up authentication
- Optional dependencies (S3, SMTP, SES)
- Email configuration (SMTP / AWS SES)
- Registration modes (open / approval / invite / closed) and email verification
- File storage (filesystem / S3) with hotlink protection
- Storage quotas and upload limits
- Reverse proxy setup (nginx, Caddy)
- Backups

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
- [ ] Front change notifications (WebSocket push)
- [ ] Journals/notes (per-member, encrypted at rest)
- [ ] PluralKit bidirectional sync
- [ ] Friend/trust system (cross-system visibility controls)
- [ ] Per-field-per-member privacy overrides
- [x] Storage quotas (per-tier account-wide budget)
- [x] Orphaned file cleanup (images uploaded but never attached to a member/system)
- [x] API keys with granular scopes (for scripts and integrations)
- [x] Admin UI (user management, maintenance operations)
- [x] Signed image URLs with S3 presign support (hotlink protection)
- [ ] Webhooks for switch/fronter notification
- [ ] Custom-defined user tiers by server admin instead of placeholder free/plus/selfhosted
- [ ] Android+iOS apps (API-first — OpenAPI spec available for client generation)
- [ ] Prometheus-compatible /metrics endpoint
- [ ] Terraform module for cloud deployment
- [ ] More 2FA methods — WebAuthn/YubiKey, email OTP as a "better than nothing" fallback

## License

[AGPL-3.0-or-later](LICENSE)

This means: you can self-host, modify, and run Sheaf however you want. If you run a modified version as a public service, you must share your modifications under the same license.
