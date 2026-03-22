# Contributing to Sheaf

Thanks for considering contributing! Sheaf is built for plural systems, but we welcomes contributions from anyone who shares our goals, including singlets.

Please read the [Code of Conduct](CODE_OF_CONDUCT.md) before participating.

## Getting started

### Prerequisites

- Python 3.12+
- Node.js 20+
- Docker and Docker Compose (for PostgreSQL and Redis)

### Setup

```bash
# Clone the repo
git clone https://github.com/SiteRelEnby/sheaf.git
cd sheaf

# Copy env and start infrastructure
cp .env.example .env
docker compose up db redis -d

# Backend
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head
uvicorn sheaf.main:app --reload

# Frontend (separate terminal)
cd web
npm install
npm run dev
```

The API runs on `http://localhost:8000` (docs at `/v1/docs`), and the web UI on `http://localhost:5173`.

### Running tests

Tests are integration tests that hit a running server. Start the server first, then run pytest:

```bash
uvicorn sheaf.main:app --reload &
pytest -v
```

If you're running tests **outside Docker** (e.g. `uvicorn` on your host machine connecting to `docker compose up db redis -d`), you also need `SHEAF_TEST_DB_URL` so the `admin_client` test fixture can directly promote a test user to admin in the DB. The default `DATABASE_URL` uses Docker's internal `db` hostname, which isn't reachable from your host:

```bash
export SHEAF_TEST_DB_URL="postgresql+asyncpg://sheaf:<POSTGRES_PASSWORD>@localhost:5432/sheaf"
pytest -v
```

Replace `<POSTGRES_PASSWORD>` with the value from your `.env`. If you run the full stack inside Docker (`docker compose up -d`), the server manages all DB access internally and `SHEAF_TEST_DB_URL` is not needed — but tests must be run inside the container in that case.

### Linting

```bash
# Backend
ruff check sheaf/

# Frontend
cd web
npm run lint
npx tsc --noEmit
```

Both must pass with zero errors.

## How to contribute

### Reporting bugs

Open an issue. Include:
- What you expected to happen
- What actually happened
- Steps to reproduce
- Your environment (self-hosted or hosted, browser, OS)

### Suggesting features

Open an issue tagged as a feature request. Describe the use case — what are you trying to do and why?

If you're coming from SimplyPlural, we're especially interested in hearing about features you relied on, workflows that worked well, and things you wished were different.

### Submitting code

1. Fork the repo and create a feature branch from `main`
2. Make your changes
3. Ensure all linting passes (`ruff check sheaf/` and `cd web && npm run lint && npx tsc --noEmit`)
4. Ensure tests pass (`pytest`)
5. Open a PR with a clear description of what and why

#### PR guidelines

- Keep PRs focused. One feature or fix per PR.
- Write clear commit messages.
- If your change touches the data model, include an Alembic migration.
- If your change adds an API endpoint, add a test.
- Don't include unrelated formatting changes, refactors, or dependency bumps.

### AI-assisted contributions

We welcome AI-assisted contributions. If you use an AI tool, that's fine — just make sure you understand the code you submit, can explain it in your own words and not your agent's, and are willing to stand behind what you submit. See [AGENTS.md](AGENTS.md) for instructions that AI coding agents can use when working on this codebase.

## Architecture notes

Before making significant changes, it helps to understand a few design decisions:

- **User != System.** A user is an auth identity. A system is the plural system profile. They're 1:1 today but separated for future flexibility - do not poke holes in the separation between the two.
- **Self-hosted first.** The codebase supports both self-hosting and a hosted tier without forking. The `SHEAF_MODE` config flag controls which features are active.
- **Encryption is application-level.** Email and TOTP secrets are encrypted before storage. Lookups use blind indexes. Don't bypass this.

## Key conventions

- **All IDs are UUIDs.** No auto-increment.
- **Enums use StrEnum with lowercase values.** SQLAlchemy Enum columns must use `values_callable=lambda e: [m.value for m in e]` to match.
- **Encrypted fields** (email, totp_secret) use `crypto.encrypt()`/`crypto.decrypt()`. Lookups use blind indexes (`crypto.blind_index()`).
- **Auth dependency:** Use `get_current_user` for authenticated endpoints, `get_admin_user` for admin-only, `get_current_user_optional` for public endpoints that optionally use auth.
- **Database sessions:** `get_db` yields a session and commits on success. For endpoints where the client needs the data immediately after the response (register, login), explicitly `await db.commit()` before returning.
- **API versioning:** All routes under `/v1/`. New versions get a new directory.
- **Frontend API calls:** Use `apiFetch()` from `lib/api-client.ts`. It handles auth headers, token refresh, and error parsing. All fetch calls use `credentials: "same-origin"` for cookie-based auth.
- **Frontend state:** TanStack Query for server state. Custom hooks in `hooks/` wrap query/mutation logic. No Redux or other global state.

## Security requirements

This is not negotiable. Sheaf handles deeply personal identity data.

- **Never log or expose plaintext encrypted fields** (email, TOTP secrets).
- **Never store secrets in code or commit .env files.**
- **Validate all user input.** Pydantic handles request validation; don't bypass it.
- **Check ownership on all mutations.** Every endpoint that modifies data must verify the resource belongs to the authenticated user's system.
- **No path traversal.** File paths must be validated with `resolve()` + `is_relative_to()`.
- **Use parameterised queries only.** SQLAlchemy handles this — don't use raw SQL strings.
- **Refresh tokens are HttpOnly cookies**, not stored in localStorage.

## License

By contributing to Sheaf, you agree that your contributions will be licensed under [AGPL-3.0-or-later](AGPL-3.0.txt).
