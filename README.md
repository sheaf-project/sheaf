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

**Auth:** JWT bearer tokens (15min access + 30d refresh) for API/mobile clients. The `Authorization: Bearer <token>` header is used for all authenticated requests.

Key endpoints:

| Endpoint | Description |
|----------|-------------|
| `POST /v1/auth/register` | Create account |
| `POST /v1/auth/login` | Login, get tokens |
| `GET /v1/systems/me` | Your system profile |
| `GET/POST /v1/members` | List/create members |
| `GET/POST /v1/fronts` | Front history |
| `GET /v1/fronts/current` | Who's fronting now |
| `GET/POST /v1/groups` | Groups |
| `GET/POST /v1/tags` | Tags |
| `GET/POST /v1/fields` | Custom field definitions |
| `PUT /v1/members/{id}/fields` | Set custom field values |
| `POST /v1/import/simplyplural` | Import SP data |
| `GET /v1/export` | Export all data |
| `POST /v1/files/upload` | Upload avatar |

Full interactive docs: `http://your-instance/v1/docs`

## Self-Hosting

### Requirements
- Docker and Docker Compose
- ~512MB RAM minimum

### File Storage

Avatars can be stored locally (default) or on any S3-compatible service (AWS S3, MinIO, Cloudflare R2, BackBlaze B2, etc)

```env
# Local filesystem (default)
STORAGE_BACKEND=filesystem
STORAGE_PATH=data/files

# S3-compatible
STORAGE_BACKEND=s3
S3_BUCKET=sheaf-files
S3_ACCESS_KEY=...
S3_SECRET_KEY=...
S3_REGION=us-east-1
S3_ENDPOINT=https://your-minio.example.com  # For MinIO/R2
S3_PUBLIC_URL=https://cdn.example.com        # Optional CDN prefix
```

### Reverse Proxy

Sheaf should sit behind nginx, Caddy, or similar for TLS termination. There is no builtin TLS support - proxy to localhost:8000 after TLS termination.

## Development

```bash
# Backend
pip install -e ".[dev]"
docker compose up db redis -d
alembic upgrade head
uvicorn sheaf.main:app --reload

# Frontend
cd web && npm install && npm run dev

# Tests
pytest
```

## Roadmap
- [ ] CLI similar to [simplyplural-cli](https://github.com/SiteRelEnby/simplyplural-cli)
- [ ] Front change notifications (WebSocket push)
- [ ] Journals/notes (per-member, encrypted at rest)
- [ ] PluralKit bidirectional sync
- [ ] Friend/trust system (cross-system visibility controls)
- [ ] Per-field-per-member privacy overrides
- [ ] Storage quotas (account-wide budget)
- [ ] Android+iOS apps (API-first — OpenAPI spec available for client generation)
- [ ] Terraform module for cloud deployment

## License

[AGPL-3.0-or-later](AGPL-3.0.txt)

This means: you can self-host, modify, and run Sheaf however you want. If you run a modified version as a public service, you must share your modifications under the same license.
