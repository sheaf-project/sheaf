# Changelog

All notable changes to Sheaf are documented here. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and Sheaf adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

`v1.0.0` is the first stable release. The `v0.x.y` releases were betas; from 1.0 on, the v1 API and database schema carry semver compatibility guarantees.

## [Unreleased]

### Fixed

- **Member banners 403 behind the image worker.** Banner images are stored under a new `banners/` storage prefix (added in 1.0.2), but the bundled `selfhost-utils/cf-image-worker` allowlist (`ALLOWED_KEY_PREFIXES`) still only permitted `avatars/,bios/`, so the worker rejected every banner with a 403 before reaching S3. The bundled default now includes `banners/`. **Selfhost operators running the image worker must add `banners/` to their `ALLOWED_KEY_PREFIXES`** (redeploy the worker) for member banners to load.
- **Clearer errors when browser-push subscription fails.** When a recipient subscribes to web push, the browser's own `pushManager.subscribe()` can fail with an opaque message (e.g. Chrome's "Registration failed - push service error") if it can't reach its push backend - and the redeem page showed that raw string with nothing logged for debugging. The page now logs the full exception to the console, maps the common failures to actionable guidance ("permission was denied", "your browser couldn't reach its push service - check that notifications or Google Play services aren't blocked"), and refuses to attempt a keyless subscribe when the server's VAPID key can't be loaded (which produced that same opaque error) in favour of a clear "couldn't load the push key" message.

## [1.0.2] - 2026-06-14

### Added

- **Member banner photo.** Members now have an optional wide header image (`banner_url`) alongside their avatar, shown across the top of the member profile. It rides the same upload pipeline and trust model as avatars: upload-and-crop (landscape 3:1) or paste an external URL, gated by the same instance image-upload and external-image policies, stored as a bare key and signed on read. Banners are included in the data export, round-trip through native re-import and the with-images archive, and are tracked by the orphaned-file cleanup and the "where is this image used?" view so a banner blob is never garbage-collected while still referenced.

### Changed

- **Image cropper: edge-to-edge crops and quarter-turn buttons.** Every crop surface that shares the dialog - avatars, bio and journal image embeds, and the new member banner - can now zoom out and pan past the image edges, so a fixed-aspect crop (the round avatar, the 3:1 banner) can include the whole of an image whose ratio doesn't match, letterboxed, instead of forcing the corners or sides off. The rotation control also gained rotate-left / rotate-right buttons that snap to the nearest 90 degrees, alongside the free slider for fine angles.

### Fixed

- **Route labels in metrics and rate-limit history dropped the `/v1` prefix.** Starlette 1.0 changed `request.scope["route"].path` to be relative to the outermost prefixed router (reporting `/members/{id}` instead of `/v1/members/{id}`) without moving the prefix into `root_path`, which silently relabelled every `sheaf_http_requests_total` series and rate-limit bucket and made the per-account rate-limit history record routes without their prefix. A shared `route_template()` helper now reconstructs the full template (keeping path params as placeholders so cardinality stays bounded), used by both the metrics middleware and the rate-limit middleware.
- **Custom-field date "No year" label.** The optional-year checkbox on a date custom field read "No birth year", borrowed from the member Birthday field; it now reads "No year" everywhere except the Birthday field, which keeps its specific wording.
- **Export build worker no longer spins the job loop every 10s.** The pending-export poll interval defaulted to 10 seconds (with a mislabelled comment), and because the background job runner wakes at the smallest registered interval, that held the whole registry to a 10-second tick. Exports are a deferred build-then-download/email flow, so the default is now 60 seconds, cutting idle wakeups across all background jobs. Override with `EXPORT_BUILD_INTERVAL_SECONDS`.
- **"Send test" on a mobile-push channel failed with a db-session error.** The test-send endpoint didn't pass its database session through to the dispatch handler, and the mobile-push (FCM/APNs) handler needs it to fan out over the recipient's registered devices, so a test send returned "mobile push handler called without a db session". It now passes the session, matching the real dispatcher path; other channel types were unaffected.

## [1.0.1] - 2026-06-12

### Added

- **Support page.** A new Support entry in the sidebar with two sections. The top is an operator contact card populated from optional env vars (`SUPPORT_EMAIL`, `SUPPORT_URL`, `SUPPORT_NOTE`, `STATUS_URL`), surfaced read-only via `GET /v1/auth/config` the same way the legal-footer links are; it hides entirely when none are set, so a bare self-host shows nothing there. The bottom is static and identical on every instance: links to the project's GitHub issue tracker for bug reports and to the security policy + `security@sheaf.sh` for private vulnerability disclosure. The status-page link is operator-set rather than static, since a self-hosted instance's status page is the operator's own.
- **SMTP2GO delivery webhook.** A new `POST /v1/webhooks/smtp2go/events` endpoint feeds SMTP2GO's delivery/bounce/spam events into the same deliverability lifecycle as the SES and SendGrid handlers, so bounce suppression and soft-bounce self-healing work when sending via SMTP2GO (over the `smtp` backend). `delivered` clears transient soft-bounce state, `bounce` maps hard/soft from SMTP2GO's classification (an unclassified bounce defaults to soft, the conservative choice), and `spam` is treated as a complaint. SMTP2GO does not sign payloads, so the endpoint is guarded by a shared secret in the URL (`SMTP2GO_WEBHOOK_SECRET`; returns 404 when unset) - point the SMTP2GO webhook at `/v1/webhooks/smtp2go/events?token=<secret>` (JSON or form-encoded output both accepted) and enable at least the Delivered, Bounce, and Spam events. See SELFHOSTING for setup.

### Fixed

- **Email deliverability no longer permanently locks an account out.** Bounce/complaint handling was a write-once-to-bad flag: a single transient soft bounce (e.g. a greylisting MX deferring the first delivery attempt) flagged the address undeliverable, the matching `delivered` event was ignored, and nothing - not re-verifying, not even changing email - ever cleared it, so all further mail to the account was silently dropped with admin-only recovery. Deliverability is now a recoverable lifecycle: a soft bounce only blocks after `EMAIL_SOFT_BOUNCE_THRESHOLD` (default 5) accumulate without an intervening delivery; a successful-delivery event clears soft state automatically (the SendGrid webhook now consumes `delivered` events - enable Delivered in your event selection); and a flagged user gets a sign-in banner prompting them to re-verify or change their address. Verification emails are now sent even to a blocked address (the recovery channel must reach the user), and completing verification - or changing to a new address - clears the deliverability flags. New `POST /v1/auth/revalidate-email` drives the re-verify recovery. Hard bounces and spam complaints still block immediately and are cleared only by re-verification, never silently undone by a later delivery.

## [1.0.0] - 2026-06-12

### Added

- **Admin: per-account rate-limit hit history.** When a rate-limit check blocks a request that belongs to a logged-in account, the hit (bucket, route, time, IP) is recorded to a short-lived, size-capped Redis list, and admins get a per-account view of it: `GET /v1/admin/users/{id}/rate-limit-history` plus a section in the Explain-account panel showing per-bucket totals and recent entries. This is the per-account drill-down the aggregate Prometheus counters can't provide ("what has THIS account tripped recently"). Only blocked checks are recorded, nothing touches Postgres, retention and size are bounded (`RATE_LIMIT_HISTORY_HOURS`, default 48h; `RATE_LIMIT_HISTORY_MAX_ENTRIES`, default 200; `RATE_LIMIT_HISTORY_ENABLED` to turn it off), the key is purged on account deletion rather than waiting out the TTL, and the history is included in the admin DSAR dossier since it is held personal data. Anonymous traffic (failed logins from logged-out clients, the global per-IP backstop) is not attributable to an account and is not recorded. Reading the history writes no audit row, same as the other read-only triage endpoints.
- **Re-import is now fully idempotent (content dedup).** Import deduplication covers everything, not just members: tags and groups match by name, fronts by their exact interval and member set, and journals, edit-history revisions, board messages, polls, reminders, and notification config by the source timestamps every importer already preserves. Importing the same export twice - any source: native JSON, the with-images archive, PluralKit, SimplyPlural, Tupperbox, PluralSpace, or Prism - adds nothing the second time; each section reports a `*_skipped` count on the import detail page instead. `conflict_strategy=create` keeps the old append-everything behaviour. This also fixes a crash on re-import in the SimplyPlural, PluralSpace, and Prism importers: a reused custom-field definition plus an already-present (deduped) member violated the one-value-per-(field, member) constraint and failed the whole job; the value and group-membership guards are now pre-seeded with what the system already has.
- **Export-with-images archive import.** The zip the async export job produces (export.json + images/) can now be uploaded straight back through Settings -> Import: avatars, markdown image embeds, and journal/revision image attachments are re-uploaded to the importing account through the same pipeline as regular uploads (format sniff, EXIF strip + dimension cap, storage quota) and every reference is rewritten to the new keys. Previously the zip's images were carry-your-own: the JSON imported but image references were stripped and re-uploading was manual. The plain JSON import is unchanged. Restore is quota-aware (stops cleanly with a warning when full), runs the member-cap check before writing any blob so a failed import never strands storage, and discards uploads nothing ends up referencing (e.g. when the dedup pass skips an already-present member) so re-imports don't leak quota. The image-ingest pipeline itself is now shared code (`import_media`) used by the PluralSpace and Prism importers too, instead of three private copies.
- **Import deduplication.** Every importer (PluralKit, SimplyPlural, Tupperbox, PluralSpace, Prism, and Sheaf native re-import) now matches each incoming member against the system's existing roster before writing, so re-importing the same export no longer doubles your members. Matching is by PluralKit ID where present (exact, so PK round-trips cleanly) and otherwise by name, scoped so a member and a custom front sharing a name never collide. A new `conflict_strategy` option chooses what happens on a match: `skip` (default - leave the existing member untouched and add nothing), `update` (overwrite the existing member's importable fields from the export), or `create` (the old append-everything behaviour, kept as an escape hatch). The tier member cap now counts only the members an import would actually create, so re-importing into a near-full system no longer trips the cap on members that already exist. The PluralKit member HID is now also confirmed to land in each member's `pluralkit_id` field, which doubles as the dedup key. (Non-member content dedup landed alongside - see the entry above.)
- **SimplyPlural chat history import.** The SP importer can now bring across chat messages (opt-in, off by default since chat can be large). SP's multi-channel chat collapses onto the Sheaf system board with each message prefixed by its channel name, authors resolved to the imported members, reply threads preserved, and SP `<###@member###>` mention tokens rewritten to readable `@name`. It reads both export shapes (the `messages` channel map and the flat `chatMessages` array) plus their field-name aliases. Legacy exports whose message bodies are still encrypted in SimplyPlural's old, undocumented format are detected and skipped with a clear warning advising a fresh export or an API import - Sheaf can't decrypt them (the format isn't published and no client does). No chat content is ever quoted into the import log.

### Fixed

- **Build provenance for local compose builds.** `GET /v1/version` reports the commit/tag/build-time the backend was built from; CI-built ghcr images already set these, but a local `docker compose build` left them null because the compose `args` didn't forward them. The app service now accepts `GIT_COMMIT` / `GIT_TAG` / `BUILD_TIME` from the host environment (documented in SELFHOSTING.md), so a compose build can identify itself too. Unset values stay null, same as before.
- **SimplyPlural importer survives real-world export variants.** SP exports are a decade of MongoDB-into-Firebase sediment, and the shapes tidy test fixtures never produce were failing or silently dropping data on live imports. The importer now handles: collections exported as either an array or a map keyed by id (a map previously crashed the whole job); timestamps as integer/float millis, numeric strings, zone-less ISO strings, or Firebase `{_seconds, _nanoseconds}` objects (the old int-only parser silently skipped or crashed on the rest, dropping entire front histories); the renamed collection keys (`frontStatuses`/`customFronts`, `frontHistory`/`fronters`, system profile under `settings` or `users[0]`); avatars stored as an `avatarUuid` plus owner id (constructed to the serve URL, still policy-gated); and 8-hex ARGB colours. Wrong-typed name/description/colour fields are coerced away instead of crashing, and the import detail page now logs what the export contained alongside what imported so a partial import is diagnosable at a glance. Cross-referenced against the Prism importer's handling of the same quirks.

## [0.5.1] - 2026-06-10

### Added

- **Leader-election and import-backlog observability metrics.** New `sheaf_leader_is_leader` gauge (1 on the leader process, 0 on standbys; multiprocess_mode=livesum, so `sum(sheaf_leader_is_leader) != 1` alerts on a wedged election with zero leaders or, impossibly, a split brain - a gap previously only caught indirectly via the notification-backlog alert and so invisible during quiet periods), `sheaf_leader_transitions_total` counter for leadership-flap detection, and `sheaf_imports_oldest_pending_seconds` gauge mirroring the notifications outbox-age signal now that the import runner is NOTIFY-driven. The leader gauge is only published when `LEADER_ELECTION` is enabled. (The per-job freshness, run-count, and duration metrics the same pass would have wanted already existed: `sheaf_job_last_success_timestamp`, `sheaf_job_runs_total`, `sheaf_job_run_duration_seconds`.)

### Fixed

- **Multi-worker metrics exposition.** Three bugs that surfaced only with `WEB_CONCURRENCY > 1` and `METRICS_BIND=separate`: every metric family was exported twice (the live metric object's per-process view, often zeros for a gauge another worker maintains, alongside the multiprocess collector's real cross-process aggregate, so a scraper could keep the zero); the separate metrics listener raced on its port across workers, with the loser crash-looping on `EADDRINUSE`; and each (re)spawned worker wiped the shared multiprocess directory, resetting every other worker's accumulated counters. Metric objects now bind to an unregistered registry in multiprocess mode (values still reach the mmap files), the listener bind skips gracefully when another worker already holds the port, and the directory is no longer wiped per-worker. Single-worker deployments were unaffected.

## [0.5.0] - 2026-06-09

### Added

- **PluralSpace importer.** New source on the import picker that takes a PluralSpace data export zip (manifest + data + media) and brings across system profile, members, custom fronts, member groups, custom fields, fronts, journal entries, polls, and avatars. Member roles import as Sheaf tags, multi-channel chat history collapses onto the system board with channel-name prefixes, and open-ended polls get a one-year close window since Sheaf polls require one. Format mappings that don't have a clean Sheaf equivalent surface user-facing warning events on the import detail page rather than silently dropping data: journal `visibility_level` is dropped, multi-value custom fields collapse to newline-joined text, and `thoughts[]` is unsupported pending a Sheaf surface for it. Avatars run through the same normalize_image pipeline as regular uploads (EXIF strip, dim cap, animation gate, quota check) so the importer can't be used as an upload-policy bypass.
- **Prism importer (encryption-aware).** New source on the importer picker that takes an encrypted Prism (`.prism`) export and a decryption passphrase. Decrypts the PRISM1 envelope server-side via the crypto module landed previously, then walks the JSON payload to bring across headmates, front history, member groups, custom fields, notes (as journal entries), polls, chat messages (collapsed to the system board), member board posts, inline base64 avatars, and per-message media attachments (each decrypted with its own XChaCha20-Poly1305 key from the JSON metadata). The passphrase is encrypted at rest in `payload_metadata.encrypted_credential` using `SHEAF_ENCRYPTION_KEY` while the job runs and wiped by the runner at terminal state, mirroring the PluralKit API token flow. Surfaces that don't have a Sheaf equivalent (sleep tracking, habits, the channel-bound reminders model, cross-system friends, conversation categories, front-session comments) get one user-facing warning event each on the import detail page rather than dropping silently. Multi-conversation chat history collapses onto the system board with each body prefixed `[DM ...]` / `[Chat: ...]` so the original thread is recoverable. Open-ended polls get a one-year close window since Sheaf polls require one; freeform "Other" poll responses fold into the option text. Prism-specific custom field types (slider, choice, etc.) collapse to TEXT with a warning. Avatars and media attachments route through the same `normalize_image` pipeline (EXIF strip, dim cap, animation gate, quota check) as regular uploads so the importer isn't a back door around upload policy.
- **Nine new web palettes (Android parity).** Asexual, Bi, Crimson, Goldenrod, Mint, Ocean, Pan, Plural, and Sepia join the existing Classic / Purple / OLED / Pride / Trans / Non-binary set. Colours and slot mappings are ported from the matching Android theme files so the two clients stay visually identifiable as "the same theme". Material You is Android-only and remains web-skipped. Existing user preferences are unaffected — the catalog only grows.

### Changed

- **Background loops are coordinated by leader election.** Every replica competes for a Postgres advisory lock; the holder runs the job runner, notification dispatcher, and import runner, and standbys take over within seconds if the leader dies. Single-instance deploys behave exactly as before (the lone process always wins); `LEADER_ELECTION=false` restores the old run-everywhere behaviour.
- **The job runner wakes as often as its fastest job.** A fixed 15-minute sleep silently floored every job's cadence: repeated reminders declaring 60 seconds fired up to 14 minutes late (a real correctness issue for medication-style reminders) and queued export builds cleared one per 15 minutes. The runner now wakes at the fastest enabled job's interval (floored at 15s); per-job elapsed checks still decide what actually runs. Scheduling also keys off the last run attempt rather than the last success, so a permanently-failing job retries at its declared interval instead of every wake.
- **Imports start the moment they are enqueued.** The enqueue transaction sends a Postgres NOTIFY that wakes the import runner immediately instead of waiting out its poll interval; the poll remains as a safety net, so a dropped notification delays a job by at most one interval rather than losing it.

### Fixed

- **Notifications claimed by a crashed dispatcher are no longer lost forever.** The dispatcher marks rows claimed before delivering; a worker killed between the two (crash, deploy, OOM) stranded its whole batch permanently, because reclaim required an unclaimed row and the retention sweep only deletes delivered ones. Claims are now leases: rows whose claim has outlived the lease window (default 15 minutes, `NOTIFICATIONS_CLAIM_LEASE_MINUTES`) are presumed orphaned and re-claimed. Lease-based rather than reset-on-startup, so it stays correct with multiple replicas.
- **Export jobs stuck RUNNING no longer wedge the user permanently.** A crash or deploy mid-build left the job RUNNING forever, and since new exports are refused while one is pending or running, the affected user got a permanent 409 fixable only by manual SQL. A recovery sweep now resets stale RUNNING exports to pending (mirroring the import runner's), and parks a job as failed after three stalled attempts so a poisoned export can't crash-loop the worker.

- **Password hashing moved off the event loop.** Argon2 verification/hashing (login, register, password change, and every step-up password gate) previously ran inline on the single async event loop, freezing all request handling for the 50-150ms each hash takes — a burst of concurrent logins would visibly stall the whole instance. The work now runs in a worker thread, bounded by a new `PASSWORD_HASH_CONCURRENCY` setting (default 4) since each in-flight Argon2 hash also holds ~64MiB of memory. Excess auth attempts queue at the semaphore rather than failing.
- **SimplyPlural and Tupperbox importers now surface per-record skip warnings.** The newer importers (Prism, PluralSpace, Ampersand) accumulate per-category counters and fold them into one warning event each on the import detail page so the user can see what didn't come across without having to compare counts to the source export. The SP and TB importers were the holdouts: SP silently dropped custom-field values whose definition wasn't in the export, front-history rows whose member wasn't selected, group-membership references to unknown members, and group parents that didn't resolve; TB silently dropped tupper rows with no name, group rows missing a name or id, and `group_id` references on tuppers that didn't match a real group. Each of these now emits a single summary warning with the affected row count. The SP custom-field walk also got a small efficiency fix on the side: the per-member info-map join was O(members * members), it's now a single dict lookup.
- **Image upload concurrency cap.** Pillow normalisation already ran in the thread pool so the event loop stayed responsive, but unbounded concurrent uploads could still pile up enough in-flight bitmap memory to OOM a small box. New `IMAGE_NORMALIZE_CONCURRENCY` setting (default 4) gates entry to the decode pass so peak memory across uploads is bounded. Excess uploads queue at the semaphore rather than failing.
- **Export builder streams to disk instead of buffering in memory.** The async export job previously assembled the whole zip — JSON + every image blob — in a single in-memory `BytesIO` before uploading to storage, which could OOM the worker on accounts with many or large images. The builder now writes the zip through a tempfile (configurable via `EXPORT_BUILD_TMP_DIR`), streams each image blob in one at a time, and uploads with `boto3.upload_file` (which switches to multipart automatically on S3). Filesystem-backed exports rename the tempfile into place; S3 cleans up the tempfile after upload. Peak memory is bounded by per-image blob size (caps at the upload pipeline's `MAX_ANIMATED_DECODED_BYTES`, default 100 MB) rather than the whole export size.

### Fixed

- **Auto-generated encryption key no longer logged.** First boot without `SHEAF_ENCRYPTION_KEY` set logged the generated key value itself alongside the file path, which routinely lands key material in journald, container logs, and any log shipper. Only the path is logged now, matching the JWT secret auto-generation. The key file (`data/encryption.key`, 0600) is unchanged — existing deployments are unaffected, but selfhosters who booted without a configured key should consider their logged key exposed if their logs are aggregated anywhere.
- **Export/import now round-trips per-member front-notification preferences and the system front-coalescing toggle.** The Article 20 export was silently dropping each headmate's `notify_on_front_*` opt-ins (the in-app front-prompt settings) and the system-level contiguous-front coalescing preference, so a backup-and-restore or a move to a new instance quietly reset them to defaults. Both now ride along in the export and are restored on re-import, with the per-member "notify me when these members front" list remapped to the new member ids rather than left pointing at stale export ids. A new `tests/test_export_import_parity.py` introspection guard asserts every user-data column on every model is either exported or explicitly excluded with a stated reason, so a newly-added field can't silently fall out of the export the same way again.
- **Export-ready email links opened a 404.** The email pointed at `/settings/export?job=...` but the export UI lives under `/settings/data`. Link now lands on the right page and the data settings page scrolls the matching backup row into view with a brief highlight ring when arriving with a `?job=` param.
- **Filesystem export storage now honours `SHEAF_DATA_DIR`.** The export storage backend hardcoded `/app/data/exports` as the on-disk export root, ignoring the operator's configured `sheaf_data_dir`. That was a no-op for the standard Docker deployment (the WORKDIR + default data dir resolve to the same path) but broke non-Docker selfhosters with a custom data layout and made the path bypass operator backup conventions. Now resolves under `{SHEAF_DATA_DIR}/exports/...`.

### Security

- **Login no longer leaks account existence by timing.** A login for an unknown email skipped the Argon2 verify entirely (the short-circuit never reached it), returning ~100ms faster than a wrong-password attempt on a real account and letting an attacker probe which emails are registered by latency alone. The unknown-user branch now spends an equivalent Argon2 verify. The password-reset request path's smaller asymmetry (the real-user branch committed a DB transaction the no-match branch didn't) is closed the same way - both branches now commit.
- **Email addresses are redacted in logs.** Addresses are encrypted at rest, but a few log lines (the no-backend send warning, the blocked-recipient skip, the admin-promotion line) wrote them in plaintext to stdout/journald. They are now masked to `a***e@example.com` - enough to spot a bouncing domain, not enough to expose the address.
- **Removed the unused `rehype-sanitize` dependency.** It was installed but never wired into the markdown renderer, implying an XSS protection it wasn't providing. react-markdown v10 escapes raw HTML by default and the app never adds `rehype-raw`, so bios/journals never render raw HTML in the first place; the dependency was dead weight. Anyone later enabling raw-HTML rendering must now consciously add sanitisation rather than assuming it is already present.
- **X-Forwarded-For is parsed right-to-left.** With TRUSTED_PROXIES configured (the documented production posture), the client IP was taken from the LEFTMOST X-Forwarded-For entry - but proxies (including the shipped nginx templates) append the peer they saw, so the leftmost entries are client-supplied. Any client could rotate a fake IP per request and walk through every per-IP rate limit (login, register, password reset, captcha, the global backstop) and poison signup/session/audit IP records. The parser now walks the chain right to left and returns the first entry that is not itself a trusted proxy; malformed chains and all-proxy chains fall back to the direct peer.
- **CSRF Origin checks on cookie-authenticated mutations.** SameSite=Lax was the only cross-site defence and deliberately exempts top-level POST navigations, the classic auto-submitting-form CSRF shape. New middleware: unsafe requests carrying a Sheaf auth cookie and a browser Origin header must originate from the request's own host, SHEAF_BASE_URL, or the new CSRF_TRUSTED_ORIGINS list; `Origin: null` is rejected. Requests without an Origin header (curl, mobile apps, server-to-server) and bearer-only requests are untouched.
- **Outbound webhook/ntfy connections are pinned to the validated IP.** The SSRF guard resolved and validated the destination, then let the HTTP client resolve again - a rebinding nameserver could answer the check with a public address and the connection with an internal one. Delivery now resolves once (async, with a timeout, so a black-holed nameserver can't stall the dispatcher), validates every returned address, and connects to the validated IP directly while keeping the original hostname for the Host header and TLS SNI/verification. Web-push endpoints (client-supplied URLs) now pass the same SSRF gate, and the pywebpush call runs in a worker thread with an explicit timeout instead of blocking the event loop.

- **Session tokens no longer cross the API boundary.** The user session list (`GET /v1/auth/sessions`), the admin per-user session list, and the admin dossier all returned the raw session id - which is the literal `sheaf_session` cookie credential, so any admin (or `admin:read` API key holder, or a script reading your own session list) could lift a live token for another session and silently impersonate it. All three surfaces now return an opaque digest handle; the rename/revoke/terminate endpoints accept the handle and resolve it server-side, scoped to the owning user's session set. The secondary-session pairing response carries the handle too, and audit rows store handles rather than tokens. API note: session ids visible to clients changed format; clients that only round-trip ids from the list endpoints are unaffected. The session list's `is_current` marker now also works for session-bound JWT callers (mobile), not just cookie auth.
- **The admin account-recovery endpoints are audited, reasoned, and cannot target admins.** reset-password, change-email, disable-totp, verify-email, and cancel-deletion previously wrote no audit rows, required no reason, and would happily operate on another admin's account - chaining change-email + reset-password was silent admin-account takeover with zero trail. All five now require a reason and write an audit row (visible to the affected user on their account activity page); the three credential-touching endpoints (reset-password, change-email, disable-totp) refuse admin targets outright - recovering a locked-out admin is deliberately an out-of-band operation. The one-time password from reset-password is never logged.
- **Remaining silent admin mutations now audit.** Invite create/delete (the code value itself stays out of the log), member-limit overrides, and manual job triggers (including retention and cleanup runs) write audit rows.

- **Importer hardening across every source format.**
  - The PluralSpace and Prism importers now enforce the tier member cap before writing anything, like the PluralKit, SimplyPlural, Tupperbox, and native importers always did - a free-tier account can no longer bypass its member limit by importing.
  - The PluralSpace zip parser refuses entries whose declared decompressed size is over a cap (256MB for data.json, 4MB for the manifest, 100MB per media file). DEFLATE reaches roughly 1000:1, so a small upload could previously expand to tens of gigabytes in memory when read.
  - Prism envelope decryption (scrypt with attacker-supplied, bounded parameters) and PluralSpace zip parsing now run in a worker thread with a small concurrency cap instead of on the event loop, and both preview endpoints gained per-user rate limits. A handful of crafted preview requests could previously freeze all request handling.
  - Avatar URLs carried in third-party exports now pass a shared policy gate in every importer (PluralKit, SimplyPlural, Tupperbox, PluralSpace, Prism, and native re-import): only plain http(s) URLs survive (a crafted export can no longer plant a javascript:/data: URL in a profile field), and external URLs are dropped entirely when the instance sets ALLOW_EXTERNAL_IMAGES=false - imports are no longer a way around the tracking-pixel policy the regular profile-write path enforces.

- **`GET /v1/systems/{id}` is now owner-only.** The endpoint previously honoured `privacy=public` by returning the full owner view of the system — including the decrypted private note and the destructive-action confirmation tier — to any authenticated account that knew the id. Nothing consumes a public read path today (the web client only uses `/systems/me` and there is no discovery surface), so cross-tenant reads are closed entirely until public profiles ship as a designed feature with a dedicated public schema. The `privacy` field remains settable; it just grants nothing yet.
- **Enabling 2FA now requires the account password.** `POST /v1/auth/totp/setup` takes a `{"password": ...}` body (API change — clients sending no body get a 422). Without the gate, a session-only attacker could enrol an attacker-controlled TOTP secret and recovery codes, making the stolen session durable: change-password, change-email, and TOTP-disable would then demand a code only the attacker could produce. Enabling a factor is now held to the same re-auth standard as disabling one.
- **TOTP codes are single-use.** Every accepted code is marked consumed (Redis, TTL outliving the ±1-step drift window) and replays are rejected at every TOTP gate — login, change-password/email, TOTP disable/enable, recovery-code regeneration, account-data read, exports, admin step-up, and System Safety destructive-action confirmations. Previously a code observed over a shoulder or in transit stayed valid for up to ~90 seconds anywhere TOTP was accepted. Side effect: performing two TOTP-gated actions inside one 30-second step now needs two different codes (wait for the next one, or use the adjacent-step code your authenticator shows next).
- **Password reset now revokes all sessions, trusted devices, and lockout state.** Redeeming a reset token previously changed the password and nothing else, leaving an attacker's live session and trusted-device cookie untouched by the victim's recovery. It now matches change-password's posture; with no calling session to spare, all sessions die.
- **Admin step-up is per-session.** The step-up flag was keyed per-user, so the moment an admin completed step-up anywhere, every other live session on the account — including a stolen one — silently inherited admin authority for the 2h window. Each session now passes the step-up gate itself.
- **Step-up password/TOTP gates feed the account lockout.** The re-auth checks on change-password, change-email, account deletion, data export, account-data read, delete-confirmation changes, admin step-up, and System Safety destructive actions now consult and increment the same unified failed-attempt lockout as login, instead of allowing unlimited password/code guesses from a hijacked session.

## [0.4.0] - 2026-06-06

### Added

- **Admin audit log.** Every state-changing admin action (user updates, approvals, rejections, member-limit changes, plus the safety-reset / pending-bypass / import-log-view endpoints below) now writes a row to a new `admin_audit_events` table with the acting admin's identity, the target, before/after diffs for changed fields, and optional reason text. Two read surfaces: a paginated, filterable admin panel under Admin > Audit log, and a per-account "Admin activity on your account" card in Settings > Account so every user can see what admins have done to their account. Routine reads (list users, single-user detail, search) are deliberately not logged so the table stays signal-rich for abuse detection — logging every browse would let a malicious admin hide their data trawls in the noise. The log is append-only by design; there is no edit or delete endpoint.
- **Admin emergency-support endpoints.** Three operator tools for support tickets, all gated behind admin auth + a required reason string and all logged through the new audit table:
  - **Reset System Safety** clears all `safety_applies_to_*` toggles, zeros the grace period, and sets `delete_confirmation=none` on the user's system. For the "I locked myself out with strict safeguards" support case. Doesn't touch already-queued pending actions — that's bypass.
  - **Bypass pending** finalises every queued System Safety pending action on the user's system immediately, without waiting out the grace window. Idempotent on an empty queue. Writes one audit row per finalised action plus a user-level summary row so the per-action history is recoverable.
  - **View import-job log** (`POST /v1/admin/import-jobs/{id}` with a reason in the body) exposes the structural events of a single import job to admins. Importer events are mostly counts and source IDs (PluralKit HIDs, SP `_id`s) but exception-text branches could in pathological cases quote a value that failed Pydantic validation, so treated as privacy-sensitive — reason required, every view writes an `import_log_view` audit row. The accompanying `GET /v1/admin/users/{id}/import-jobs` browse listing returns summaries only (no events, no audit row).
- **Admin small-actions batch.** Five tightly scoped admin tools, surfaced inline on the existing Admin > Users row expander where applicable:
  - **Explain account.** `GET /v1/admin/users/{id}/explain` returns a one-shot dossier — tier, status, verified/2FA flags, signup IP, last login, session count, API-key count, system metadata, and the most recent 20 admin audit rows that touched the account. Pure read; deliberately does not write an audit row.
  - **List + terminate session.** `GET /v1/admin/users/{id}/sessions` exposes a user's active sessions to operators (UA / IP / nickname / timestamps). `POST /v1/admin/users/{id}/sessions/{sid}/terminate` revokes one, reason required, writes a `user_session_revoke` audit row with the captured session metadata in `before_json`.
  - **Force-rotate API keys.** `POST /v1/admin/users/{id}/api-keys/rotate-all` revokes every API key on the target account. Idempotent on zero keys; reason required; writes a `user_api_keys_rotate_all` audit row. The user reissues replacements from their own settings — admins never touch fresh secret material.
  - **Search by signup IP.** `GET /v1/admin/users?signup_ip=<ip>` exact-match filter for abuse triage when one address shows up across multiple complaints. Partial / CIDR matching intentionally absent so operators don't accidentally surface broad swaths of accounts behind a single NAT.
  - **Bulk approve.** `POST /v1/admin/approvals/bulk-approve` approves a batch of pending users in one request (max 200 ids). Per-user errors (not found, already active) are reported in `results` rather than 4xx-ing the whole call, so partial success works when one row in the operator's selection has gone stale. Each approval still writes the same per-user `user_approve` audit row as the single-approve endpoint.
- **Soft-ban with auto-restore.** `POST /v1/admin/users/{id}/suspend` puts an account into the `suspended` state with an optional `duration_days` (1-1825) and a required reason; omit the duration for an indefinite suspension. The endpoint revokes all of the user's active sessions atomically, and the login + auth gates refuse SUSPENDED users with a detail string that surfaces the reason and expiry. A new `unsuspend_expired` background job restores expired suspensions automatically and writes a `user_unsuspend` audit row with `admin_user_id` NULL so the sweep is distinguishable from a manual unsuspend. The auth dep also treats past-expiry suspends as effectively ACTIVE so a returning user isn't wedged in the gap between expiry and the next sweep tick. `POST /v1/admin/users/{id}/unsuspend` lifts a ban early. Suspending an admin account is refused with 409.
- **Admin dossier export (GDPR Article 15).** `POST /v1/admin/users/{id}/dossier` returns a downloadable JSON bundle of the metadata Sheaf holds about an account: identity, system state, structural counts, API-key metadata (names / scopes / timestamps — never hashed keys), active sessions, trusted devices, client settings, email delivery state, admin audit history, and recent import / export job summaries. For DSAR cases where the affected user can't request portability themselves (locked-out account, deceased user with next-of-kin asking). Distinct from `/v1/export` which is the Article 20 portability path and ships member / journal / message *content*. Reason required; writes a `user_dossier_export` audit row. Deliberately omits decrypted member / journal / message content so admins don't get a backdoor to user-content via the DSAR path.
- **Permanent ban.** `POST /v1/admin/users/{id}/ban` is the permanent companion to soft-suspend. Sets `account_status=BANNED` and revokes all active sessions atomically. Differs from suspend in two ways: there is no auto-restore sweep (the only path back is `/unban`), and the auth detail string is just "Account banned" with no reason or expiry surfaced to the user (the reason lives in the audit row for operator reference). Escalating an existing soft-ban to a permanent ban clears the stale `suspended_until` / `suspended_reason` fields so a later reader of the row isn't misled. Banning an admin account is refused with 409. `POST /v1/admin/users/{id}/unban` lifts the ban back to ACTIVE.

### Changed

- **Internal review followups.** Mostly performance and infrastructure tightening. No user-visible API changes:
  - `/fronts/current` previously walked per-(front, member) chains with a sequential awaited query per step, capped at 500 round-trips per pair. Replaced with a single recursive CTE that walks every chain in parallel. Same depth cap, same `capped` flag semantics.
  - `board_summaries()` (Members tab + sidebar, called 3x around front-start) previously loaded every live message into Python and aggregated there. Replaced with a single SQL aggregation that returns counts, latest body, and per-board unread counts via a LEFT JOIN against `message_read_state`. Lazy-create of baseline read-state rows still happens for first-time viewers but now batches across boards.
  - Pillow `normalize_image()` (avatar / bio upload re-encode) is pure-CPU and was blocking the worker event loop. Now offloaded to the default starlette thread pool via `run_in_threadpool`.
  - `pending_finalize_after_by_target()` (called on every list endpoint to surface `pending_delete_at` badges) now short-circuits to `{}` when the caller's system has `safety_grace_period_days == 0`. Callers updated to pass the `System` object directly.
  - `list_polls` no longer eager-loads every vote when results are hidden. Per-vote detail and tally live on the detail endpoint; the list endpoint returns aggregate totals only via a `GROUP BY` count.
  - Thread-delete previously did per-id `get + delete` in a loop. Now bulk-deletes via `WHERE id IN (...)` after walking the cascade.
  - Channel enable / disable previously did `_load_owned_channel` twice (before and after mutation). Now mutates the loaded row in place and `db.refresh()` once.
  - `JWT_SECRET_KEY` previously had a hardcoded `changeme-in-production` default that selfhosters only got a warning about. Now auto-generates and persists to the data dir on first start (mirroring the encryption-key pattern). SaaS mode still refuses to start with the default.
  - `nginx/dev-tls.conf.template` previously had no security headers and `client_max_body_size 10m` (below the app's 110MB cap, which silently truncated imports at the proxy). Added HSTS / X-Content-Type-Options / X-Frame-Options / Referrer-Policy headers and bumped the body size to match the app.
  - Localdev MinIO now binds to loopback by default (matching the db / redis posture) and the init container no longer sets the bucket anonymous-download (the app uses signed URLs, so the public policy was both redundant and contradicted the privacy posture).

## [0.3.2] - 2026-06-03

### Added

- **Select and multi-select custom field types on web.** The backend `FieldType` enum and mobile apps already supported these types, but the web frontend only listed text/number/date/boolean and rendered every field value as a plain text input. The custom-fields settings card now offers all six types, lets you define a list of valid choices for select/multiselect at create time (or leave choices blank for freeform tagging — same behaviour the mobile apps default to), and supports per-choice editing on rename. Member values render with type-aware widgets: number input for numeric, date picker for dates, checkbox for booleans, dropdown for select, checkbox group for multi-select. When a field defines an explicit list of choices, the server now rejects submitted values that don't match — applies across all clients (web, iOS, Android).
- **"Show technical error details" setting.** New Advanced tab in Settings with a toggle that swaps friendly error toasts for the raw HTTP status code and backend error message. Off by default; useful for reporting bugs or diagnosing flaky network paths. Backend-stored so the choice follows the account across browsers.
- **Private PyPI mirror support in the backend Docker build.** Selfhosters running a local PyPI cache or mirror (proxpi, devpi, Artifactory, etc.) can now point the backend pip install at it via the optional `PIP_INDEX_URL` / `PIP_EXTRA_INDEX_URL` build args, both empty by default. Drop one into the gitignored `.env` next to `docker-compose.yml` and rebuild — same flow as the existing `INCLUDE_DEV_TOOLS` knob. Documented under "Optional dependencies" in `docs/SELFHOSTING.md`.

### Changed

- **Polls no longer require voters to be fronting by default.** Voting is now open to any system member regardless of front state, matching the journals authoring model (an author doesn't need to be in the current front either). Polls that want the fronter-only gate ("what should we wear today") opt in via a new "Restrict voting to current fronters" checkbox at create time. Existing polls in v0.3.1 betas adopt the new permissive default; create restricted polls when you want the old behaviour. The custom-front exclusion (`include_custom_fronts`) still applies independently — system-state members like Asleep / Away can't vote unless that flag is on.
- **Friendly error toasts.** Failed API calls now show a status-aware summary ("Not found.", "Slow down — too many requests.", "Server error — please try again.") instead of either a generic "Server error" or the raw backend detail. Inline error messages (red text under forms in the TOTP, password change, email change, import, and System Safety surfaces) follow the same toggle. Operators or bug reporters can opt back into raw detail via the new Advanced setting.
- **Status codes audit.** Several endpoints that returned `400 Bad Request` for state mismatches ("Not pending" on already-cancelled trim notices, system-safety pending actions, and pending changes) now correctly return `409 Conflict`. Cosmetic raw-int `status_code=404`/`400` raises across the API layer were swapped for the named `status.HTTP_*` constants for consistency. No client-visible behaviour change beyond the 400→409 swaps above.

### Fixed

- **Sheaf-to-Sheaf import no longer attaches another account's images.** When a Sheaf JSON export was imported into a different account (e.g. cloning a member roster to a fresh account on the same instance), the importer copied hosted `avatar_url` values and bio image references verbatim. The new account ended up with `/v1/files/...` references pointing at the original account's storage — silently borrowing blobs it did not own, invisible to quota tracking, and at risk of breaking the moment the original account triggered orphan cleanup. The importer now strips any internal storage references (avatars, bio image embeds, journal image keys) during the JSON import path; external image URLs (Gravatar, Imgur, etc.) are preserved unchanged. Restoring images on a Sheaf-to-Sheaf migration now requires re-uploading them on the new account; the proper fix is the planned export-with-images zip format that ships blob bytes alongside the JSON.

### Security

- **Bumped `aiohttp` from 3.13.5 to 3.14.0** for an upstream security fix. See the [aiohttp 3.14.0 release notes](https://github.com/aio-libs/aiohttp/releases/tag/v3.14.0) for details.
- **Bumped `react-router` from 7.13.1 to 7.15.0** to pick up upstream fixes. See the [react-router 7.15.0 release notes](https://github.com/remix-run/react-router/releases/tag/react-router%407.15.0) for details.

## [0.3.1] - 2026-06-02

### Added

- **Avatar and bio image cropper.** Picking a file for an avatar or a bio embed now opens a crop dialog. Avatars get a 1:1 aspect lock and a circular preview mask, so what you see in the cropper is exactly what the round avatar component will render. Bio embeds use a freeform rectangular crop. Both support zoom and rotation. Drag-and-drop avatar uploads route through the cropper too.
- **Server-side image normalization.** Every upload now decodes through Pillow, gets its longest edge capped to `MAX_IMAGE_DIMENSION` (default 4096 px), has EXIF and ICC metadata stripped, and is re-encoded into a clean container before storage. Closes a privacy leak (phone photos retained GPS) and adds defence in depth against decompression-bomb uploads (the cap is on declared decoded bytes, not on-disk size, and runs before Pillow asks for the pixel data).
- **Animation gate for animated avatars.** Animated GIF and animated WebP uploads now flatten to their first frame by default. A new master switch `ALLOW_ANIMATED_UPLOADS` plus a per-user `can_upload_animated_images` flag (settable from the admin UI) opt selected accounts back in. The hook is wired through to the upload pipeline so a future tier-based rollout (e.g. "animated avatars on the Plus tier") needs no callsite changes.

### Fixed

- **Journal entry editor placeholder.** The body field no longer reads "Write a bio..." - it now matches its context.

## [0.3.0] - 2026-06-02

### Added

- **Prometheus-compatible `/metrics` endpoint.** Sheaf now exposes a full set of application metrics: HTTP RED (request volume, latency, in-flight), the auth funnel broken down by outcome (password incorrect, locked, TOTP required/invalid, recovery code used, trusted-device bypass, etc.), rate-limit hits per bucket, captcha lifecycle, lockout events, notification dispatch per channel type and outcome including enqueue-to-dispatch lag, outbox depth and oldest-pending age, per-kind email sends, SendGrid feedback events, cf-shield engagements with a current-state gauge, scheduled-job runs with duration and consecutive-failure tracking, imports and exports by source/outcome, System Safety pending-action grace, decrypt failures per field, tier-limit hits per limit and account tier, DB query duration bucketed by operation, S3 op counters per bucket, plus base data-shape gauges (users, systems, members, active sessions, trusted devices, DB pool, Redis up). The endpoint defaults to a separate listener on `127.0.0.1:8090` with no auth (safe for single-node deploys scraped over loopback or a private network), with `METRICS_BIND=main` + bearer-token auth available for shared-listener and remote-scrape setups. Per-IP and per-account request rates are surfaced as histograms-of-rates so the distribution is visible without ever putting an IP or account ID into a label. Multi-worker uvicorn is supported out of the box via `PROMETHEUS_MULTIPROC_DIR`. Fast-moving signals (Redis up, DB pool, outbox depth) refresh on a dedicated 10s loop so up/down detection isn't bounded by the slower per-15-minute DB-counts pass. Full catalog, cardinality rules, and scrape configuration examples in `docs/METRICS.md`.

## [0.2.4] - 2026-05-30

### Added

- **Opt-out of CDN proxying during DDoS mitigation.** A new Privacy toggle in Settings -> Account lets users refuse Cloudflare proxying when the operator engages DDoS mitigation. When set, the user's sessions are ended the moment mitigation engages; they cannot sign back in until mitigation clears (the direct origin is closed for the duration of the incident). The toggle only renders on instances where the operator has wired the feature via `SHIELD_MODE_ENABLED` and a webhook secret. A new public `GET /v1/shield-mode/status` endpoint exposes the current posture for API and mobile clients that want to honour the preference voluntarily. While mitigation is active, a non-dismissable banner appears across every page (logged-in or not) explaining that traffic is currently routed through the CDN.
- **Pending-delete badges across every listing.** Members, groups, tags, custom fields, fronts (current and history), journal entries, polls, board messages, watch tokens, notification channels, and uploaded images now show a "Pending delete" badge and dim styling whenever they're sitting in System Safety's grace window for deletion. Uploaded images additionally get a corner warning marker on the thumbnail; the detail modal's Delete button is disabled while a delete is already queued, with a tooltip pointing to Settings -> Safety where the queued action can be cancelled.

### Changed

- **Announcement banner readability.** The title and body of an announcement banner now use stronger weight contrast and a middle-dot separator so the two pieces no longer read as a single run-on sentence.

### Fixed

- **Notification outbox no longer grows unbounded.** Delivered notification rows are now swept on a regular schedule so the outbox table stays bounded over time; previously they accumulated indefinitely with no cleanup.

## [0.2.3] - 2026-05-26

### Added

- **File references view.** Selecting an uploaded image now shows where it's used (system avatar, member avatars and bios, journal entries, and edit history) with deep links to each, so you can see what a delete would break before confirming. An image referenced only by old revisions is flagged as safe to delete (orphan cleanup leaves those in place).
- **Dedicated board-message API-key scopes.** Board messages now have their own `messages:read` / `messages:write` / `messages:delete` scopes instead of reusing `members:*`, so an API key can be granted message access independently of member access.

### Changed

- **Imports enforce the member cap.** An import (Sheaf, PluralKit, SimplyPlural, Tupperbox) that would push the account past its member limit now fails up front instead of silently overshooting, and the import screen warns and disables the button before you start. New `GET /v1/members/limit` backs the warning.
- **Modals are less cramped on larger displays.** Default dialog width now scales to `max-w-2xl` (672px) at `lg+` viewports instead of staying at `sm:max-w-lg` (512px), and the member picker caps its height at 40% of the viewport with its own scroll so the surrounding controls (start-front Start button, group/tag editor save) stay visible no matter the roster size.

### Security

- **API keys can no longer manage API keys.** The create / list / revoke key endpoints refuse API-key auth, so a leaked key can't mint a wider-scoped key or revoke others; key management is session/JWT only, matching the account and async-export endpoints. The unused, drifted internal scope list was removed in favour of a single source of truth, with a test asserting every scope the API enforces is actually grantable.

### Fixed

- **Re-import no longer duplicates custom field definitions.** Restoring a Sheaf export into a system that already had those fields stacked a second copy of every definition ("Pronouns", "Pronouns", ...). The importer now dedupes definitions by (name, type) against the target system and within the file, reusing the existing one; members and their values are still added (member dedup is a separate, larger piece of work). Field values guard the `UNIQUE(field_id, member_id)` constraint so a shared definition can't trip it mid-import.
- **Image delete now prompts for step-up auth.** When the system's delete-confirmation tier requires a password or TOTP, deleting an uploaded image prompts for it instead of failing with "TOTP code required" and no way to supply it. The System Safety grace period is honoured too (scheduled-deletion toast). A sweep confirmed image delete was the only destructive action whose frontend bypassed the step-up prompt.
- **Poll creation over an API key returned 403.** The `polls` scope is enforced server-side but was missing from the key-creation UI, so a key could never be granted it. The scope is now offered in the picker.
- **Re-issue activation link for mobile_push channels.** The channel detail page only showed the Re-issue button for `web_push`, leaving mobile-push channels in `pending_registration` (notably re-imported ones, which intentionally don't carry the original activation hash across) with no UI path to a fresh magic link. The backend already accepted either; the frontend gate now matches.

## [0.2.2] - 2026-05-24

### Fixed

- **Sheaf import completeness** The importer only consumed system / members / fronts / groups / tags / custom fields, so journals (and their edit history), board messages, polls, reminders, and the notification config (watch tokens + channels + filter rules) silently vanished on re-import even though the export had always carried them. The importer now round-trips all of them, remapping cross-references (revision targets, poll option/vote refs, channel group/member rules, reminder channel + scope members) onto the freshly minted IDs. The import screen gains per-section selectors (journals, messages, polls, reminders, notifications), and the preview shows a count for each.
  - Restored references: journal/revision authorship re-points at the importing user, the poll audit-log actor is nulled (old-instance account UUIDs are meaningless on the target), notification channels land in `pending_registration` so nothing dispatches to external recipients until the owner re-activates, and `delete_confirmation` is intentionally not restored (it would otherwise lock destructive actions on an account without the matching TOTP enrolment). Reminders attach to a channel, so they ride the notifications toggle.
- **Export download failed on KMS-encrypted buckets.** S3 only serves a presigned `GET` for an SSE-KMS-encrypted object (including objects covered by a bucket-default KMS policy) when the URL is signed with SigV4; the boto3 clients weren't pinning a signature version and could fall back to SigV2, so the download 403'd with "requests specifying Server Side Encryption with AWS KMS managed keys require AWS Signature Version 4". Both the export-artefact and image storage clients now pin `s3v4` (harmless for non-KMS buckets and MinIO).

## [0.2.1] - 2026-05-24

### Fixed

- **Messages page 500s under concurrency.** Opening a board fires several endpoints in parallel (board list, board contents, mark-seen, unread badge), each of which calls `get_or_create_read_state`. The plain select-then-insert raced: per-member boards hit the unique index (500), and the system board (`board_member_id` NULL) silently accumulated duplicate rows because Postgres treats NULLs as distinct in a unique index, later breaking `scalar_one_or_none`. Now uses `INSERT ... ON CONFLICT DO NOTHING` then selects the winner, and the `ix_message_read_state_lookup` unique index is rebuilt with `NULLS NOT DISTINCT` (PG15+) so the system board is covered too. A migration dedupes existing read-state rows first, keeping the most-recently-seen row per member/board.

## [0.2.0] - 2026-05-23

The pre-public-beta hardening release. On top of the features that landed since v0.1.0 (front-change notifications, reminders, polls, messages, journals/notes, mobile push, PluralKit/Tupperbox/SimplyPlural import, analytics, custom fronts), this cycle moved imports onto a background job runner, did a broad security/privacy and performance pass, and added the first quick-switch building block.

### Top-fronters quick-switch endpoint

`GET /v1/members/top-fronters?limit=N` ranks members for a quick-pick list, so UIs (and the mobile apps) can autopopulate a start-front shortcut with the people most likely to be picked.

- Recency-weighted score: per-member sum of fronting seconds with exponential decay (30-day half-life) over a 180-day window. Co-fronting counts for every participant, matching the analytics aggregator.
- New nullable `members.quick_switch_pin`. Pinned members sort ahead of the recency ranking, ascending by pin value; everyone else follows by score.
- Member create/edit form gains a "Pin to quick-switch" toggle; the start-front dialog shows a one-tap quick-pick chip row fed by the endpoint.
- The pin round-trips through Sheaf export and re-import.

### Imports moved onto an async job runner

All five import paths (PluralKit file, PluralKit API, Tupperbox, SimplyPlural, Sheaf re-import) now run as background jobs instead of blocking the request, so a large or slow import no longer ties up a connection or times out.

- New `import_jobs` table. `POST /v1/imports/file` and `/v1/imports/api` return `202` with a pollable job; `GET /v1/imports` (cursor-paginated) and `/v1/imports/{id}` expose status, per-record events, and counts. New `/imports` + `/imports/{id}` report UI.
- Idempotency key dedupes double-submits (the double-clicked upload). Uploaded payloads are stored off-row and wiped on finalize; API credentials (PK token) are wiped once consumed.
- File-bounds + JSON-size hardening on parse. A stale-running-job recovery sweep resets jobs orphaned by a worker crash.
- PluralKit API preview: transient connect-retry, a busy spinner, and server-side logging of upstream API errors.

### Security and privacy hardening

- **SaaS signup tier**: new registrations in SaaS mode now default to the `free` tier instead of inheriting the model's `self_hosted` default, so member-count and storage-quota limits actually apply to new accounts. Self-hosted instances are unaffected (signups stay `self_hosted`).
- **SendGrid webhook**: verifies the Signed Event Webhook ECDSA signature with a timestamp replay window (`SENDGRID_WEBHOOK_PUBLIC_KEY`, optional `SENDGRID_WEBHOOK_MAX_SKEW_SECONDS`). The legacy query-string token is a fallback used only when no key is configured.
- **Unified lockout**: failed-attempt lockout is now shared across login, TOTP disable, recovery-code regeneration, and the account-data endpoint, so attempts can't be spread across endpoints to dodge it. Per-user rate limits added to those plus the anonymous notification redeem / preview / manage endpoints.
- **Password reset**: closed a timing oracle (the send now runs as a background task with symmetric work on the no-match branch); reset tokens are invalidated on password change and on successful login.
- **Destructive-auth TOTP gate**: a TOTP-requiring confirmation tier can no longer be set without TOTP enrolled, TOTP can't be disabled while such a tier is active, and the verifier fails safe to a password check for legacy misconfigured rows.
- **Races**: export-job and poll creation are row-locked; delete-account recovery-code consumption uses the race-safe conditional update; the account-deletion / pending-action / reminder background sweeps claim rows with `FOR UPDATE SKIP LOCKED`.
- **Storage integrity**: the export-ready email now sends to the decrypted address (it was sending ciphertext); a failed upload deletes the orphaned blob; account deletion defers when storage cleanup is incomplete rather than dropping the row and orphaning blobs.
- **Frontend**: avatar URL scheme allowlist (blocks `javascript:` / `data:` / `file:`); one-shot tokens (email verification, password reset, notification activation) stripped from the address bar after capture; cross-tab logout via BroadcastChannel; admin-reset passwords and regenerated TOTP recovery codes auto-clear from the DOM after a timeout.
- Admin change-email normalizes the address before indexing; docker-compose binds the Postgres and Redis published ports to loopback by default (`POSTGRES_BIND_HOST` / `REDIS_BIND_HOST` to override).

### Performance, correctness, and schema

- **Pagination**: the journals list moved to an opaque `(created_at, id)` cursor and board messages gained an id tiebreaker, so entries sharing a timestamp can't be skipped or duplicated across a page boundary.
- **N+1**: the board-message list and the repeated-reminder tick batch their lookups; `/admin/users` paginates in SQL and decrypts only the current page instead of the whole table.
- **Client settings**: atomic-merge `PATCH` so independent writers (front prefs, dismissed announcements, onboarding) no longer clobber each other.
- **Schema**: `UNIQUE(field_id, member_id)` on custom field values (with a dedup migration); indexes on `uploaded_files.user_id` and the member side of the association tables; the redundant single-column journal index dropped; server defaults added to JSONB / status columns on `pending_actions`, `safety_change_requests`, and `client_settings`; the `Group.children` ORM cascade matched to the `ON DELETE SET NULL` foreign key.

### Accessibility

- Form labels associated with their inputs (`htmlFor` / `id`) across ~21 settings, dialog, and route components, so screen readers and click-to-focus behave the same way they already did on the login and register pages.

### Developer & ops

- Alembic autogenerate now compares column types and server defaults, so schema drift isn't silently missed.
- `run_tests.sh` fails hard if the database-backed endpoint never warms up, instead of proceeding and masking a pool-warmup bug as flake.
- Added test coverage for login lockout, SendGrid webhook signature verification, client-settings merge, journals cursor pagination, and the top-fronters ranker.
- README documents `SHEAF_ENCRYPTION_KEY` as a distinct backup target; the self-hosting backup section now covers encrypting the dump, off-host rotation, separating the key from the database backup, and testing restores.

### Unified `mobile_push` channels (collapse of fcm / apns_dev / apns_prod)

The platform-specific mobile destination types had to be picked at channel creation, even though the owner didn't know which OS the recipient was on. Replaced with a single `mobile_push` type that binds to a Sheaf account at redemption and fans out across every `push_device_tokens` row for that account at delivery time — one channel rings every device the recipient has signed into, iOS or Android.

Breaking change (pre-GA, mobile app coordinated):

- New `DestinationType.MOBILE_PUSH` value. The legacy `fcm` / `apns_dev` / `apns_prod` values stay in the Python enum + API Literal type so read-back of historical audit / export rows still validates, but channel creation refuses them with a message pointing at `mobile_push`.
- Migration `f2g3h4i5j6k7` collapses any existing channel rows to `mobile_push`.
- Per-channel platform gating + `APNS_DEV_ENABLED` channel-level enforcement removed (the flag still gates apns_dev *device-token* registration, which is the orthogonal "don't accrue sandbox tokens on a prod backend" concern).
- Dispatcher's `_deliver_mobile_push` drops the `platform` parameter and queries all of the account's device tokens, routing each to the FCM or APNs handler based on the device row's own `platform`.
- Owner-side UI: one "Mobile push (iOS + Android)" option in the channel-create dialog replaces the three platform-specific rows.
- Mobile app coordination: app reads its API target from `/v1/version` at first-run; deep-link domain (`sheaf.sh/redeem` via Universal Links / App Links) is built into the published app. Self-hosters route through `sheaf.sh/redeem?code=...&instance=https://...` or use the `sheaf://` custom-scheme fallback.

### Device list management in the Receiving tab

A recipient can now see and manage every device registered to their account from the Receiving tab.

- `push_device_tokens` gains `enabled` (bool, default true) and `label` (optional 80-char user-visible device name set by the mobile app at registration).
- New endpoints: `PATCH /v1/devices/push/{id}` to toggle `enabled` or rename the device; `DELETE /v1/devices/push/{id}` to remove a device from the web UI (the existing token-based DELETE stays for the mobile app's logout flow).
- Dispatcher's mobile-push fan-out skips rows where `enabled = false`, so a recipient can mute one device (e.g. the work phone over the weekend) without unregistering it entirely.
- Receiving tab gets a "Your devices" card with one row per registered token: label (rename inline), platform badge, last-seen-at, on/off checkbox, remove button.

### Recipient label fix: "Paused by sender" vs "Unsubscribed"

Owner-paused channels and recipient-unsubscribed channels both flipped `destination_state` to `disabled`, and the recipient UI labelled them both as "Unsubscribed" — confusing in the owner-paused case ("but I didn't unsubscribe").

- New `paused_by_sender` column on `notification_channels`. Owner pause sets it to true; re-enable clears it; recipient unsubscribe leaves it false (its default).
- Exposed on `ChannelRead`, `ReceivingChannelView`, and `ManageChannelView`. Receiving-tab list and manage-link page render "Paused by sender" (amber) when the flag is set, "Unsubscribed" (muted) otherwise.
- Manage-page copy distinguishes the two: the paused branch tells the recipient that subscription will resume automatically when the sender does, with a pointer to ask for removal if they want to opt out permanently.

### Step-up auth denials return 403 instead of 401

Bug surfaced when deleting a notification channel under a system with `delete_confirmation=password` (or `totp` / `both`): a wrong password returned `401 Incorrect password`, which the frontend's `apiFetch` interpreted as "access token may be stale" and silently kicked off the refresh-and-retry path. Refresh succeeded but the retried DELETE still came back 401 (same wrong password) — and the second 401 was swallowed by a `resp.status !== 401` guard meant to avoid double-toasting during normal refresh dances. End result: user clicks Delete, nothing visibly happens.

Fix is two-sided:

- **Backend**: every "user is authenticated, step-up credential is wrong" path now raises **403** instead of **401**. 401 means "authenticate"; 403 means "you are authenticated but can't do this action". The wrong-credential case is the latter. Sites changed: `services/system_safety.verify_destructive_auth` (used by member / channel / front / journal / group / tag / poll / message / front-entry / safety-setting deletes), `api/v1/admin.do_step_up`, `api/v1/systems.update_delete_confirmation`, `api/v1/account.account_data`, `api/v1/export.create_export_job`, `api/v1/auth.request_account_deletion`. The "credential not provided" branches stay 400 (or 422 for admin, matching the existing per-site style).
- **Frontend**: `apiFetch` and `apiFetchWithHeaders` now distinguish "pre-retry 401" (suppressed; the silent refresh handles it) from "post-retry 401" (surfaced via toast like any other error). A defensive measure on top of the backend fix — covers any future bug where a 401 survives the refresh dance.

Test asserts updated in `test_system_safety`, `test_admin_step_up`, `test_account_deletion`, `test_account_export_completeness` to expect 403 on the wrong-credentials paths.

### Pagination for the fronts history (cursor + numbered, with toggle)

`GET /v1/fronts` was paginated by `limit` + `offset` but had no way to tell a caller "there's more" - so the frontend silently rendered only the first 50 entries and anyone with a longer history was truncated without warning. Now there's an explicit signal, plus a real numbered-pages UI for the people who don't want infinite scroll.

Non-breaking backend additions:

- New `cursor` query param (alternative to `offset`); when set, `offset` is ignored.
- New `include_total` query param (opt-in) - adds `X-Sheaf-Total-Count` header. Off by default since it costs one extra `COUNT(*)`; the numbered-pages UI opts in, the cursor / "load more" path doesn't.
- New response headers:
  - `X-Sheaf-Has-More: true|false` on every response.
  - `X-Sheaf-Next-Cursor: <opaque>` when more results exist; absent otherwise.
  - `X-Sheaf-Total-Count: <int>` when `include_total=true`.
- Cursor is base64url JSON of `{started_at, id}`, opaque to callers. Server uses Postgres row comparison `(started_at, id) < (cursor.started_at, cursor.id)` with a matching `ORDER BY started_at DESC, id DESC` for stable pagination across ties.
- Cursor-mode has-more detection uses a `limit + 1` probe rather than a separate count, so the response time stays flat regardless of total history length.
- Existing `offset`-only callers (notably the mobile app in app-store review) are unaffected; the new headers just provide extra info they can ignore.

Frontend `/fronts` history now supports two views, toggleable via a small icon group in the History header and persisted to the URL search params:

- **Infinite (default)**: `useInfiniteQuery` with cursor pagination + Load older entries button. No URL state.
- **Numbered pages**: opt-in via the toggle (or `?view=paged` directly). Renders First / Prev / 1 2 3 ... N / Next / Last navigation with "Page N of M" and "X entries" indicators. Page + page-size live in URL search params (`?view=paged&page=3&pageSize=50`) so refresh / bookmark / share preserve position. Per-page selector offers 25 / 50 / 100.

Per-user persistence: view mode + page size persist to `client-settings/web` under a `fronts` key, so toggling once sticks across sessions. URL still wins when present (bookmark / share stays deterministic); settings just supply the default on bare `/fronts` visits. Page number itself is transient and not persisted.

### In-browser "Verify this page" button + attestation links

Two enhancements on the `/about` page's verifiability surface:

- **Verify this page**: a button in the Bundle integrity card that re-fetches every file in `build-manifest.json`, computes its SHA-384 in the browser via the Web Crypto API, and compares to the manifest's recorded integrity. Renders per-file pass / fail / unreachable inline with a running summary. The hash function and comparison both live in the browser, so the server can only influence the bytes it serves - which is the thing being checked. Covers `index.html` too, closing the SRI bootstrap gap for verification (though browser-enforced loading of `index.html` against a hash still needs the cosign-attested manifest path). Previously the docs said "open devtools and eyeball it"; that's now a click.
- **Attestations & transparency card**: direct links to the GHCR package pages (backend image, frontend image - both expose cosign signatures, SBOM, and the build-manifest predicate via the GitHub UI), to the Rekor transparency log search, and to the current build's GitHub release page when a tag is present. Cross-checking what the instance reports against what CI actually published no longer requires hunting around.

Also updated `docs/VERIFYING.md` to lead with the in-browser button before the manual devtools workflow.

### Fix flaky import tests: commit before responding

`run_import` in both the PluralKit (`sheaf/services/pk_import.py`) and Tupperbox (`sheaf/services/tb_import.py`) importers wrote rows via `db.flush()` but never called `db.commit()` inside the handler. They relied on the auto-commit at the end of `get_db`, which for FastAPI `yield` dependencies runs *after* the response has been delivered to the client. A test (or any client) firing a follow-up request immediately after a 200 OK could race that cleanup-commit and see an empty members list, manifesting on CI as a `KeyError: 'Alice'` in `test_import_resolves_visibility_to_privacy_enum`. The race window is microseconds on a fast local machine and milliseconds on slow CI runners; rerunning usually won the race. Fixed by committing explicitly at the end of both importers' `run_import`, matching the convention every other write endpoint in the codebase already follows.

Also hardened the `_upload` test helper in `tests/test_pk_import.py` and `tests/test_tb_import.py` to assert `2xx` on the response so any future server-side failure surfaces as a clear assertion failure instead of a downstream `KeyError`.

### Grey out history buttons on entries without history

Small UX polish across every surface that has an edit/audit/revision history button. Before, the button was always enabled, so you'd click it and find an empty list. Now the button is disabled and dimmed when nothing's there, so you can see at a glance which entries have actually been edited.

- **Fronts** (`/fronts` - both Currently-fronting and History): the per-entry History toggle is disabled until the entry has at least one audit row. Backed by a new `has_audit_history` boolean on the `FrontRead` API shape, populated via a single batched `EXISTS` query per list (no per-row round-trip).
- **Members** (bio history modal): the History button in the member detail dialog is disabled until the bio has been edited at least once. Backed by `has_bio_revisions` on `MemberRead`, same batched-`EXISTS` pattern. Nested contexts (tag / group member lists) default to `false` since the modal is opened from the members route; if you need the accurate value, fetch from `GET /v1/members` or `/v1/members/{id}`.
- **Messages**: the History button is disabled when `updated_at` equals `created_at` (no edit has happened yet). No backend change needed - the existing "(edited)" indicator already relies on this signal.
- **Journal entries**: the Revisions button is disabled when `revision_count === 0`. No backend change needed - the existing single-entry endpoint already returns the count.

### Mobile push redemption accepts Bearer auth

`POST /v1/notifications/redeem` only consulted the `sheaf_session` cookie when resolving the redeeming account, so mobile clients (which authenticate with `Authorization: Bearer <jwt>` and carry no cookies) failed the mobile-channel "login required" gate and got 401 even with a valid token. The Android app's retry-on-401 logic compounded the issue into a refresh loop.

Fixed by switching the endpoint to `get_current_user_optional`, which accepts either a session cookie or a Bearer access token. Web push redemption is unchanged (auth was always optional there); mobile push redemption now works from both the web fallback (cookie) and the native deep-link flow (Bearer).

### apns_dev sandbox tokens gated behind explicit opt-in

The mobile push backend accepts two APNs environments (`apns_dev` for Xcode-built sandbox installs, `apns_prod` for TestFlight / App Store). Both authenticate with the same `.p8` key, but their tokens are not interchangeable: a sandbox token registered against a prod backend would bounce at the APNs host at delivery time, leaving an orphaned `push_device_tokens` row on a real production account.

Added `APNS_DEV_ENABLED` (default `false`). When off, both `POST /v1/watch-tokens/{id}/channels` with `destination_type=apns_dev` and `POST /v1/devices/push` with `platform=apns_dev` refuse the request (501 and 400 respectively) so prod deployments never see dev rows. Dev / staging / self-hosted-with-TestFlight setups flip the flag on.

### PATCH endpoints reject explicit null on NOT-NULL columns

Bug fix uncovered while testing on the test instance: `PATCH /v1/systems/me` with `date_format: null` (or any other NOT-NULL column nulled out) crashed with a 500 `NotNullViolationError`. The `| None = None` shape that every Update schema uses to enable "presence-in-body" PATCH semantics also allowed clients to send explicit `null`, which the handler then setattr'd onto the model and pushed to the DB.

Fixed by adding `field_validator` rejections at the schema layer for every NOT-NULL column on every Update schema where the handler doesn't already defensively ignore `None`. Clients now get a clean 422 instead of a 500. Validators don't run on default values in Pydantic v2, so the "omit to keep" semantics is unchanged — only explicit `null` for required fields is newly rejected. Affected schemas: `SystemUpdate`, `MemberUpdate`, `GroupUpdate`, `TagUpdate`, `CustomFieldUpdate`, `AnnouncementUpdate`, `ReminderUpdate`, `ChannelUpdate`, `SystemSafetyUpdate`, `FrontUpdate`. `JournalEntryUpdate` and `UserUpdate` were already None-tolerant at the handler layer and don't need the schema-level check.

### Edit front entry + audit log

SP parity for editing past front entries. Each explicit edit now appends an audit row to `front_audit_events`, capturing who did it, when, what was at front at the time, and a full pre/post snapshot.

- **Extended `PATCH /v1/fronts/{id}`.** Now accepts `started_at` (new), plus the existing `ended_at` (with reopen semantics: send `null` to clear and reopen a closed front), `member_ids`, and `custom_status`. All four use presence-in-body to distinguish "omit" from "explicit set", so a partial PATCH only touches what you sent. Overlap with adjacent entries is allowed (SP parity: front history is self-reported state, not a system-enforced timeline); the only timeline impossibility rejected is `ended_at` strictly before `started_at`.
- **Audit log.** New `front_audit_events` table — append-only, one row per explicit edit. Stores `actor_user_id`, `fronting_member_ids` (the system-wide currently-fronting set at the moment of the edit, mirroring polls' fronting snapshot), and `before_snapshot` / `after_snapshot` JSONB columns holding the full entry state (member ids, started_at, ended_at, custom_status — encrypted at rest exactly as on the live row). `ON DELETE CASCADE` on `front_id` means the audit log is bound to the entry: purging a front (retention, manual delete) takes its history with it.
- **No audit for system-driven edits.** Auto-end on `replace_fronts=true` and any other implicit mutation does **not** write an audit row; only explicit `PATCH` calls do. No-op PATCHes (empty body, or body that doesn't actually change the snapshot) also skip the row.
- **No System Safety gating on edits.** Edits are mutating but not destructive — the audit log itself is the safeguard. Front-entry deletion still goes through System Safety unchanged.
- **API**: `GET /v1/fronts/{id}/audit` lists audit rows newest-first, gated by `fronts:read`. Ownership is verified via the live front row; other systems' entries 404 (not 403, to avoid leaking existence).
- **Frontend**: Edit button on each entry (both Currently-fronting and History) opens a dialog with member-set / started_at / ended_at / reopen-toggle / custom_status fields. History toggle (clock icon) on each entry expands inline to show a chronological audit list with per-row "Members: X → Y", "Started: A → B", etc. diffs.

### Mobile push notifications (FCM + APNs)

Backend wiring so the iOS and Android apps can receive front-change pings (and any other channel-driven notification surface) directly via the OS push providers, on top of the existing notification-channels machinery.

- **Account-anchored, not channel-anchored.** Mobile push tokens rotate (app reinstall, OS-side housekeeping, clear-data). Treating them like web push subscriptions and storing the token on the channel would orphan every subscription on every rotation. Instead, devices register their token against the logged-in account once via `POST /v1/devices/push`, and channel fan-out at delivery time looks up `push_device_tokens` rows matching the channel's `redeemed_by_account_id`. Web push retains its existing anonymous-capable flow unchanged.
- **APNs split by environment.** The `DestinationType` enum gains `apns_dev` and `apns_prod` (the placeholder `apns` is replaced). Apple's `.p8` key authenticates against both `api.sandbox.push.apple.com` and `api.push.apple.com`; the dispatcher routes per-device based on the row's platform value, so a single deployment serves both Xcode-built dev installs and TestFlight / App Store users without an env-selecting setting. iOS clients pick which one they have at build time from the `aps-environment` entitlement (not `#if DEBUG` — TestFlight is release config but production APNs).
- **FCM is single-token.** Android tokens have no environment split; the same token works for dev / internal-test / Play Store. Clients always send `platform: "fcm"`.
- **`/v1/devices/push` endpoints.** `POST` registers / refreshes / rotates (via `install_id` matching), `DELETE` drops on logout (idempotent), `GET` lists the account's devices for an in-app management screen. Tokens are never returned by `GET`. Per-account soft cap defaults to 20 rows, oldest-`last_seen_at` evicted on insert when over (configurable via `NOTIFICATIONS_MOBILE_TOKENS_PER_ACCOUNT_MAX`, `0` for unlimited).
- **Redemption requires a session.** Mobile-push channel redemption refuses anonymous traffic (401), refuses `push_subscription` payloads (transport lives on `push_device_tokens`, not the channel), and binds `redeemed_by_account_id` to the redeeming user. No anonymous `/manage` URL is issued — recipients manage via the in-app Receiving screen using the existing `/v1/notifications/receiving/{channel_id}/unsubscribe` endpoint.
- **Dispatch fan-out.** `_deliver_mobile_push` looks up every `push_device_tokens` row matching the channel's account + the channel's platform, dispatches per-device, and aggregates: any-success-is-success. 404 / 410 / `Unregistered` / `BadDeviceToken` responses delete the dead row in-line. Channel itself is not disabled on permanent failures (the user might re-register a device).
- **Configuration.** `FCM_SERVICE_ACCOUNT_PATH` / `_JSON` (path wins) for FCM; `APNS_TEAM_ID`, `APNS_KEY_ID`, `APNS_BUNDLE_ID`, optional `APNS_BUNDLE_ID_DEV` (override for `apns_dev` devices), `APNS_P8_PATH` / `APNS_P8_KEY` for APNs. Each cred is a long-term static secret (no rotation, no state to track), shaped like the existing VAPID keys. Channel creation rejects FCM/APNs with 501 when the relevant credentials are missing — the deployment opts in by configuring them.
- **All tiers.** No tier gating; FCM and APNs are unmetered for the operator at any reasonable scale, so there's nothing useful to gate on (unlike Pushover which has paid app tokens with monthly caps).
- **Payload shape.** Both providers receive a data-only-equivalent payload (FCM: `data: {title, body, event_id}`. APNs: `aps.alert` placeholder + `mutable-content: 1` + custom `data` keys, expecting the iOS client to ship a Notification Service Extension that rewrites the user-visible alert from the data fields). Lets clients format title/body locally per recipient prefs without the server tracking per-recipient display config.
- **Database.** New `push_device_tokens` table (account FK with cascade delete, platform/token/install_id/app_version/last_seen_at, unique on (account, platform, token), indexed on account). Migration `d0e1f2g3h4i5_add_push_device_tokens`.
- **Recipient-side magic-link routing.** New `GET /v1/notifications/redeem-preview?code=...` endpoint reveals a pending channel's `destination_type` (plus channel name, system label, expiry) without consuming the activation code. The recipient-facing `/notifications/redeem` page uses it to branch: web push runs the in-browser permission + service-worker + subscribe flow; mobile push (FCM / APNS_DEV / APNS_PROD) shows an "Open in Sheaf" button that fires the `sheaf://notifications/redeem?code=...&channel=...` deep link, handing off to the native app for redemption. The web new-channel dialog also gains FCM and APNs options (with the same activation-link modal flow as web push) so owners can issue mobile-push channels directly.

### Messages

A lightweight in-system message board so headmates can leave each other notes — global wall plus a per-member wall (an SP-style surface, but encrypted at rest and revisioned).

- **Two board kinds.** `system` (one shared global feed) and `member` (one wall per member, addressable from the Members page or directly via `/messages?member=<id>`). The Messages tab in the sidebar shows both, with a search/filter panel for member walls.
- **No external auth.** Any member of the system can post and read on any board. Matches SP semantics — the threat model is "headmates leaving each other notes", not cross-system trust.
- **Authorship is per-member, not per-account.** Posters pick which member they are speaking as; deletes follow the author member, not the user account, so a member's posts go away cleanly when the member is deleted (`author_member_id` is `SET NULL`, rendered as "[deleted member]").
- **Replies are a chain, not a tree.** Each message can carry a single `parent_message_id`; the UI shows a "Replying to Alice: ..." backlink with a preview, no nested rendering. Keeps the model simple and avoids the SP-thread depth-creep failure mode.
- **Revision history.** Edits capture content revisions through the same polymorphic mechanism journals use; first revision auto-pinned. Length cap 5000 plaintext chars.
- **Soft delete.** Single-message delete tombstones the row (`deleted_at`); replies still render but show "Replying to a deleted message" instead of a preview. Thread delete is a separate operation that walks the reply tree breadth-first and deletes everything reachable.
- **System Safety integration.** New `applies_to_messages` category. Both `message_delete` and `message_thread_delete` go through `verify_destructive_auth` and queue pending actions when safeguarded; finalize hard-deletes. Threads stayed a separate operation type so a future per-operation auth-tier setting can require stronger reauth for "delete the entire reply tree".
- **Per-member unread tracking.** `MessageReadState` is keyed `(member_id, board_kind, board_member_id)` and lazy-created on first access — first call to `/v1/messages/unread` for a member establishes the baseline, so opening Messages doesn't dump every historical post into "unread". Sidebar nav badges the Messages item with the unread total for the first currently-fronting member.
- **On-front prompt.** Each member has three opt-in toggles (global, own wall, watched-member ids stored as JSONB). When a member starts fronting, `GET /v1/messages/front-start-prompt` returns the boards they care about with unread counts so the client can surface a "you have N unread on these walls" notice.
- **Encryption.** Message bodies are encrypted at rest with the same per-system key chain as the rest of the free-text surface.
- **Revision history surfaced inline.** Each message has a History button that opens the same revision viewer used for journals and bios (list, diff, restore, pin/unpin via System Safety) — `GET /v1/messages/{id}/revisions` plus `restore-revision`, `pin-revision`, `unpin-revision` POSTs. `ContentRevisionTarget.MESSAGE` joins the existing polymorphic enum.
- **Revision retention coverage.** The periodic `gc_revisions` job now sweeps message revisions alongside journal/bio revisions, honouring the same per-tier `revisions_per_target` and `revisions_max_days` caps. The orphan-revision sweep also covers the `message` target type. Message rows themselves are not bounded — same as journal entries; revisit if it ever becomes a problem.
- **Two view modes.** Flat (every message in chronological order) is the default; Topics mode groups by thread root and shows top-level posts with a reply-count badge, expanding inline to show the chain. Per-board state, no preference persistence yet.
- **API**: `POST/PATCH/DELETE /v1/messages`, `DELETE /v1/messages/{id}/thread`, `GET /v1/messages` (board-scoped list), `GET /v1/messages/boards`, `GET /v1/messages/unread`, `GET /v1/messages/front-start-prompt`, `POST /v1/messages/mark-seen`, `GET/PUT /v1/messages/notify-settings/{member_id}`, `GET /v1/messages/{id}/revisions`, `POST /v1/messages/{id}/restore-revision`, `POST /v1/messages/{id}/pin-revision`, `POST /v1/messages/{id}/unpin-revision`. All gated by the existing `members:*` scopes.
- **Frontend**: new `/messages` route with Global / Members tabs, composer with reply UI, edit dialog, single + thread destructive-confirm flows, History dialog per message, and a Flat/Topics view toggle. Member detail dialog gains a "Wall" button (deep-links into the member's board) and an "On-front notifications" editor.

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
