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
git clone https://github.com/sheaf-project/sheaf.git
cd sheaf

# Copy env and start infrastructure
cp .env.example .env
docker compose up db redis -d

# Backend
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pip install -e ./sheaf_dev       # optional: dev-only tools (demo wipe, etc.)
alembic upgrade head
uvicorn sheaf.main:app --reload

# Frontend (separate terminal)
cd web
npm install
npm run dev
```

The API runs on `http://localhost:8000` (docs at `/v1/docs`), and the web UI on `http://localhost:5173`.

### Running tests

#### Full test suite (recommended)

Use `run_tests.sh` to spin up a dedicated isolated Docker stack, run tests against every server configuration in sequence, then tear everything down:

```bash
./run_tests.sh
```

This tests four configurations: selfhosted with no admin step-up, selfhosted with password step-up, selfhosted with TOTP step-up, and saas mode. Uses ports 8001/5433/6380 so it doesn't conflict with a running dev stack.

```bash
# Skip rebuilding the image if you haven't changed backend code:
./run_tests.sh --no-build
```

#### Quick run against a local server

Start a server first, then run pytest directly. You need `SHEAF_TEST_DB_URL` so the `admin_client` fixture can promote a test user to admin directly in the DB — the default `DATABASE_URL` uses Docker's internal `db` hostname, which isn't reachable from the host:

```bash
docker compose up db redis -d
uvicorn sheaf.main:app --reload &

export SHEAF_TEST_DB_URL="postgresql+asyncpg://sheaf:<POSTGRES_PASSWORD>@localhost:5432/sheaf"
pytest -v
```

Replace `<POSTGRES_PASSWORD>` with the value from your `.env`.

#### Test fixtures

- `client` — unauthenticated httpx client
- `auth_client` — registers a fresh user per test, sets Bearer token
- `admin_client` — registers a fresh user, promotes to admin directly via DB, completes admin step-up automatically (adapts to whatever `ADMIN_AUTH_LEVEL` the server has configured)
- `raw_admin_client` — same as `admin_client` but skips step-up — use this to test step-up enforcement

Test markers gate config-specific tests: `admin_auth_password`, `admin_auth_totp`, `saas`. The conftest skips them unless the matching server config is active.

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

### Migrations

Create migrations with Alembic. The Docker entrypoint runs `alembic upgrade head` on startup.

```bash
# Generate migration from model changes
alembic revision --autogenerate -m "description"

# Apply migrations
alembic upgrade head
```

When adding enum columns, ensure the migration creates the Postgres enum type with **lowercase values** to match the StrEnum values.

#### Idempotency on long-running feature branches

If your branch lives long enough to be rebased onto new schema changes — or its migration revision id ever needs renumbering to resolve a chain conflict — write the `upgrade()` so it can run on a DB that already has a previous version applied. Use the SQLAlchemy inspector to check before each `add_column` / `create_table`:

```python
bind = op.get_bind()
inspector = sa.inspect(bind)
existing_cols = {c["name"] for c in inspector.get_columns("my_table")}
if "my_new_col" not in existing_cols:
    op.add_column("my_table", sa.Column("my_new_col", ...))
```

Why: dev DBs that ran the original revision id won't know to skip the renumbered one, and the app crashes in a restart loop before you can `alembic stamp` it manually. Production isn't affected, but everyone testing the branch will hit it.

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
4. Ensure tests pass (`./run_tests.sh` for the full suite, or `pytest` against a local server)
5. Open a PR with a clear description of what and why

#### PR guidelines

- Keep PRs focused. One feature or fix per PR.
- Write clear commit messages.
- If your change touches the data model, include an Alembic migration.
- If your change adds an API endpoint, add a test.
- Don't include unrelated formatting changes, refactors, or dependency bumps.

## Architecture notes

Before making significant changes, it helps to understand a few design decisions:

- **User != System.** A user is an auth identity. A system is the plural system profile. They're 1:1 today but separated for future flexibility - do not poke holes in the separation between the two.
- **Self-hosted first.** The codebase supports both self-hosting and a hosted tier without forking. The `SHEAF_MODE` config flag controls which features are active.
- **Dev-only code stays in `sheaf_dev/`.** Destructive tools (database wipes, demo resets) belong in the `sheaf_dev` package, never in `sheaf`. The production Docker image doesn't include it by default — the code physically cannot exist in production. To include dev tools in a Docker build: `INCLUDE_DEV_TOOLS=true docker compose up -d --build`. For local dev: `pip install -e ./sheaf_dev`. The job system loads dev jobs via `try/except ImportError`, so no configuration error can activate code that isn't there.
- **Encryption is application-level.** Email and TOTP secrets are encrypted before storage. Lookups use blind indexes. Don't bypass this.

## Key conventions

- **All IDs are UUIDs.** No auto-increment.
- **Enums use StrEnum with lowercase values.** SQLAlchemy Enum columns must use `values_callable=lambda e: [m.value for m in e]` to match.
- **Encrypted fields** (email, totp_secret) use `crypto.encrypt()`/`crypto.decrypt()`. Lookups use blind indexes (`crypto.blind_index()` — keyed HMAC derived from the encryption key, not plain SHA-256).
- **Auth dependency:** Use `get_current_user` for authenticated endpoints, `get_admin_user` for admin-only (requires `is_admin=True` or `admin:read` scope), `get_admin_write_user` for mutating admin endpoints (`admin:write`), `get_current_user_optional` for public endpoints that optionally use auth.
- **Scope enforcement:** All resource endpoints are gated by `require_scope()` from `sheaf/auth/dependencies.py`. Router-level read deps live in `sheaf/api/v1/router.py`; per-endpoint write/delete deps are on the individual route functions. Session/JWT auth bypasses scope checks (full access). Rules: `resource:write` and `resource:delete` both imply `resource:read`; nothing implies `resource:delete`. When adding a new endpoint, add the appropriate `dependencies=[Depends(require_scope(...))]`.
- **API keys:** Stored as SHA-256 hash only — plaintext (`sk_…`) returned once on creation. Valid scopes are defined in `_ALL_SCOPES` (dependencies.py) and `_VALID_SCOPES` (auth.py) — keep both in sync when adding new scopes. `admin:*` scopes can only be created by users with `is_admin=True`.
- **File URLs:** Store the storage key (e.g. `avatars/{user_id}/{uuid}.png`), never a signed URL. Call `resolve_avatar_url(key)` from `sheaf/files.py` to get the appropriate URL at read time. Schemas use `@field_serializer("avatar_url")` to do this automatically.
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
- **API key plaintext is never stored.** Only the SHA-256 hash is persisted. Return the plaintext once on creation; never log it.
- **Never store signed file URLs.** Store the key; resolve URLs at read time via `resolve_avatar_url()`.

## License

By contributing to Sheaf, you agree that your contributions will be licensed under [AGPL-3.0-or-later](AGPL-3.0.txt).
