# Metrics

Sheaf exposes a Prometheus-compatible `/metrics` endpoint covering HTTP
request volume + latency, the auth funnel, rate-limit and lockout
counters, notification dispatch, email send, job runner, imports/exports,
System Safety, cf-shield events, and core data-shape gauges.

This document covers:

1. How to enable and bind the endpoint safely
2. The metric catalog (what's there and how to read it)
3. Cardinality rules (what NOT to add)
4. Multi-worker setup
5. How to add a new metric
6. Scrape configuration examples

---

## 1. Enabling and binding

Six environment variables control the endpoint. The defaults expose
metrics on `127.0.0.1:8090` with no auth - safe for a single-node
deployment scraped via SSH tunnel or a private network, NOT safe to
forward through your edge.

```
METRICS_ENABLED=true                 # master switch
METRICS_BIND=separate                # main | separate | disabled
METRICS_BIND_HOST=127.0.0.1          # separate listener bind address
METRICS_BIND_PORT=8090               # separate listener bind port
METRICS_AUTH=none                    # none | token
METRICS_TOKEN=                       # required when AUTH=token or BIND=main
METRICS_GAUGE_REFRESH_SECONDS=60     # DB-sourced gauges refresh interval
METRICS_EXTENDED=false               # opt into the sheaf_ext_* tier (see below)
METRICS_EXTENDED_VERSION_PAIRS_PER_DAY=64  # extended tier: version label cap
```

### Four shapes

| Shape | Values | When to use |
|---|---|---|
| Off | `METRICS_BIND=disabled` (or `ENABLED=false`) | No metrics anywhere. Good for "off until I get to it". |
| Local-only | `BIND=separate`, `HOST=127.0.0.1`, `AUTH=none` | Single-node deploy. Scrape over loopback (Prometheus on the same host, SSH tunnel, or sidecar). |
| Internal network | `BIND=separate`, `HOST=10.x.x.x`, `AUTH=none` | Multi-node deploy with a private network. Scrape from a Prometheus instance inside the network. Cloud SG / firewall is the perimeter. |
| Token-gated | `BIND=separate` or `main`, `AUTH=token`, `TOKEN=<bearer>` | Anywhere the endpoint is reachable from anything you don't already trust. |

The `main` bind ALWAYS forces token auth regardless of `METRICS_AUTH` -
sharing a listener with the public API surface makes "forgot to set
auth" a foot-gun, so we just rule it out.

`BIND=separate` + `AUTH=none` + a non-loopback / non-private bind host
will warn loudly at startup. Loopback (`127.0.0.1`, `::1`) and RFC1918
private ranges (`10/8`, `172.16/12`, `192.168/16`, etc.) are silent;
anything else (including `0.0.0.0` and hostnames) prints a warning so
you confirm the perimeter is doing the work - a containerised deploy
that binds `0.0.0.0` inside the container but only publishes the host
port on `127.0.0.1` is fine, but the app can't tell that on its own.

Generate a token:

```bash
openssl rand -hex 32
```

### Sample env per shape

```bash
# Local-only (default-ish)
METRICS_ENABLED=true
METRICS_BIND=separate
METRICS_BIND_HOST=127.0.0.1
METRICS_BIND_PORT=8090
METRICS_AUTH=none

# Internal network
METRICS_ENABLED=true
METRICS_BIND=separate
METRICS_BIND_HOST=10.0.1.5
METRICS_BIND_PORT=8090
METRICS_AUTH=none

# Token-gated on main listener (no second port to manage)
METRICS_ENABLED=true
METRICS_BIND=main
METRICS_AUTH=token
METRICS_TOKEN=<paste the openssl output>

# Token-gated separate listener (belt-and-braces)
METRICS_ENABLED=true
METRICS_BIND=separate
METRICS_BIND_HOST=0.0.0.0
METRICS_BIND_PORT=8090
METRICS_AUTH=token
METRICS_TOKEN=<paste the openssl output>
```

---

## 2. Metric catalog

Names follow `sheaf_<domain>_<thing>_<unit>`. Labels are listed for each
metric; permitted values are bounded sets defined in
`sheaf/observability/metrics.py` as `Literal[...]` aliases.

### HTTP (RED)

| Metric | Type | Labels |
|---|---|---|
| `sheaf_http_requests_total` | counter | `method`, `route`, `status_class`, `status` |
| `sheaf_http_request_duration_seconds` | histogram | `method`, `route` |
| `sheaf_http_requests_in_progress` | gauge | `method` |

`route` is the templated path (`/v1/members/{member_id}`), not the raw
URL. Unmatched routes collapse to `<unmatched>`. `status_class` is one
of `2xx`, `3xx`, `4xx`, `5xx`. `status` is the exact code for anything
outside 2xx (`404`, `429`, `503`, ...) and the literal `2xx` for
successes: a 429 has to be distinguishable from a 404 on a panel, but
the success path does not need to fan out into 200/201/204 per route.

### Auth funnel

| Metric | Type | Labels |
|---|---|---|
| `sheaf_auth_logins_total` | counter | `outcome` ∈ {success, user_not_found, password_incorrect, locked, totp_required, totp_invalid, recovery_code_used, trusted_device_bypass, captcha_failed, email_unverified, email_revalidation_required} |
| `sheaf_auth_password_reset_total` | counter | `stage` ∈ {requested, completed, expired, abandoned} |
| `sheaf_auth_email_verification_total` | counter | `outcome` ∈ {sent, completed, expired, resend_blocked} |
| `sheaf_auth_recovery_codes_used_total` | counter | - |
| `sheaf_auth_sessions_invalidated_total` | counter | `reason` ∈ {logout, expiry, mass_invalidation, password_change, cf_shield, admin} |
| `sheaf_auth_lockout_events_total` | counter | `reason` ∈ {login_failures, totp_failures} |
| `sheaf_auth_lockouts_active` | gauge | - |
| `sheaf_auth_trusted_devices_active` | gauge | - |
| `sheaf_auth_sessions_active` | gauge | - |
| `sheaf_auth_sessions_by_client` | gauge | `client_family` |
| `sheaf_auth_totp_enabled` | gauge | - |

Useful alerts: a sustained `password_incorrect` rate per hour (credential
stuffing), `lockout_events_total` rate (active attack), `lockouts_active`
high-water mark (failure-mode tracking).

### Anti-abuse

| Metric | Type | Labels |
|---|---|---|
| `sheaf_rate_limit_checks_total` | counter | `bucket`, `scope` ∈ {per_ip, per_user, global}, `outcome` ∈ {allowed, blocked} |
| `sheaf_rate_limit_active_blocks` | gauge | `bucket` |
| `sheaf_account_concurrency_total` | counter | `outcome` ∈ {immediate, waited, timed_out} |
| `sheaf_account_concurrency_wait_seconds` | histogram | - |
| `sheaf_captcha_challenges_total` | counter | `outcome` ∈ {issued, solved, failed} |
| `sheaf_webhook_signature_failures_total` | counter | `endpoint` ∈ {sendgrid, cf_shield, notification_dispatch} |
| `sheaf_requests_per_ip_per_minute` | histogram | - |
| `sheaf_requests_per_account_per_minute` | histogram | - |

`bucket` is derived from the request route - values include `login`,
`register`, `password_reset`, `totp`, `email_verification`,
`account_delete`, `account_change`, `account_data`, `upload`, `export`,
`redeem`, `webhook`, `admin`, `global`, `other`. New endpoints land
under `other` until a bucket mapping is added in
`sheaf/middleware/rate_limit.py:_route_to_bucket`. Three buckets are
named explicitly rather than derived, because each is one shared counter
across many routes: `write` (the combined per-account write budget),
`read` (its read-side twin, applied to every authenticated GET from the
auth dependency), and `concurrency` (the per-account in-flight cap; a
`blocked` there is a request that waited its full allowance for a slot
and got a 429).

`account_concurrency_total` and `account_concurrency_wait_seconds`
describe the in-flight cap itself. `waited` means an account was at its
cap and the request queued for a slot; a rising `waited` share with a
healthy `timed_out` of zero is a client fanning out faster than the cap
and being paced, which is the cap working. `timed_out` is the 429 case.
The wait histogram is the pacing delay those requests experienced, and
is the first place to look when one account's requests are slow and
nobody else's are.

The per-IP / per-account histograms are the "no labels" trick: they
capture the distribution of per-identifier request rates without ever
putting an IP or user ID into a label. p99 is the busiest IP everyone
is fine with; p999 is your busiest IP; an unexpected jump at high
percentiles means abuse.

### Notifications dispatch

| Metric | Type | Labels |
|---|---|---|
| `sheaf_notifications_dispatched_total` | counter | `channel_type`, `outcome` ∈ {success, transient_failure, permanent_failure, filtered, revoked, dropped} |
| `sheaf_notifications_dispatch_duration_seconds` | histogram | `channel_type` |
| `sheaf_notifications_dispatch_lag_seconds` | histogram | `channel_type` |
| `sheaf_notifications_outbox_depth` | gauge | - |
| `sheaf_notifications_outbox_oldest_pending_seconds` | gauge | - |
| `sheaf_notifications_subscriptions_active` | gauge | `channel_type` |
| `sheaf_webhook_ssrf_rejections_total` | counter | `channel_type` ∈ {webhook, ntfy, web_push} |
| `sheaf_webhook_private_target_allowed_total` | counter | `channel_type` ∈ {webhook, ntfy, web_push} |

`channel_type` ∈ {web_push, mobile_push, webhook, ntfy, pushover, discord, email}.

`webhook_ssrf_rejections_total` is a security signal: a delivery was refused
because the target resolved to a blocked internal / cloud-metadata address.
Sustained non-zero means a channel is pointed at an internal IP (a
misconfiguration, or an attempt to reach the operator's network). Transient
DNS failures are not counted here. `webhook_private_target_allowed_total`
counts deliveries that were permitted to a private / LAN address *because* the
target matched `WEBHOOK_ALLOWED_PRIVATE_CIDRS` - the self-host opt-in was
actually exercised. It is incremented once per delivery (keyed off the single
pinned address), not per resolved IP, so a multi-A-record LAN host doesn't
inflate it. It stays flat at zero unless an operator has enabled the
allowlist.

`outbox_depth` shows pending volume; `outbox_oldest_pending_seconds`
catches the "depth is fine but one row is stuck" case where a single
wedged dispatch can otherwise hide behind a healthy aggregate.
`dispatch_lag_seconds` is the per-row distribution: time from outbox
enqueue to dispatch on successful deliveries, the distributional cousin
of `oldest_pending_seconds`.

### Realtime front-change stream (SSE)

| Metric | Type | Labels |
|---|---|---|
| `sheaf_realtime_connections_active` | gauge | - |
| `sheaf_realtime_connections_opened_total` | counter | - |
| `sheaf_realtime_connections_closed_total` | counter | `reason` ∈ {client_closed, auth_revoked, auth_expired, backpressure, server_shutdown, error, connection_cap} |
| `sheaf_realtime_handshake_failures_total` | counter | `reason` ∈ {missing_scope, connection_cap, disabled} |
| `sheaf_realtime_events_published_total` | counter | - |
| `sheaf_realtime_events_delivered_total` | counter | - |
| `sheaf_realtime_events_dropped_total` | counter | `reason` ∈ {backpressure} |
| `sheaf_realtime_publish_failures_total` | counter | - |
| `sheaf_realtime_delivery_lag_seconds` | histogram | - |
| `sheaf_realtime_connection_duration_seconds` | histogram | - |

The first-party front-change SSE stream (`GET /v1/fronts/stream`). No
`system_id` / per-account label - the cardinality rule holds here too.

`delivery_lag_seconds` (emit at the front-switch commit to the client
write) is the headline signal that the stream beats the 5-second
notification poll. It is measured across replicas - a front change
published on one process and delivered on another - so it carries any
wall-clock skew between them.

`connections_opened_total` minus the sum of `connections_closed_total`
tracks alongside the `connections_active` gauge; a persistent gap between
`events_published_total` and `events_delivered_total` is fanout plus
drops. `connection_cap` appears on both `handshake_failures_total` (a
connection rejected at the cap) and `connections_closed_total` (reserved
for symmetry with the other close reasons). `publish_failures_total`
counts Redis-publish failures at the emit point; publishing is
best-effort and never fails the front switch.

### Email

| Metric | Type | Labels |
|---|---|---|
| `sheaf_emails_sent_total` | counter | `kind`, `provider`, `outcome` |
| `sheaf_email_provider_events_total` | counter | `provider`, `event` ∈ {bounce, blocked, dropped, deferred, spamreport} |
| `sheaf_email_send_duration_seconds` | histogram | `provider` |

`kind` ∈ {verification, password_reset, lockout_notify, export_ready,
deletion_reminder, deletion_confirmed, announcement, other}.

`provider` ∈ {ses, sendgrid, smtp, console, none}.

`outcome` ∈ {sent, blocked_recipient, send_failed, skipped_no_provider}.

### Jobs

| Metric | Type | Labels |
|---|---|---|
| `sheaf_job_runs_total` | counter | `job`, `outcome` ∈ {success, error, skipped} |
| `sheaf_job_run_duration_seconds` | histogram | `job` |
| `sheaf_job_items_processed_total` | counter | `job` |
| `sheaf_job_last_success_timestamp` | gauge | `job` |
| `sheaf_job_consecutive_failures` | gauge | `job` |
| `sheaf_orphan_files_deleted_total` | counter | (none) |

`job` is the name registered via `register_job()`. Alert on
`time() - last_success_timestamp > N` for stuck-job detection. (The
timestamp gauge predates the `_seconds` naming convention; it is a unix
timestamp in seconds despite the missing suffix.)

`sheaf_orphan_files_deleted_total` counts real (non-dry-run) blob deletions by
the orphaned-file cleanup. It is intentionally its own series so an abnormal
deletion volume is directly alertable: alert on an unexpected jump over a single
run's interval (e.g. `increase(sheaf_orphan_files_deleted_total[1h])` above a
sane ceiling), so an over-deletion trips within a run rather than surfacing via
a user report.

### Leader election

| Metric | Type | Labels |
|---|---|---|
| `sheaf_leader_is_leader` | gauge (livesum) | none |
| `sheaf_leader_transitions_total` | counter | none |

`sheaf_leader_is_leader` is 1 on the process holding background-loop
leadership and 0 on standbys; `multiprocess_mode=livesum` means
`sum(sheaf_leader_is_leader)` across live workers is the leader count.
The invariant alert is the point of this metric:

```
sum(sheaf_leader_is_leader) != 1   for 10m
```

0 means the election is wedged and all background work (job runner,
dispatcher, import runner) is stalled, which a quiet period would
otherwise hide since the notification-backlog alert only fires when
there's traffic to back up. 2+ is a split brain that shouldn't be
possible by construction; the metric proves the invariant rather than
assuming it. Only published when `LEADER_ELECTION_ENABLED` is on; with it
off, every process runs the loops and this metric is absent, so the
`!= 1` alert does not apply.

`sheaf_leader_transitions_total` increments on each acquisition; a high
`rate(sheaf_leader_transitions_total[15m])` is leadership flapping,
usually an unstable DB connection.

### Imports / exports

| Metric | Type | Labels |
|---|---|---|
| `sheaf_imports_started_total` | counter | `source` |
| `sheaf_imports_completed_total` | counter | `source`, `outcome` ∈ {complete, failed, cancelled} |
| `sheaf_imports_in_progress` | gauge | - |
| `sheaf_imports_oldest_pending_seconds` | gauge | none |
| `sheaf_exports_built_total` | counter | `outcome` ∈ {done, failed, expired} |
| `sheaf_export_size_bytes` | histogram | - |

`source` ∈ {pluralkit_file, pluralkit_api, tupperbox_file,
simplyplural_file, sheaf_file, pluralspace_file, prism_file,
ampersand_file}.

`sheaf_imports_oldest_pending_seconds` is the age of the oldest
unclaimed import. The runner is NOTIFY-driven, so a value climbing past
a few seconds means it isn't draining (wedged leader or a disconnected
LISTEN). Mirrors `sheaf_notifications_outbox_oldest_pending_seconds`.

### System Safety

| Metric | Type | Labels |
|---|---|---|
| `sheaf_pending_actions_active` | gauge | `category` |
| `sheaf_pending_actions_finalized_total` | counter | `category`, `outcome` ∈ {completed, cancelled, errored} |

`category` ∈ pending-action type enum (member_delete, group_delete,
tag_delete, field_delete, front_delete, journal_delete, journal_unpin,
image_delete, channel_delete, reminder_delete, poll_delete, message_delete,
message_thread_delete, revision_unpin, watch_token_revoke).

### Public profiles / sharing

| Metric | Type | Labels |
|---|---|---|
| `sheaf_share_grants_created_total` | counter | `subject_type` ∈ {public, link} |
| `sheaf_share_grants_revoked_total` | counter | `subject_type` ∈ {public, link} |
| `sheaf_share_grants_rotated_total` | counter | - |
| `sheaf_share_grants_finalized_total` | counter | `kind` |
| `sheaf_share_pending_exposures` | gauge | `kind` |
| `sheaf_share_pending_exposure_oldest_seconds` | gauge | - |
| `sheaf_share_grants_live` | gauge | `subject_type` ∈ {public, link} |
| `sheaf_public_media_serves_total` | counter | `outcome` |
| `sheaf_share_publish_blocked_total` | counter | `reason` |
| `sheaf_adult_attestations_total` | counter | - |
| `sheaf_watch_redemptions_total` | counter | `destination_type`, `outcome` |
| `sheaf_share_projection_duration_seconds` | histogram | `projection` ∈ {members} |
| `sheaf_share_views_with_option` | gauge | `option` ∈ {member_permalinks, include_fronting, include_bio, link_preview_detailed, member_preview_detailed} |
| `sheaf_systems_by_share_view_count` | gauge | `le` |
| `sheaf_system_share_view_count_max` | gauge | - |
| `sheaf_public_requests_total` | counter | `surface`, `subject_type` ∈ {public, link}, `outcome` |
| `sheaf_link_previews_total` | counter | `card` ∈ {generic, system_details, member}, `unfurler` |
| `sheaf_systems_with_public_profile_by_subject` | gauge | `subject_type` ∈ {public, link, both} |

`kind` (both the finalize counter and the pending gauge) ∈ {grant,
view_member, view_field, view_flags, member_guard, member_raise, edge_raise,
group_raise, field_raise, system_privacy} - one per promotion category the
finalize sweep handles. `member_raise` is a member's own ceiling waiting to go
public (the `members.pending_privacy` pair); `view_member` is a membership row
waiting, which after this split is how an unarchive back onto a published
view stages. `sheaf_share_grants_finalized_total` counts staged exposures the
sweep has promoted live; `sheaf_share_pending_exposures` is the point-in-time
depth still waiting behind a grace window (the operator mirror of the owner's
exposure banner). member/field kinds collapse per entity, matching the banner.

`sheaf_share_pending_exposure_oldest_seconds` is the age of the oldest staged
activation across all kinds; it stays 0 while every staged row is still ahead
of its window and only climbs once the finalize sweep falls behind. Mirrors
`sheaf_imports_oldest_pending_seconds`.

`sheaf_share_grants_live` counts grants live-or-pending on their own window
right now (the same `grant_live_clause` the resolver serves against).

`outcome` for `sheaf_public_media_serves_total` ∈ {served, feature_off,
invalid_path, invalid_token, dark_account, missing_blob}. The headline is
`dark_account`: a signed media capability presented after the profile behind
it went dark (revoke / rotate / system-private / suspend / ban). Raw serve
volume and latency stay in HTTP RED; this is only the outcome breakdown.

`reason` for `sheaf_share_publish_blocked_total` ∈ {adult_attestation,
publishing_blocked, system_not_public, grant_cap, duplicate_public}.
`sheaf_adult_attestations_total` counts the one-way 18+ self-declaration
transition only (a no-op re-declaration is not counted).

`destination_type` for `sheaf_watch_redemptions_total` ∈ {web_push,
mobile_push, unknown}; `outcome` ∈ {redeemed, invalid_code, not_pending,
expired, auth_required}. `invalid_code` carries `destination_type=unknown`
because no channel resolved. Only the reachable combinations are pre-warmed
(web push never demands auth; mobile push always does).

`sheaf_share_projection_duration_seconds` is scoped to the `members`
projection - the privacy-ceiling roster query plus the decrypt-and-render
pass. The other `project_*` surfaces are near-duplicates of HTTP RED and are
left to it.

`sheaf_share_views_with_option{option}` counts LIVE share views (at least one
grant satisfying `grant_live_clause`, the resolver's own predicate) with each
option on: member permalinks, fronting shown, bios shown, a detailed system
preview card, a detailed member preview card. Not a partition - a view can
have every option on - so the series do not sum to anything. A view nothing
points at is a draft and is not counted; its options are intentions, not
exposures. `sheaf_systems_by_share_view_count{le}` and
`sheaf_system_share_view_count_max` are the per-system view-count
distribution in the same CDF shape as the other data-shape gauges (hourly,
`FRONT_COUNT_BUCKETS` thresholds, `+Inf` = all systems). Most systems have
zero; the interesting comparison is `le="1"` against `le="5"` among the ones
that publish, which is what decides whether a linked "all public members"
selection is a convenience or a necessity.
**Demand side.** Everything above counts what owners publish; the last three
count what visitors and crawlers ask for.

`sheaf_public_requests_total` counts every anonymous JSON request to the
public surface. `surface` ∈ {system, members, member, fronting,
relationships, groups}; `subject_type` is how the visitor addressed it
(`public` by system id, `link` by share token); `outcome` ∈ {served,
withheld, pending, dark, not_found, feature_off}. The visitor still gets one
uniform 404 for every non-served outcome - the no-oracle rule is unchanged;
the split exists only in this counter. `withheld` is a live grant whose view
does not publish that surface (no roster, no fronting, no permalinks). `pending`
is a grant inside its grace window. `dark` is a grant that exists but may not
serve: revoked, or the account suppressed (system private, publishing latch,
suspended, banned, pending deletion) - the same set `public_media_serves_total`
calls `dark_account`, decided by the same two SQL clauses the resolver uses so
the two cannot disagree. `not_found` is no grant at all; a rotated link's old
token reads as this, since its hash matches nothing once rotated. The
classification runs only on the miss path (one extra lookup behind a 404) and
its answer never reaches the response. Reading it: `dark` and `not_found`
climbing on `subject_type=link` is somebody probing dead tokens; `served`
on `member` says deep links are being used at all; `withheld` on `fronting`
says visitors want something the owner chose not to show.

`sheaf_link_previews_total` counts crawler-facing preview documents by `card`
(generic / system_details / member) and `unfurler` ∈ {discord, slack, telegram,
mastodon, matrix, bluesky, twitter, facebook, whatsapp, other}, folded from the
User-Agent by a bounded substring match (`sheaf/observability/unfurler.py`) so a
crawler cannot mint series by lying about itself. It answers where people paste
their links and whether the detailed modes see use. Documents only: the image
a rich card points at is fetched as a consequence of the card and is not
counted again.

`sheaf_systems_with_public_profile_by_subject` partitions the adopters by how
they publish: `public` = only a public grant live, `link` = only share links,
`both` = at least one of each. The three sum to
`sheaf_systems_with_public_profile`, which keeps its unlabelled shape.

### cf-shield

| Metric | Type | Labels |
|---|---|---|
| `sheaf_cf_shield_engagements_total` | counter | `direction` ∈ {activated, deactivated} |
| `sheaf_cf_shield_session_revocations_total` | counter | - |
| `sheaf_cf_shield_active` | gauge | - |

`sheaf_cf_shield_active` is 1 when the backend believes shield mode is
currently engaged, else 0. Use it to alert on "shield-mode active for
> N minutes" and to cross-check against cf-shield's view of CF.

### Encryption / data integrity

| Metric | Type | Labels |
|---|---|---|
| `sheaf_decrypt_failures_total` | counter | `field` |
| `sheaf_field_decrypts_total` | counter | `version` |
| `sheaf_field_decrypt_v1_rejected_total` | counter | - |
| `sheaf_users_total` | gauge | - |
| `sheaf_users_pending_delete` | gauge | - |
| `sheaf_tier_limit_hits_total` | counter | `limit`, `tier` |

`limit` ∈ {members, storage, polls_concurrent, pushover_user,
pushover_global}.

`tier` ∈ {free, plus, self_hosted, unknown}.

Tracks where users bump into per-tier caps. Useful for pricing and
limit-adjustment decisions - a sustained `members{tier="free"}` rate
suggests the free cap needs revisiting.

`field` ∈ {email, totp_secret, recovery_codes, channel_config, other,
unlabelled}.

Should always be zero. Pre-warmed at startup so an absence-alert can
detect non-zero from the first scrape.

`sheaf_field_decrypts_total` counts successful field decrypts by ciphertext
format `version` ∈ {v1, v2}. v1 is the legacy no-AAD SecretBox format; v2 is
the AAD-bound XChaCha20-Poly1305 format. This is a cumulative counter, so
the migration signal is the *rate* of v1 reads trending toward zero as rows
are rewritten - the totals never fall, and read volume cannot prove
completeness (a dormant cell that is never read never shows here). The
authoritative completeness signal is the re-encrypt sweep's remaining-v1
count. After `FIELD_ENCRYPTION_ACCEPT_V1` is disabled, v1 reads fail closed
and never reach this success counter - the rejection lands on
`sheaf_field_decrypt_v1_rejected_total`, which counts reads of legacy v1
ciphertext rejected under the cutoff. After migration that counter should
be zero; a nonzero rate is an attempted legacy read or a v1 downgrade
attack.
Decrypt *failures* (including an AAD mismatch from a relocated v2
ciphertext, which is an indistinguishable nacl CryptoError) land on
`sheaf_decrypt_failures_total`; failure counting lives in `decrypt()`
itself, labelled `unlabelled` when the call site does not use
`decrypt_field`.

### Data shape

| Metric | Type | Labels |
|---|---|---|
| `sheaf_systems_total` | gauge | - |
| `sheaf_members_total` | gauge | - |
| `sheaf_members_custom_front` | gauge | - |
| `sheaf_fronts_total` | gauge | - |
| `sheaf_systems_by_front_count` | gauge | `le` (front-count threshold; `+Inf` = all systems) |
| `sheaf_system_front_count_max` | gauge | - |
| `sheaf_fronts_created_total` | counter | - |
| `sheaf_journal_entries_total` | gauge | - |
| `sheaf_systems_by_journal_entry_count` | gauge | `le` (entry-count threshold; `+Inf` = all systems) |
| `sheaf_system_journal_entry_count_max` | gauge | - |
| `sheaf_content_revisions_total` | gauge | - |
| `sheaf_targets_by_revision_count` | gauge | `le` (revisions-per-target threshold; `+Inf` = all targets) |
| `sheaf_target_revision_count_max` | gauge | - |
| `sheaf_content_revisions_created_total` | counter | - |
| `sheaf_messages_total` | gauge | - |
| `sheaf_systems_by_message_count` | gauge | `le` (live-message-count threshold; `+Inf` = all systems) |
| `sheaf_system_message_count_max` | gauge | - |
| `sheaf_messages_created_total` | counter | - |
| `sheaf_polls_total` | gauge | - |
| `sheaf_systems_by_poll_count` | gauge | `le` (poll-count threshold; `+Inf` = all systems) |
| `sheaf_system_poll_count_max` | gauge | - |
| `sheaf_polls_created_total` | counter | - |
| `sheaf_open_polls_total` | gauge | - |
| `sheaf_systems_by_open_poll_count` | gauge | `le` (open-poll-count threshold; `+Inf` = all systems) |
| `sheaf_system_open_poll_count_max` | gauge | - |
| `sheaf_groups_total` | gauge | - |
| `sheaf_systems_by_group_count` | gauge | `le` (group-count threshold; `+Inf` = all systems) |
| `sheaf_system_group_count_max` | gauge | - |
| `sheaf_groups_created_total` | counter | - |
| `sheaf_tags_total` | gauge | - |
| `sheaf_systems_by_tag_count` | gauge | `le` (tag-count threshold; `+Inf` = all systems) |
| `sheaf_system_tag_count_max` | gauge | - |
| `sheaf_tags_created_total` | counter | - |
| `sheaf_custom_fields_total` | gauge | - |
| `sheaf_systems_by_custom_field_count` | gauge | `le` (field-count threshold; `+Inf` = all systems) |
| `sheaf_system_custom_field_count_max` | gauge | - |
| `sheaf_custom_fields_created_total` | counter | - |
| `sheaf_reminders_total` | gauge | - |
| `sheaf_systems_by_reminder_count` | gauge | `le` (reminder-count threshold; `+Inf` = all systems) |
| `sheaf_system_reminder_count_max` | gauge | - |
| `sheaf_reminders_created_total` | counter | - |

`sheaf_systems_by_front_count` is a point-in-time cumulative distribution of
per-system front-history size, re-set each gauge refresh: each `le` series is
the number of systems whose front count is at or below that threshold. It
carries no system id by design. Read it to answer "what does a typical
system's front history look like, and is anyone an outlier?" - e.g. the gap
between the `le="1000"` series and `le="+Inf"` is how many systems have more
than 1000 fronts, and `sheaf_system_front_count_max` is the single largest.
`sheaf_fronts_created_total` is switch velocity (rows created), distinct from
the HTTP request counter on `POST /v1/fronts`. These exist to ground the
front-history retention decision in real usage data.

The `sheaf_journal_entries_total` / `sheaf_systems_by_journal_entry_count` /
`sheaf_system_journal_entry_count_max` set and the
`sheaf_content_revisions_total` / `sheaf_targets_by_revision_count` /
`sheaf_target_revision_count_max` / `sheaf_content_revisions_created_total`
set apply the same lens to journal entries and to content-revision (edit
history) volume. `sheaf_targets_by_revision_count` is the key one for the
journal-revision cap decision: a "target" is one journal entry / member bio /
message, and `sheaf_target_revision_count_max` is the most-revised single
target (the save-spam outlier signal). `sheaf_content_revisions_created_total`
is edit velocity on the live edit path (imports excluded).

The board-message / poll / group / tag / custom-field / reminder sets apply
the same lens to the remaining bulk-creatable user-content entities that
gained per-import row caps, so the caps can be tuned from real per-system
usage. Each set is a global `*_total`, an id-free per-system CDF snapshot
(`sheaf_systems_by_<entity>_count`, re-set each distribution refresh, `+Inf`
= all systems), the single-largest system (`sheaf_system_<entity>_count_max`),
and a live-create `*_created_total` counter (imports excluded - imports have
their own counters). `sheaf_messages_total` and `sheaf_systems_by_message_count`
count live messages only (`deleted_at IS NULL`), matching the board summary.
Polls carry two lenses: all polls, and OPEN polls
(`sheaf_open_polls_total` / `sheaf_systems_by_open_poll_count` /
`sheaf_system_open_poll_count_max`, where open = `closes_at` in the future).
The open-poll set is the operationally useful one: it is what the tier
concurrent-open-poll cap and the import clamp bound, so
`sheaf_system_open_poll_count_max` read against the cap is the direct outlier
signal. The `*_total` gauges refresh on the 60s gauge pass; the per-system
distributions ride the hourly distribution job.

### Usage (DAU / MAU)

| Metric | Type | Labels |
|---|---|---|
| `sheaf_signups_total` | counter | - |
| `sheaf_active_accounts_daily` | gauge | `auth_kind` |
| `sheaf_active_systems_daily` | gauge | `auth_kind` |
| `sheaf_active_accounts_monthly` | gauge | `auth_kind` |
| `sheaf_active_systems_monthly` | gauge | `auth_kind` |
| `sheaf_active_accounts_daily_by_client` | gauge | `client_family` |
| `sheaf_active_accounts_monthly_by_client` | gauge | `client_family` |
| `sheaf_active_accounts_monthly_overlap` | gauge | `families` |
| `sheaf_requests_by_client_total` | counter | `client_family` |
| `sheaf_push_devices` | gauge | `platform` ∈ {fcm, apns_dev, apns_prod} |
| `sheaf_active_accounts_daily_by_age` | gauge | `account_age` ∈ {lt7d, lt30d, lt90d, older} |
| `sheaf_systems_with_feature` | gauge | `feature` |
| `sheaf_systems_with_public_profile` | gauge | - |

`sheaf_signups_total` is new-account velocity (the flow signal), incremented
once per registration after the transaction commits. `sheaf_users_total` is the
stock; this is the flow. No labels.

The four `active_*` gauges are aggregate active-cardinality (DAU/MAU), and the
privacy invariant is strict: they are **aggregate counts only, never
attributable to an account**. On each authenticated request the account id and
its system id are PFADDed into a per-day Redis HyperLogLog sketch
(`sheaf:hll:<scope>:<auth_kind>:<day>`, e.g. `sheaf:hll:acct:client:<day>`,
~31-day TTL) at the auth choke point, best-effort and fire-and-forget so Redis
latency or an outage never delays or fails a request. The id is not added raw: it
goes through a keyed HMAC under the server encryption key first, so a sketch can
estimate a distinct count but a holder of the bytes can neither enumerate members
nor test whether a known account was active without the key (a plain HLL alone is
membership-testable by re-adding a candidate id, which is why the HMAC is there).
There is NO per-account series or stored id anywhere; the only label is the
bounded `auth_kind`, and only the PFCOUNT is ever published.

The `auth_kind` label splits interactive client use (`client`: session cookie or
JWT bearer, i.e. web and native apps) from automation (`api`: API key), kept in
separate sketches because a distinct count cannot be sliced out of a merged
sketch after the fact. `any` is the read-time deduped UNION of client and api
(PFMERGE), so an account active both ways in a window counts once - it is a true
total, not `client + api`.

`daily` is the PFCOUNT of today's sketch (for `any`, the merge of today's client
and api sketches). `monthly` (MAU) is the cardinality of the UNION of the
trailing 30 daily sketches (PFMERGE + PFCOUNT) - **not** a sum of daily counts,
which would double-count returning users. For durability the
per-day sketch BYTES (not a scalar count - a scalar cannot be unioned) are
flushed to the `usage_daily_sketches` Postgres table every 10 minutes by the
`flush_usage_sketches` job; Redis survives an in-place upgrade but not an
instance replace, so after a replace the monthly union RESTOREs any missing
day-key from Postgres before merging. That table is aggregate ops data (the ids
are irreversibly folded into HLL registers) and is deliberately excluded from
the user-data export. If Redis is down the gauges hold their last value rather
than zeroing (a blip is not "activity dropped to zero"); `sheaf_redis_up` covers
visibility.

**Client families.** `client_family` ∈ {web, android, ios, watch, api, other}
splits the `client` auth kind by platform. It is derived once per request at
the auth choke point from the credential and the `X-Sheaf-Client` header: an
API key is `api` whatever its header says, an official app prefix
(`Sheaf Web/`, `Sheaf Android/`, `Sheaf iOS/`, `Sheaf watchOS/`, `Sheaf Wear/`)
maps to its family, and anything else is `other`. The raw header never reaches
a label or a key, so a third-party client cannot mint series by changing it.
There is deliberately no User-Agent fallback for `web`: "a browser talked to
the API" is not "the Sheaf web app". The watchOS app currently sends the phone's
header and lands as `ios` until its watch target identifies itself.

The family sketches are **additive**: an interactive request is PFADDed into
the `client` auth-kind sketch exactly as before AND into
`sheaf:hll:acct:fam:<family>:<day>`, so the DAU/MAU series above never depend
on the family sketches and did not move when they were introduced. `api` has
no family sketch (the api auth-kind sketch is the api family), and families are
kept for the account scope only. Persisted alongside the auth-kind sketches in
`usage_daily_sketches` under a `client_family` column ('' for the auth-kind
rows) with the same restore-after-Redis-replace path.

`active_accounts_{daily,monthly}_by_client` are per-family cardinalities; an
account active on two families counts in both, and the deduped total is
`active_accounts_*{auth_kind=any}`. Platform share is the family gauge over
that total. `active_accounts_monthly_overlap{families="a+b"}` is the
estimated number of accounts active on BOTH families in the trailing 30 days,
by inclusion-exclusion over the sketch unions (`|A| + |B| - |A u B|`), one
series per unordered pair. It needs no per-account state at all, which is the
point; the cost is that three HLL estimates' errors compound, so it answers
"is it 3% or 30%", not "3% or 4%", and a small true overlap can read as 0.

`sheaf_requests_by_client_total` counts authenticated requests by family and
deliberately carries no route: it exists for the hour-of-day and day-of-week
shape per platform. Route x family belongs behind the extended-metrics gate
when that exists. `sheaf_auth_sessions_by_client` splits the live session
count by the family of the `client_name` stored at mint time; a web session
minted before the web app sent the header carries a browser name and reads as
`other` until it expires. `sheaf_push_devices` is the *installed* mobile base
(registered push tokens by platform), as opposed to the *active* base the
sketches count.

**Account age.** `sheaf_active_accounts_daily_by_age{account_age}` splits
today's active accounts by how long ago the account was created: under 7
days, under 30, under 90, older. The four buckets partition the day, so they
sum (within HLL error) to `active_accounts_daily{auth_kind=any}`, and the
shape is the retention signal: a healthy instance has a fat `older` and a
steady `lt7d`; a leaky one has a fat `lt7d` and not much else. Four fixed
buckets rather than a signup-week cohort label, which would add 52 series a
year for the same shape. **Daily only, on purpose**: a monthly union over age
buckets would need the persistence and restore machinery the other sketches
have, and the daily gauge answers the question, so these are Redis day-keys
(`sheaf:hll:acct:age:<bucket>:<day>`, two-day TTL) that are never persisted
and never restored. The bucket is decided at the auth choke point from the
account's creation timestamp and, like the family, only the bounded bucket
name ever becomes a key.

**Feature adoption.** `sheaf_systems_with_feature{feature}` is one
`COUNT(DISTINCT system_id)` per feature - journals, polls, relationships,
reminders, share_views, custom_fields, groups, tags - plus `public_profile`,
which is the same number as `sheaf_systems_with_public_profile` (kept as an
alias so existing dashboards keep working). Adoption, not volume: a system
with one journal entry and one with a thousand both count once, and the
data-shape distributions cover the volume side. Refreshed on the slow gauge
pass. It answers "which features do people actually use", which is the
question that decides where the next month goes.

`sheaf_systems_with_public_profile` is the public-profiles adoption signal:
distinct systems with at least one live public or unlisted-link share grant
right now, counted against the same `grant_live_clause` the resolver serves. No
labels.

### Extended tier (`METRICS_EXTENDED=true`)

Everything above is the default tier: bounded labels, aggregate values, no
per-account state, on for every instance with metrics enabled. The
extended tier is for the questions whose answers need more series or
short-lived per-account state, and it is off unless the operator asks:

```
METRICS_EXTENDED=true
```

Every setting the tier has shares the `METRICS_EXTENDED_` prefix, for the
same reason its metrics share `sheaf_ext_`: one grep finds the gate and
everything it governs.

Two things make the gate worth trusting. It is applied in one place
(`sheaf/observability/extended.py`): when the flag is off the metric objects
are never created, so the series do not exist, and nothing can half-leak
because a call site forgot a check. And every metric in the tier is named
`sheaf_ext_*`, every Redis key `sheaf:ext:*`, so one regex finds the lot
wherever it matters: `grep -r sheaf_ext_` in the repo,
`{__name__=~"sheaf_ext_.*"}` in a `write_relabel_configs` block or a
recording rule to route it to a short-retention or downsampled store, `SCAN
sheaf:ext:*` in Redis. A `tier` label would work in a pipeline but is
invisible in code and easy to drop when copying a definition.

Per-account state in this tier is folded under a **day-salted** token
(`HMAC(key, scope:day:id)`) rather than the stable one the DAU/MAU sketches
use. A stable token is fine inside a HyperLogLog, whose members are never
read back; anything that could be read back must not be joinable from one
day to the next. Every `sheaf:ext:` key carries a 48-hour TTL, and the
hourly `sweep_extended_metric_keys` job deletes anything older than
yesterday outright, so nothing per account outlives two days whether or not
the TTL fires.

| Metric | Type | Labels |
|---|---|---|
| `sheaf_ext_active_accounts_by_version` | gauge | `client_family`, `version` |

**Active accounts by client version.** Distinct accounts active today per
interactive client family and `major.minor` client version, from the
`X-Sheaf-Client` header (`Sheaf Android/1.2.0` reads as `1.2`; patch
releases would multiply the series for no decision anyone makes at patch
granularity). It answers "how long do we keep the compatibility shim for
1.2", and it is extended-tier because every release adds series on every
instance whether or not the operator cares. Unparseable or third-party
headers land as `unknown`, never the raw string, and a single day holds at
most `METRICS_EXTENDED_VERSION_PAIRS_PER_DAY` (default 64) distinct
`(family, version)` pairs before further new versions fold into `other`, so
a client minting a fresh version string per request cannot mint series;
pairs already seen that day keep counting normally, and the set resets with
the day. A version no longer seen today reads 0 until the process
restarts, then disappears. Refreshed on the slow gauge pass, read from a
per-`(family, version)` day sketch under the day-salted token.

### Infra

| Metric | Type | Labels |
|---|---|---|
| `sheaf_db_pool_connections` | gauge | `state` ∈ {checked_in, checked_out} |
| `sheaf_db_pool_checkout_wait_seconds` | histogram | - |
| `sheaf_db_query_duration_seconds` | histogram | `operation` ∈ {select, insert, update, delete, ddl, other} |
| `sheaf_redis_up` | gauge | - |
| `sheaf_s3_operations_total` | counter | `op`, `outcome` ∈ {success, error} |
| `sheaf_s3_operation_duration_seconds` | histogram | `op` |

`db_query_duration_seconds` complements the HTTP RED histogram - handler
latency is the user-facing number, but a query-time spike vs handler-
time spike tells you where to look.

`db_pool_checkout_wait_seconds` is the third leg of that: the time a
request session waited to get a pooled connection at all. When the pool
is exhausted, query time looks fine, handler time looks terrible, and
this is the one that says why. A p99 above a few milliseconds here means
the pool is the bottleneck; the per-account concurrency cap above is
what keeps one account from causing it, and `DB_POOL_TIMEOUT` bounds how
long a request waits before it is turned away with a 503.

`op` for S3 metrics ∈ {put, get, delete, head, list, presign}. Catches
"upload failures" and "image fetch storms" without bucket-name
cardinality (the wrapper covers both the images bucket and the
exports bucket).

`redis_up` and `db_pool_connections` are refreshed every
`METRICS_FAST_GAUGE_REFRESH_SECONDS` (default 10s) on a dedicated
asyncio loop, so up/down detection is bounded by that interval rather
than the slower DB-counts refresh.

### Build info

| Metric | Type | Labels |
|---|---|---|
| `sheaf_build_info` | gauge (always 1) | `version`, `sheaf_mode`, `git_commit` |

Standard pattern - value is meaningless, labels carry the dimensions.
Use it in Grafana for "running version" by joining against this metric.

---

## 3. Cardinality rules

Strict. Code review should bounce any PR that breaks these:

1. **No `*_id` labels.** Ever. Not user, not system, not member, not
   request, not anything. Per-identifier labels blow up Prometheus
   memory and break dashboard performance.
2. **No email or IP labels.** Same reason.
3. **No raw URL paths.** Use the route template (`/v1/things/{id}`)
   only.
4. **No raw HTTP status codes.** Use the status class (`2xx`, `3xx`,
   ...).
5. **Bounded label values via `Literal[...]`.** Each label that has a
   fixed value set should have a `Literal` alias in `metrics.py`. Typos
   become type errors rather than silently spawning new series.
6. **Pre-warm counters with bounded label sets.** Every
   `(outcome, ...)` combination should be touched at startup with
   `.inc(0)` so the series exists from the first scrape. Absence-alerts
   only fire on series Prometheus has seen.
7. **Per-identifier volume → histogram of rates, not labels.** See
   `requests_per_ip_per_minute` for the pattern: the background updater
   walks the rate-limit counters and observes each per-IP rate into a
   histogram. The IP never becomes a label.

---

## 4. Multi-worker setup

Sheaf currently ships a single-worker uvicorn (`Dockerfile.backend`).
The metrics module is multi-worker-ready anyway, controlled by the
`PROMETHEUS_MULTIPROC_DIR` env var:

- Set in the Dockerfile to `/var/run/prometheus-multiproc`.
- The entrypoint wipes the directory before starting uvicorn so stale
  values from a previous container life don't bleed in.
- When the env var is set, `init_registry()` builds a
  `MultiProcessCollector` that aggregates counter / histogram values
  across worker processes.
- When unset (tests, local single-process), the default in-process
  registry is used.

If you bump uvicorn or gunicorn workers up later, the only thing to be
aware of is that gauges need a `multiprocess_mode` declared. The
`_G(...)` helper in `metrics.py` does this - `livesum` is the default
for "count of things right now" gauges, `max` for high-water marks,
`mostrecent` for the build-info gauge. Adding a new gauge without
picking a mode is a clear code-review item.

---

## 5. Adding a new metric

1. Declare the metric in `sheaf/observability/metrics.py` using the
   `_C` / `_H` / `_G` wrappers (these bind to the shared registry and
   apply the right buckets / multiprocess_mode).
2. If labels have a fixed value set, declare a `Literal` alias in the
   same file.
3. Use a histogram bucket family from `buckets.py` rather than
   hand-rolling.
4. If counters have a bounded label set that should always be visible,
   pre-touch each combination with `.inc(0)` inside `prewarm_metrics()`.
5. Update this catalog.
6. If the metric multiplies an existing label set or holds anything per
   account, it belongs in the extended tier: declare it in
   `sheaf/observability/extended.py` under the `ENABLED` branch, name it
   `sheaf_ext_*`, key any Redis state `sheaf:ext:*` under the day-salted
   token with the 48-hour TTL, and document it in the extended-tier table.

---

## 6. Scrape configuration

### Local-only (separate listener, no auth)

```yaml
# prometheus.yml
scrape_configs:
  - job_name: sheaf
    static_configs:
      - targets: ['127.0.0.1:8090']
```

### Internal network (separate listener, no auth)

```yaml
scrape_configs:
  - job_name: sheaf
    static_configs:
      - targets: ['10.0.1.5:8090']
```

### Token-gated

```yaml
scrape_configs:
  - job_name: sheaf
    metrics_path: /metrics
    authorization:
      type: Bearer
      credentials: <the token>
    static_configs:
      - targets: ['sheaf.example.com:443']
    scheme: https
```

### Sample Grafana queries

```promql
# Login funnel breakdown
sum by (outcome) (rate(sheaf_auth_logins_total[5m]))

# 99th-percentile API latency
histogram_quantile(0.99,
  sum by (le, route) (rate(sheaf_http_request_duration_seconds_bucket[5m])))

# Stuck jobs: nothing succeeded for > 1h
time() - sheaf_job_last_success_timestamp > 3600

# Notifications outbox health
sheaf_notifications_outbox_depth
sheaf_notifications_outbox_oldest_pending_seconds

# Anti-abuse: per-IP rate distribution
histogram_quantile(0.99,
  sum by (le) (rate(sheaf_requests_per_ip_per_minute_bucket[5m])))
```

---

## Operational notes

- `/metrics` and `/health` are deliberately separate endpoints with
  different exposure rules. `/health` stays public on the API port for
  load balancers; `/metrics` does not.
- The gauge refresher runs as a registered job. Its interval is bounded
  below by `job_check_interval_minutes * 60`. Refresh-second values
  below that are effectively rounded up to the next loop tick.
- The per-IP / per-account rate histograms bail out cleanly if the
  Redis SCAN exceeds 50k keys per refresh - on deployments at that
  scale, switch to redis-exporter for per-IP visibility instead of
  trying to stream everything through this single sample pass.
