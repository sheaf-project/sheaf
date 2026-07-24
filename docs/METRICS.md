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
| `sheaf_http_requests_total` | counter | `method`, `route`, `status_class` |
| `sheaf_http_request_duration_seconds` | histogram | `method`, `route` |
| `sheaf_http_requests_in_progress` | gauge | `method` |

`route` is the templated path (`/v1/members/{member_id}`), not the raw
URL. Unmatched routes collapse to `<unmatched>`. `status_class` is one
of `2xx`, `3xx`, `4xx`, `5xx` (the literal HTTP status code is
deliberately not a label).

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
| `sheaf_auth_totp_enabled` | gauge | - |

Useful alerts: a sustained `password_incorrect` rate per hour (credential
stuffing), `lockout_events_total` rate (active attack), `lockouts_active`
high-water mark (failure-mode tracking).

### Anti-abuse

| Metric | Type | Labels |
|---|---|---|
| `sheaf_rate_limit_checks_total` | counter | `bucket`, `scope` ∈ {per_ip, per_user, global}, `outcome` ∈ {allowed, blocked} |
| `sheaf_rate_limit_active_blocks` | gauge | `bucket` |
| `sheaf_captcha_challenges_total` | counter | `outcome` ∈ {issued, solved, failed} |
| `sheaf_webhook_signature_failures_total` | counter | `endpoint` ∈ {sendgrid, cf_shield, notification_dispatch} |
| `sheaf_requests_per_ip_per_minute` | histogram | - |
| `sheaf_requests_per_account_per_minute` | histogram | - |

`bucket` is derived from the request route - values include `login`,
`register`, `password_reset`, `totp`, `email_verification`,
`account_delete`, `account_change`, `account_data`, `upload`, `export`,
`redeem`, `webhook`, `admin`, `global`, `other`. New endpoints land
under `other` until a bucket mapping is added in
`sheaf/middleware/rate_limit.py:_route_to_bucket`.

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
assuming it. Only published when `LEADER_ELECTION` is enabled; with it
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
tag_delete, field_delete, front_delete, journal_delete, image_delete,
channel_delete, reminder_delete, poll_delete, message_delete,
message_thread_delete, revision_unpin, watch_token_revoke).

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
front-history retention decision in real usage data (see
`../sheaf-design-docs/front-history-retention-and-limits.md`).

The `sheaf_journal_entries_total` / `sheaf_systems_by_journal_entry_count` /
`sheaf_system_journal_entry_count_max` set and the
`sheaf_content_revisions_total` / `sheaf_targets_by_revision_count` /
`sheaf_target_revision_count_max` / `sheaf_content_revisions_created_total`
set apply the same lens to journal entries and to content-revision (edit
history) volume. `sheaf_targets_by_revision_count` is the key one for the
journal-revision cap decision: a "target" is one journal entry / member bio /
message, and `sheaf_target_revision_count_max` is the most-revised single
target (the save-spam outlier signal). `sheaf_content_revisions_created_total`
is edit velocity on the live edit path (imports excluded). See
`../sheaf-design-docs/usage-limits-and-tiers.md`.

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

### Infra

| Metric | Type | Labels |
|---|---|---|
| `sheaf_db_pool_connections` | gauge | `state` ∈ {checked_in, checked_out} |
| `sheaf_db_query_duration_seconds` | histogram | `operation` ∈ {select, insert, update, delete, ddl, other} |
| `sheaf_redis_up` | gauge | - |
| `sheaf_s3_operations_total` | counter | `op`, `outcome` ∈ {success, error} |
| `sheaf_s3_operation_duration_seconds` | histogram | `op` |

`db_query_duration_seconds` complements the HTTP RED histogram - handler
latency is the user-facing number, but a query-time spike vs handler-
time spike tells you where to look.

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
