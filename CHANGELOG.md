# Changelog

All notable changes to Sheaf are documented here. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and Sheaf adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

`v0.x.y` releases are betas — APIs and database schema may still change. The first stable release will be `v1.0.0`.

## [Unreleased]

### Notes

A small scratchpad surface, deliberately separate from journals. One free-form note per member and one per system, encrypted at rest, capped at 5000 plaintext characters.

- **By design lightweight.** No revision history, no System Safety integration, no destructive-auth on edits. Edits overwrite the previous content; clearing the textarea wipes the column. Aimed at "trigger list / fav drink / current med doses" type quick reference, where journals' versioning + protection is unwanted overhead.
- **Single note per scope.** Multiple notes per member would just reinvent custom fields, which already exist for that.
- **Markdown rendered with no embedded images.** Same renderer as bios.
- **API**: `note` field added to `MemberCreate` / `MemberUpdate` / `MemberRead` and to `SystemUpdate` / `SystemRead`. No new endpoints; piggybacks on the existing PATCH surfaces with the existing `members:write` and `system:write` scopes.
- **Frontend**: notes textarea added under the bio editor on member create/edit, and as a section on Settings → System. Read view shows the note as a dashed-border card under the bio.
- **Export**: notes are decrypted to plaintext in the Article 20 export alongside other free-text content.

### Polls

A small voting surface for system-internal decision-making. Headmates cast votes "as" a fronting member, and every action lands in an audit log.

- **Vote attribution**: each vote is attributed to a specific member, who must be part of the current front at vote time. Stops one headmate from silently casting on behalf of others. Anonymous voting was considered and rejected: same-actor repeat-voting is too easy without a real member-auth surface (out of scope for v1).
- **Audit log**: every cast, change, and withdraw appends a row with the voted-as member, the chosen options, the full set of fronting member ids at vote time, and the actor user id.
- **Two kinds**: `single_choice` and `multi_choice`. Ranked voting deferred.
- **Two visibility modes**: `live` (tally and audit visible while the poll is open) and `end_only` (both hidden until close, to avoid bandwagon effects). Locked at creation; cannot be toggled later.
- **Deadline only**: `closes_at` is required at creation and immutable. Manual close is intentionally not supported, since it would be abusable without member-level auth. Free tier accepts 1 hour to 14 days; raise the env-var bounds when scaling allows. Premium tiers and self-hosted deployments default to longer windows.
- **Retention**: 30 days post-close by default, configurable per-poll up to a tier-scaled cap (free 30d, plus 180d, self-hosted unlimited). The cleanup job hard-deletes the poll, votes, and audit log together.
- **Concurrent open polls**: a per-tier cap (free 5, plus 20, self-hosted unlimited) so one runaway question doesn't tile the dashboard. Closed polls don't count toward the cap.
- **Custom fronts**: per-poll opt-in flag (`include_custom_fronts`, default false). Members marked `is_custom_front=true` (Asleep, Away, etc.) are usually system states rather than voters; opt in if you actually want them counted.
- **Server-config endpoint**: `GET /v1/polls/server-config` returns the calling user's effective tier limits (close-window, retention, concurrent-open). The frontend fetches it to clamp inputs and signal upsell paths.
- **System Safety integration**: new `applies_to_polls` safety category. Delete is gated by `verify_destructive_auth` and queues a pending action when safeguarded.
- API: `POST/GET/DELETE /v1/polls`, `POST/DELETE /v1/polls/{id}/votes`, `GET /v1/polls/{id}/audit`. New scopes: `polls:read`, `polls:write`, `polls:delete`.
- Frontend: new `/polls` route in the sidebar with list + detail + voting UI, result bars, and audit log table.
- Question, description, and option text are encrypted at rest.

### Reminders

A new reminders surface alongside notification channels. Two trigger types share one data model and ride existing notification channels for delivery.

- **Automated timers**: fire after a front-change event. Choose a specific member (or "any"), a side of the transition (start / stop / either), and a delay in minutes/hours. The reminder dispatches `delay_seconds` after the matching front change. Useful for member-bound self-care cues, medication routines, partner/therapist coordination pings.
- **Repeated reminders**: cron-style schedule. UI exposes a structured daily / weekly / monthly + time-of-day picker; an "Advanced" toggle takes a raw 5-field cron expression for power users. Each reminder has its own IANA timezone.
- **Member-scoped repeated reminders**: by default, reminders fire system-wide on schedule. Optionally scope a reminder to specific members so it only fires when one of them is currently fronting. When the schedule fires while no scoped member is fronting and `digest_when_absent=true` (default), the missed firings queue (capped at 5) and drain as a single digest notification when one of the scoped members next starts fronting.
- API: `POST/GET/PATCH/DELETE /v1/reminders`, gated by the existing `notifications:read` and `notifications:write` scopes (a caller permitted to manage notification destinations also manages reminders that ride them). New `GET /v1/channels` flat-list endpoint for picking a channel without traversing watch tokens.
- Backend: shared `notification_outbox` rows with `event_type="reminder"`. The dispatcher branches on event_type and skips member-resolution / filter / debounce / quiet-hours for reminders — they were scheduled at a specific time on purpose. Per-channel concurrency limits still apply.
- Frontend: new `/reminders` route in the sidebar between Notifications and Settings, with a list view and a single create/edit dialog covering both kinds.
- Title and body are encrypted at rest, matching the existing convention for member descriptions and journal entries.

### Front-time analytics

- New `GET /v1/analytics/fronting` endpoint, gated by the existing `fronts:read` scope. Returns per-member time-on-front summaries over a configurable window (defaults to last 30 days, capped at 5 years).
- Co-fronting double-counts intentionally: if Alice and Bob co-front for an hour, both accrue +3600 seconds. Matches SimplyPlural's analytics shape and the reading users expect for "how much did Alice front this month".
- Hour-of-day distribution: 24 buckets indexed 0-23 in the requested timezone (passed as `tz` query param). Sessions crossing hour boundaries split proportionally; DST transitions handled via zoneinfo-aware walking.
- Custom fronts ride along with the `is_custom_front` flag set on the per-member row, so clients can filter them out of headcount-style charts.
- Members with zero fronting time still appear in the response so the UI can list them without special-casing.
- Frontend: new `/analytics` route in the sidebar (between Fronts and Groups). Cards for total time per member (horizontal bar chart, member colours), hour-of-day distribution (with per-member breakdown in the tooltip), and a per-member detail table. Window selector chips: 7d / 30d / 90d / 1 year. Times shown in the browser's local timezone.

### Custom fronts, member emoji, custom status on fronts

A bundle of three small SimplyPlural-parity additions to the member and front data models:

- **Custom fronts** — new `is_custom_front` boolean on `members`. Marks a Member as a non-counting fronting entity ("Asleep", "Away", "Lost time"). Custom fronts behave like members for fronting/groups/notifications, but are excluded from member-headcount statistics and listed in their own section on the Members page. The SP importer now sets the flag instead of prefixing imported `frontStatuses` with `[Imported SP custom front]` in the description.
- **Member emoji** — new optional `emoji` String(8) on `members`. Surfaced alongside the avatar fallback in compact lists and as a prefix on member badges in the dashboard, fronts page, and notification picker.
- **Custom status on fronts** — new optional `custom_status` Text column on `fronts`. Encrypted at rest (matching the precedent set by member descriptions and journal bodies). Lets you annotate a fronting period with context like "during a job interview" without amending the bio. Surfaced inline on the dashboard and fronts pages, editable via the start-front dialog. PATCH semantics: omit the field to keep, send `null` to clear, send a string to replace.

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
