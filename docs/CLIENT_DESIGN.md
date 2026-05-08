# Client Development Guide

This document covers everything needed to build a client for Sheaf — mobile app, CLI tool, bot, or custom web UI.

Interactive API docs are available at `/v1/docs` on any Sheaf instance.

## Authentication

Three auth methods are supported, checked in this order:

### 1. API Keys (recommended for scripts/integrations)

Prefix: `sk_`. Created in Settings > API Keys with granular scopes.

```
Authorization: Bearer sk_abc123...
```

- Scoped to specific resources (see [Scopes](#scopes) below)
- Plaintext returned once on creation — store it securely
- Server stores only the SHA-256 hash
- Optional expiry date

### 2. JWT Bearer Tokens (recommended for interactive clients)

Used by the web UI and mobile apps. Login returns an access + refresh token pair.

```
POST /v1/auth/login
{ "email": "user@example.com", "password": "...", "totp_code": "123456" }

→ { "access_token": "eyJ...", "refresh_token": "eyJ...", "token_type": "bearer" }
```

- Access token: 15 minutes (default)
- Refresh token: 30 days (default)
- Both tokens are bound to a session (`sid` claim in the JWT)
- Revoking a session invalidates all tokens issued for it

**Token refresh:**

```
POST /v1/auth/refresh
{ "refresh_token": "eyJ..." }

→ { "access_token": "eyJ...", "refresh_token": "eyJ...", "token_type": "bearer" }
```

Browsers can omit the body — the refresh token is also set as an HttpOnly cookie (`sheaf_refresh`, path `/v1/auth`).

**2FA flow:** If the user has TOTP enabled and you don't send `totp_code`, login returns:

```
HTTP 401
X-Sheaf-2FA: required
{ "detail": "TOTP code required" }
```

Prompt for the code and retry with `totp_code` included. Recovery codes (8-char alphanumeric) also work in the `totp_code` field.

### 3. Session Cookies (browser-only)

Set automatically on login/register. HttpOnly, Secure, SameSite=Lax. Clients other than browsers should use JWT or API keys.

## Email-Based Flows

Several flows send emails with links back to `{SHEAF_BASE_URL}`. These links point to **frontend routes** (the web UI), not API endpoints. The frontend then calls the appropriate API endpoint.

### Password Reset

**Step 1 — Request reset email (unauthenticated):**

```
POST /v1/auth/request-password-reset
{ "email": "user@example.com" }

→ { "requested": true }
```

Always returns success (even if the email doesn't exist) to prevent user enumeration. Rate-limited to one request per 15 minutes per email.

**Step 2 — Reset with token (unauthenticated):**

```
POST /v1/auth/reset-password
{ "token": "mWEs3VjxYs...", "new_password": "new-secure-password" }

→ { "detail": "Password has been reset" }
```

The email links to `{SHEAF_BASE_URL}/reset-password?token=...`. Tokens are single-use and expire after 1 hour.

The web UI also supports manual token entry — if the user navigates to `/reset-password` without a `?token=` parameter, they see a text field to paste the token from the email. This is useful for mobile apps: send the user to the "forgot password" flow, then show a "paste your reset token" field in the app instead of needing deep links.

### Email Verification

If the server has `email_verification` set to `"required"`, new accounts must verify their email before accessing resources.

**Check if verification is required:**

The `GET /v1/auth/me` response includes `email_verified`. If `false` and the server requires verification, the client should prompt the user to check their email.

**Verify (unauthenticated — user clicks link in email):**

```
GET /v1/auth/verify-email?token=...

→ { "verified": true }
```

Note: this is a GET, so email links work directly. The email links to `{SHEAF_BASE_URL}/verify-email?token=...`.

**Resend verification (authenticated):**

```
POST /v1/auth/resend-verification

→ { "sent": true }
```

Rate-limited to one request per 20 minutes.

### Registration

```
POST /v1/auth/register
{ "email": "user@example.com", "password": "..." }

→ { "access_token": "eyJ...", "refresh_token": "eyJ...", "token_type": "bearer" }
```

Check `GET /v1/auth/config` first to determine:
- `registration_mode`: `"open"`, `"approval"`, `"invite"`, or `"closed"`
- `invite_codes_enabled`: if `true`, include `"invite_code": "..."` in the register body
- `email_verification`: `"off"` or `"required"`
- `email_enabled`: whether the server can send emails
- `base_url`: the instance's base URL (e.g. `"https://sheaf.example.com"`) — use this for constructing web links (password reset, email verification). `null` if not configured.

If registration mode is `"approval"`, the account is created but inactive — the user sees a "pending approval" state until an admin approves them. If `"invite"`, an invite code is required.

### Mobile app considerations for email links

Email links go to web URLs (`{SHEAF_BASE_URL}/reset-password?token=...`, etc.). Mobile clients have three options:

1. **In-app browser (recommended to start):** Open the relevant web page in a Custom Tab (Android) or SFSafariViewController (iOS). The entire flow happens in the embedded browser. No server changes needed, works with any instance.

2. **Native form:** Build your own screens that call the API endpoints directly. For password reset, the user either taps the email link (which you intercept via deep linking) or copies the token manually. For email verification, the link is a simple GET — tapping it in any browser works.

3. **Deep links (polish):** Register your app to handle `{instance_domain}/reset-password` and `/verify-email` URLs via Android App Links / iOS Universal Links. Requires the instance to serve `.well-known/assetlinks.json` (Android) or `apple-app-site-association` (iOS) — more complex for self-hosted instances with arbitrary domains.

### Account Deletion

Users can request account deletion with a configurable grace period (default 14 days). During the grace period, the user can cancel.

**Request deletion (authenticated):**

```
POST /v1/auth/delete-account
{ "password": "...", "totp_code": "123456" }

→ { "deletion_scheduled_for": "2026-04-12T...", "grace_days": 14 }
```

- Requires password confirmation
- Requires TOTP code if 2FA is enabled (same `X-Sheaf-2FA: required` pattern as login)
- Returns 400 if already pending deletion

**Cancel deletion (authenticated):**

```
POST /v1/auth/cancel-deletion

→ { "cancelled": true }
```

Returns 400 if no pending deletion.

**Check deletion status:**

`GET /v1/auth/me` includes `deletion_requested_at` (ISO 8601 string or `null`) and `account_status` (`"active"`, `"pending_deletion"`, etc.). Use these to show a warning banner and the scheduled deletion date:

```
deletion_date = deletion_requested_at + grace_days
```

The grace period length comes from the server config and is returned in the `delete-account` response. Clients can show it as `deletion_requested_at + grace_days` or just display the `deletion_scheduled_for` value from the initial response.

**What happens during the grace period:**
- The user can still log in and use the system normally
- Reminder emails are sent at 10, 7, and 1 day(s) before deletion (if email is enabled)
- The user can cancel at any time via `POST /v1/auth/cancel-deletion`
- After the grace period expires, a background job permanently deletes the account and all associated data (system, members, fronts, groups, tags, files, sessions, API keys)

## Scopes

API key scopes control access. Session/JWT auth has full access (no scope restrictions).

| Scope | Grants |
|-------|--------|
| `system:read` | Read system profile |
| `system:write` | Update system profile (implies read) |
| `members:read` | List/get members |
| `members:write` | Create/update members (implies read) |
| `members:delete` | Delete members (does NOT imply read/write) |
| `fronts:read/write/delete` | Same pattern as members |
| `groups:read/write/delete` | Same pattern |
| `tags:read/write/delete` | Same pattern |
| `fields:read/write/delete` | Custom fields, same pattern |
| `export:read` | Export data |
| `admin:read` | Read admin endpoints (requires `is_admin`) |
| `admin:write` | Write admin endpoints (requires `is_admin`) |

**Key rules:**
- `write` implies `read` — having `members:write` satisfies `members:read`
- `delete` is explicit — `members:write` does NOT grant `members:delete`
- `admin:*` scopes can only be created by admin users

## Session Management

Sessions track metadata: IP address, user agent, timestamps, client name.

**Endpoints:**
- `GET /v1/auth/sessions` — list all sessions (includes `is_current` flag)
- `PATCH /v1/auth/sessions/{id}` — rename a session (`{ "nickname": "..." }`)
- `DELETE /v1/auth/sessions/{id}` — revoke a session (cannot revoke current)
- `POST /v1/auth/sessions/revoke-others` — revoke all except current

Revoking a session immediately invalidates all JWT tokens bound to it.

### X-Sheaf-Client Header

Set this header on all requests to identify your client in the session list:

```
X-Sheaf-Client: Sheaf Android/1.2.0
X-Sheaf-Client: My Custom App/0.5
```

If not set, the server falls back to parsing the User-Agent (Firefox, Chrome, Safari, Edge, or "Unknown").

## Client Settings Storage

Per-client JSON blob storage — lets your client persist preferences server-side without needing a schema per setting.

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v1/settings/client/{client_id}` | Retrieve settings (404 if none) |
| PUT | `/v1/settings/client/{client_id}` | Store/overwrite settings |
| DELETE | `/v1/settings/client/{client_id}` | Delete settings |

- `client_id`: your app's identifier, max 64 chars (e.g. `"sheaf-android"`, `"my-cli"`)
- Payload: arbitrary JSON, max 16 KB
- One blob per user per client_id — no cross-client access

```
PUT /v1/settings/client/my-app
{ "settings": { "theme": "dark", "columns": ["name", "pronouns"] } }
```

Use this for preferences that should sync across devices running the same client. Device-specific settings (e.g. UI scale) are better stored locally.

## API Endpoints

### Auth (`/v1/auth`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/auth/config` | Public — registration mode, email settings |
| POST | `/auth/register` | Create account (returns tokens) |
| POST | `/auth/login` | Login (returns tokens) |
| POST | `/auth/logout` | Logout (clears session) |
| POST | `/auth/refresh` | Refresh access token |
| GET | `/auth/me` | Current user profile |
| POST | `/auth/totp/setup` | Start TOTP setup (returns QR/secret) |
| POST | `/auth/totp/verify` | Verify code to enable TOTP |
| POST | `/auth/totp/disable` | Disable TOTP (requires password + code) |
| POST | `/auth/totp/regenerate-recovery-codes` | New recovery codes (requires TOTP code) |
| GET | `/auth/keys` | List API keys |
| POST | `/auth/keys` | Create API key |
| DELETE | `/auth/keys/{id}` | Revoke API key |
| POST | `/auth/request-password-reset` | Request reset email |
| POST | `/auth/reset-password` | Reset with token |
| POST | `/auth/delete-account` | Request account deletion (password + TOTP) |
| POST | `/auth/cancel-deletion` | Cancel pending deletion |

### Resources

All resource endpoints require authentication. With API keys, the appropriate scope is required.

| Method | Path | Scope |
|--------|------|-------|
| GET | `/systems/me` | `system:read` |
| PATCH | `/systems/me` | `system:write` |
| GET | `/members` | `members:read` |
| POST | `/members` | `members:write` |
| GET | `/members/{id}` | `members:read` |
| PATCH | `/members/{id}` | `members:write` |
| DELETE | `/members/{id}` | `members:delete` |
| GET | `/fronts` | `fronts:read` |
| POST | `/fronts` | `fronts:write` |
| GET | `/fronts/current` | `fronts:read` |
| PATCH | `/fronts/{id}` | `fronts:write` |
| DELETE | `/fronts/{id}` | `fronts:delete` |
| GET | `/groups` | `groups:read` |
| POST | `/groups` | `groups:write` |
| PATCH | `/groups/{id}` | `groups:write` |
| DELETE | `/groups/{id}` | `groups:delete` |
| GET | `/tags` | `tags:read` |
| POST | `/tags` | `tags:write` |
| PATCH | `/tags/{id}` | `tags:write` |
| DELETE | `/tags/{id}` | `tags:delete` |
| GET | `/tags/{id}/members` | `tags:read` |
| PUT | `/tags/{id}/members` | `tags:write` |
| GET | `/members/{id}/tags` | `tags:read` |
| PUT | `/members/{id}/tags` | `tags:write` |
| GET | `/fields` | `fields:read` |
| POST | `/fields` | `fields:write` |
| PATCH | `/fields/{id}` | `fields:write` |
| DELETE | `/fields/{id}` | `fields:delete` |
| PUT | `/members/{id}/fields/{field_id}` | `members:write` |
| GET | `/export` | `export:read` |

### Files

| Method | Path | Description |
|--------|------|-------------|
| POST | `/files/upload?purpose=avatar\|bio` | Upload image (requires `members:write`) |
| GET | `/files/usage` | Storage usage and quota |
| GET | `/files/list` | List uploaded files |
| DELETE | `/files/{id}` | Delete file (requires `members:write`) |
| GET | `/files/{path}` | Serve file (signed or unsigned) |

Allowed types: `image/jpeg`, `image/png`, `image/gif`, `image/webp`. Max size: 5 MB (default, configurable).

Upload returns `{ "url": "...", "key": "...", "size": 12345 }`. Store the `key`; use `url` for immediate display.

Uploads can be disabled server-wide (`ALLOW_IMAGE_UPLOADS=false`). When disabled, `POST /files/upload` returns 403 for regular users; admins and users with `can_upload_images=true` are unaffected. `GET /auth/me` returns `uploads_allowed: bool` — the effective permission for the current user. Hide upload UI when it is false and fall back to external-URL input where available.

### Reminders

| Method | Path | Description |
|--------|------|-------------|
| GET | `/reminders` | List all reminders for the caller's system |
| POST | `/reminders` | Create a reminder. Gated by `notifications:write`. |
| GET | `/reminders/{id}` | Read |
| PATCH | `/reminders/{id}` | Update. Gated by `notifications:write`. |
| DELETE | `/reminders/{id}` | Delete. Gated by `notifications:write`. |
| GET | `/reminders/{id}/next-fire` | Compute next scheduled fire time (null for automated reminders) |
| GET | `/channels` | Flat list of notification channels for the system, used when picking a destination for a reminder. |

Reminders ride a notification channel for delivery (`channel_id`). Two trigger types: `automated` (delay_seconds after a front-change matching `trigger_member_id`/`trigger_event`) and `repeated` (cron-style schedule via either structured `schedule_kind`/`schedule_time`/`schedule_dow_mask`/`schedule_dom`/`schedule_tz` fields, or a raw `cron_expression`).

Repeated reminders can be scope-limited to specific members. When the schedule fires while no scoped member is fronting and `digest_when_absent=true`, the missed firing queues (capped at 5 per reminder, oldest dropped). On the next front-start of a scoped member, the queue drains as a digest notification. Title and body are encrypted at rest.

### Notes

A scratchpad text field on each member and on the system itself. Distinct from journals: no revisions, no System Safety integration, no destructive-auth on edit. The scratchpad use case ("trigger list / fav drink / current med doses") wanted journals' machinery to be off, not on. Use journals for anything you want versioned or protected.

No new endpoints — `note: string | null` is part of `MemberCreate` / `MemberUpdate` / `MemberRead` (under `members:write`) and `SystemUpdate` / `SystemRead` (under `system:write`). Hard cap of 5000 plaintext characters. Empty string in a PATCH clears the column. Markdown is rendered with image embeds disabled. Encrypted at rest; decrypted plaintext appears in the Article 20 export.

### Polls

| Method | Path | Description |
|--------|------|-------------|
| GET | `/polls` | List polls for the caller's system. |
| POST | `/polls` | Create a poll. Gated by `polls:write`. |
| GET | `/polls/{id}` | Read a poll. Tally and per-member votes are present iff results are visible (live, or end_only after close). |
| DELETE | `/polls/{id}` | Delete. Gated by `polls:delete`. Goes through System Safety when the polls category is enabled, queueing a pending action. |
| POST | `/polls/{id}/votes` | Cast or change a vote. Body: `voted_as_member_id`, `option_ids`. The voted-as member must be in the current front. |
| DELETE | `/polls/{id}/votes/{voted_as_member_id}` | Withdraw a vote. Same fronting check applies. |
| GET | `/polls/{id}/audit` | Audit log of every cast / change / withdraw, including the fronting snapshot at vote time. |
| GET | `/polls/server-config` | Per-user effective tier limits: `min_close_seconds`, `max_close_seconds`, `default_retention_days`, `max_retention_days`, `max_concurrent_open_polls`. `0` on any max means unlimited. The frontend uses this to clamp the create form. |

Polls have a creation-time `closes_at` that cannot be moved (manual close would be abusable without member-level auth). The close window, the per-poll `retention_days`, and the count of concurrent open polls per system are all tier-scaled. The frontend pulls `GET /polls/server-config` to clamp inputs and surface the relevant upsell when a free user hits a limit. After `closes_at`, the poll is read-only; after `retention_days` past close, the cleanup job hard-deletes the poll plus its options, votes, and audit log together. `kind` is `single_choice` or `multi_choice`; `results_visibility` is `live` or `end_only` (both visible-once, locked at creation). `include_custom_fronts` defaults false: members marked `is_custom_front=true` may represent system states (Asleep, Away) rather than voters and are blocked from casting unless this flag is set. Question, description, and option text are encrypted at rest.

### Analytics

| Method | Path | Description |
|--------|------|-------------|
| GET | `/analytics/fronting?since=&until=&tz=` | Per-member fronting summary over a window. Defaults: `until=now`, `since=until-30d`, `tz=UTC`. Capped at 5-year windows. Gated by `fronts:read`. |

The response includes `total_seconds`, `percent_of_window`, `session_count`, `longest_session_seconds`, and `hour_of_day_seconds` (24 buckets indexed 0-23 in the supplied timezone) for every member in the system. Members with no fronting time are returned with zeros so clients can list them without a separate query.

Co-fronting double-counts: if Alice and Bob co-front for an hour, both accrue +3600s individually. Custom fronts are present in the response with `is_custom_front: true`; clients should filter them out of headcount-style charts.

### Import/Export

For end-user-facing import documentation (covering how data shapes
differ between SimplyPlural / PluralKit / Sheaf and what gets mapped
where), see **[IMPORT.md](IMPORT.md)**.

| Method | Path | Description |
|--------|------|-------------|
| POST | `/import/simplyplural/preview` | Preview SP import |
| POST | `/import/simplyplural` | Run SP import |
| POST | `/import/pluralkit/preview` | Preview PK import (file upload) |
| POST | `/import/pluralkit` | Run PK import (file upload) |
| POST | `/import/pluralkit-api/preview` | Preview PK import (live API; body `{token}`) |
| POST | `/import/pluralkit-api` | Run PK import (live API; body `{token, options}`) |
| POST | `/import/sheaf/preview` | Preview Sheaf import |
| POST | `/import/sheaf` | Run Sheaf import |
| GET | `/export` | Export plural system content as JSON (sync, Article 20) |
| POST | `/export/jobs` | Queue async export with image bytes (zip) |
| GET | `/export/jobs` | List your async export jobs |
| GET | `/export/jobs/{id}` | Job status |
| GET | `/export/jobs/{id}/download` | Download the zip when done |
| POST | `/account/data` | Account data (Article 15 right of access) |

The two POST endpoints (`/export/jobs` and `/account/data`) require step-up auth in the request body (`{password, totp_code}`) and refuse API-key authentication. They're the highest-value reads for a hijacked session, so the gate applies regardless of the system's `delete_confirmation` setting.

## Limits

| Limit | Default |
|-------|---------|
| Access token expiry | 15 minutes |
| Refresh token expiry | 30 days |
| Session expiry | 24 hours |
| Max upload size | 5 MB |
| Client settings payload | 16 KB |
| Import file size | 100 MB |
| Password length | 8–128 characters |
| Password reset rate limit | 15 minutes |
| Signed URL expiry window | 1 hour |

## Error Responses

All errors return:

```json
{ "detail": "error message" }
```

| Status | Meaning |
|--------|---------|
| 400 | Invalid input |
| 401 | Not authenticated or invalid credentials |
| 403 | Insufficient permissions / missing scope |
| 404 | Resource not found |
| 409 | Conflict (e.g. email already registered) |
| 413 | Payload too large (file upload, settings blob) |
| 422 | Validation error |
| 429 | Rate limited |

## Security Notes

- All IDs are UUIDs
- Emails and TOTP secrets are encrypted at rest (XChaCha20-Poly1305)
- All mutations verify resource ownership — no cross-user access
- API key plaintext is never stored; only the SHA-256 hash
- File URLs may be signed (HMAC) with time-windowed expiry — don't cache them long-term
- Password hashing uses Argon2id with automatic parameter upgrades
