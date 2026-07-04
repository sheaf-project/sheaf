"""All metric definitions for Sheaf.

One module so additions are a single-PR change and the cardinality
rule (no `*_id` / per-user / per-IP labels) can be enforced by code
review of one file.

Label-value sets are declared as `Literal[...]` aliases so a typo
becomes a type error rather than a silent new series. The `_C(...)`,
`_H(...)`, `_G(...)` wrappers ensure every metric is bound to the
shared registry returned by `init_registry()`.

Adding a new metric:
  1. Pick a name with the `sheaf_` prefix and `<domain>_<thing>_<unit>`
     shape.
  2. Declare its label values as a Literal alias here if there's a
     bounded set.
  3. Use a histogram bucket family from `buckets.py` rather than
     hand-rolling.
  4. Pre-touch known label combinations in `prewarm_metrics()` so
     dashboards don't have to wait for first observation.
  5. Document in docs/METRICS.md.
"""

from __future__ import annotations

from typing import Literal

from prometheus_client import Counter, Gauge, Histogram

from sheaf.observability.buckets import (
    DISPATCH_LATENCY_BUCKETS,
    EXPORT_SIZE_BUCKETS,
    HTTP_LATENCY_BUCKETS,
    RATE_DISTRIBUTION_BUCKETS,
)
from sheaf.observability.registry import get_metric_registry

# ---------------------------------------------------------------------------
# Label-value type aliases
# ---------------------------------------------------------------------------

LoginOutcome = Literal[
    "success",
    "user_not_found",
    "password_incorrect",
    "locked",
    "totp_required",
    "totp_invalid",
    "recovery_code_used",
    "trusted_device_bypass",
    "captcha_failed",
    "email_unverified",
    "email_revalidation_required",
]
PasswordResetStage = Literal["requested", "completed", "expired", "abandoned"]
EmailVerificationOutcome = Literal["sent", "completed", "expired", "resend_blocked"]
SessionInvalidationReason = Literal[
    "logout", "expiry", "mass_invalidation", "password_change", "cf_shield", "admin"
]
LockoutReason = Literal["login_failures", "totp_failures"]

RateLimitScope = Literal["per_ip", "per_user", "global"]
RateLimitOutcome = Literal["allowed", "blocked"]
CaptchaOutcome = Literal["issued", "solved", "failed"]
WebhookEndpoint = Literal["sendgrid", "cf_shield", "notification_dispatch"]

ChannelType = Literal[
    "web_push", "mobile_push", "webhook", "ntfy", "pushover", "discord", "email"
]
DispatchOutcome = Literal[
    "success", "transient_failure", "permanent_failure", "filtered", "revoked", "dropped"
]

EmailKind = Literal[
    "verification",
    "password_reset",
    "lockout_notify",
    "export_ready",
    "deletion_reminder",
    "deletion_confirmed",
    "announcement",
    "other",
]
EmailProvider = Literal["ses", "sendgrid", "smtp", "console", "none"]
EmailOutcome = Literal["sent", "blocked_recipient", "send_failed", "skipped_no_provider"]
EmailProviderEvent = Literal["bounce", "blocked", "dropped", "deferred", "spamreport"]

JobOutcome = Literal["success", "error", "skipped"]
ImportSource = Literal[
    "pluralkit_file", "pluralkit_api", "simplyplural", "tupperbox", "sheaf", "ampersand"
]
ImportOutcome = Literal["complete", "failed", "cancelled"]
ExportOutcome = Literal["done", "failed", "expired"]

PendingActionOutcome = Literal["completed", "cancelled", "errored"]
ShieldDirection = Literal["activated", "deactivated"]

DecryptField = Literal[
    "email", "totp_secret", "recovery_codes", "channel_config", "other"
]

TierLimit = Literal[
    "members",
    "storage",
    "polls_concurrent",
    "pushover_user",
    "pushover_global",
]
TierLabel = Literal["free", "plus", "self_hosted", "unknown"]

DbPoolState = Literal["checked_in", "checked_out"]
StatusClass = Literal["1xx", "2xx", "3xx", "4xx", "5xx"]


# ---------------------------------------------------------------------------
# Wrappers binding every metric to the metric-object registry. NOTE:
# get_metric_registry(), not get_registry() - in multiprocess mode the
# objects must stay OUT of the scraped registry or every family is
# exported twice (live-object zeros + the real multiproc aggregate).
# See registry.get_metric_registry for the full story.
# ---------------------------------------------------------------------------

def _C(name: str, doc: str, labels: list[str] | None = None) -> Counter:
    return Counter(name, doc, labels or [], registry=get_metric_registry())


def _H(
    name: str, doc: str, labels: list[str] | None = None,
    buckets: tuple = HTTP_LATENCY_BUCKETS,
) -> Histogram:
    return Histogram(
        name, doc, labels or [], buckets=buckets, registry=get_metric_registry()
    )


def _G(
    name: str, doc: str, labels: list[str] | None = None,
    multiprocess_mode: str = "livesum",
) -> Gauge:
    # multiprocess_mode is ignored in single-process mode. In multiproc
    # mode it tells the collector how to aggregate values across worker
    # processes — `livesum` is the right default for in-flight counters
    # and any "count of things right now" gauge sampled by a single
    # background worker.
    return Gauge(
        name, doc, labels or [],
        registry=get_metric_registry(),
        multiprocess_mode=multiprocess_mode,
    )


# ---------------------------------------------------------------------------
# HTTP RED
# ---------------------------------------------------------------------------

http_requests_total = _C(
    "sheaf_http_requests_total",
    "HTTP requests handled by the API, by route template and status class.",
    ["method", "route", "status_class"],
)
http_request_duration_seconds = _H(
    "sheaf_http_request_duration_seconds",
    "End-to-end handler duration (post-middleware), seconds.",
    ["method", "route"],
    buckets=HTTP_LATENCY_BUCKETS,
)
http_requests_in_progress = _G(
    "sheaf_http_requests_in_progress",
    "HTTP requests currently being handled.",
    ["method"],
    multiprocess_mode="livesum",
)

# ---------------------------------------------------------------------------
# Auth funnel
# ---------------------------------------------------------------------------

auth_logins_total = _C(
    "sheaf_auth_logins_total",
    "Login attempts terminated at each funnel outcome.",
    ["outcome"],
)
auth_password_reset_total = _C(
    "sheaf_auth_password_reset_total",
    "Password reset stages reached.",
    ["stage"],
)
auth_email_verification_total = _C(
    "sheaf_auth_email_verification_total",
    "Email verification outcomes.",
    ["outcome"],
)
auth_recovery_codes_used_total = _C(
    "sheaf_auth_recovery_codes_used_total",
    "TOTP recovery codes burned. Tracks consumption of a finite resource.",
)
auth_sessions_invalidated_total = _C(
    "sheaf_auth_sessions_invalidated_total",
    "Sessions invalidated, by cause.",
    ["reason"],
)
auth_lockout_events_total = _C(
    "sheaf_auth_lockout_events_total",
    "Account lockouts triggered.",
    ["reason"],
)

auth_lockouts_active = _G(
    "sheaf_auth_lockouts_active",
    "Accounts currently locked (locked_until > now).",
)
auth_trusted_devices_active = _G(
    "sheaf_auth_trusted_devices_active",
    "Trusted device entries currently valid (expires_at > now).",
)
auth_sessions_active = _G(
    "sheaf_auth_sessions_active",
    "Active sessions tracked in Redis.",
)
auth_totp_enabled = _G(
    "sheaf_auth_totp_enabled",
    "Users with TOTP currently enabled.",
)

# ---------------------------------------------------------------------------
# Anti-abuse / rate limit
# ---------------------------------------------------------------------------

rate_limit_checks_total = _C(
    "sheaf_rate_limit_checks_total",
    "Rate-limit evaluations, by bucket / scope / outcome.",
    ["bucket", "scope", "outcome"],
)
rate_limit_active_blocks = _G(
    "sheaf_rate_limit_active_blocks",
    "Identifiers currently sitting at their rate-limit ceiling, by bucket.",
    ["bucket"],
)
captcha_challenges_total = _C(
    "sheaf_captcha_challenges_total",
    "Captcha lifecycle counts.",
    ["outcome"],
)
webhook_signature_failures_total = _C(
    "sheaf_webhook_signature_failures_total",
    "Inbound webhook requests rejected for invalid signature. "
    "Sustained non-zero indicates probing or misconfigured sender.",
    ["endpoint"],
)

# Per-identifier rate distributions — the anti-abuse "no labels" trick.
# Background updater samples each identifier's per-minute rate into the
# histogram; the identifier itself never becomes a label.
requests_per_ip_per_minute = _H(
    "sheaf_requests_per_ip_per_minute",
    "Distribution of per-minute request rate across active IPs.",
    [],
    buckets=RATE_DISTRIBUTION_BUCKETS,
)
requests_per_account_per_minute = _H(
    "sheaf_requests_per_account_per_minute",
    "Distribution of per-minute request rate across authenticated accounts.",
    [],
    buckets=RATE_DISTRIBUTION_BUCKETS,
)

# ---------------------------------------------------------------------------
# Notifications dispatch
# ---------------------------------------------------------------------------

notifications_dispatched_total = _C(
    "sheaf_notifications_dispatched_total",
    "Notification outbox rows reaching a terminal disposition.",
    ["channel_type", "outcome"],
)
notifications_dispatch_duration_seconds = _H(
    "sheaf_notifications_dispatch_duration_seconds",
    "Per-channel dispatch handler runtime.",
    ["channel_type"],
    buckets=DISPATCH_LATENCY_BUCKETS,
)
notifications_dispatch_lag_seconds = _H(
    "sheaf_notifications_dispatch_lag_seconds",
    "Time from outbox row enqueued to dispatched (success only). "
    "Distributional cousin of outbox_oldest_pending_seconds.",
    ["channel_type"],
    buckets=DISPATCH_LATENCY_BUCKETS,
)
notifications_outbox_depth = _G(
    "sheaf_notifications_outbox_depth",
    "Outbox rows with delivered_at IS NULL.",
)
notifications_outbox_oldest_pending_seconds = _G(
    "sheaf_notifications_outbox_oldest_pending_seconds",
    "Age of the oldest pending outbox row in seconds; 0 when none pending.",
)
notifications_subscriptions_active = _G(
    "sheaf_notifications_subscriptions_active",
    "Active notification channels by destination type.",
    ["channel_type"],
)

# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

emails_sent_total = _C(
    "sheaf_emails_sent_total",
    "Outbound emails by category / provider / outcome.",
    ["kind", "provider", "outcome"],
)
email_provider_events_total = _C(
    "sheaf_email_provider_events_total",
    "Provider feedback events (bounces, complaints, deferrals).",
    ["provider", "event"],
)
email_send_duration_seconds = _H(
    "sheaf_email_send_duration_seconds",
    "Time spent in the provider send call.",
    ["provider"],
    buckets=DISPATCH_LATENCY_BUCKETS,
)

# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

job_runs_total = _C(
    "sheaf_job_runs_total",
    "Scheduled job runs, by job name and outcome.",
    ["job", "outcome"],
)
job_run_duration_seconds = _H(
    "sheaf_job_run_duration_seconds",
    "Per-job run duration, seconds.",
    ["job"],
    buckets=DISPATCH_LATENCY_BUCKETS,
)
job_items_processed_total = _C(
    "sheaf_job_items_processed_total",
    "Total items processed by a job (sum of per-run items_processed).",
    ["job"],
)
job_last_success_timestamp = _G(
    "sheaf_job_last_success_timestamp",
    "Unix timestamp of the most recent successful run of each job.",
    ["job"],
    multiprocess_mode="max",
)
orphan_files_deleted_total = _C(
    "sheaf_orphan_files_deleted_total",
    # Deliberately its own series (not folded into job_items_processed_total)
    # so a deletion-volume alert can target it directly: the 2026-07-03 incident
    # over-deleted 1000+ live blobs and surfaced only via a user report. Alert
    # on an unusual increase over a single run's interval.
    "Uploaded files removed by the orphaned-file cleanup job (real deletions, "
    "not dry-run).",
)
job_consecutive_failures = _G(
    "sheaf_job_consecutive_failures",
    "Consecutive failures of each job since the last success.",
    ["job"],
    multiprocess_mode="max",
)

# ---------------------------------------------------------------------------
# Leader election (background-loop coordination)
# ---------------------------------------------------------------------------

leader_is_leader = _G(
    "sheaf_leader_is_leader",
    "1 on the process currently holding background-loop leadership, else 0. "
    "multiprocess_mode=livesum so sum() across live worker processes is the "
    "leader count - alert on sum(sheaf_leader_is_leader) != 1 to catch a "
    "wedged election (0 leaders, background work stalled) or, impossibly, a "
    "split brain (2+). Only meaningful with LEADER_ELECTION enabled; with it "
    "off every process runs the loops and this metric is not published.",
    multiprocess_mode="livesum",
)
leader_transitions_total = _C(
    "sheaf_leader_transitions_total",
    "Background-loop leadership acquisitions. A steady climb (high "
    "rate(sheaf_leader_transitions_total[15m])) signals leadership flapping, "
    "usually an unstable DB connection dropping and reacquiring the lock.",
)

# ---------------------------------------------------------------------------
# Imports / exports
# ---------------------------------------------------------------------------

imports_started_total = _C(
    "sheaf_imports_started_total",
    "Imports that transitioned PENDING -> RUNNING.",
    ["source"],
)
imports_completed_total = _C(
    "sheaf_imports_completed_total",
    "Imports terminated, by source and outcome.",
    ["source", "outcome"],
)
imports_in_progress = _G(
    "sheaf_imports_in_progress",
    "Imports currently pending or running.",
)
imports_oldest_pending_seconds = _G(
    "sheaf_imports_oldest_pending_seconds",
    "Age of the oldest still-pending import, in seconds (0 when none are "
    "pending). The import runner is NOTIFY-driven, so a value that climbs "
    "past a few seconds means the runner isn't draining - the leader is "
    "wedged or the listener is disconnected. Mirrors "
    "notifications_outbox_oldest_pending_seconds.",
)
exports_built_total = _C(
    "sheaf_exports_built_total",
    "Export builds terminated, by outcome.",
    ["outcome"],
)
export_size_bytes = _H(
    "sheaf_export_size_bytes",
    "Size of generated export artefacts in bytes.",
    [],
    buckets=EXPORT_SIZE_BUCKETS,
)

# ---------------------------------------------------------------------------
# System Safety
# ---------------------------------------------------------------------------

pending_actions_active = _G(
    "sheaf_pending_actions_active",
    "Pending destructive actions sitting in their grace window.",
    ["category"],
)
pending_actions_finalized_total = _C(
    "sheaf_pending_actions_finalized_total",
    "Pending actions resolved (completed / cancelled / errored).",
    ["category", "outcome"],
)

# ---------------------------------------------------------------------------
# cf-shield
# ---------------------------------------------------------------------------

cf_shield_engagements_total = _C(
    "sheaf_cf_shield_engagements_total",
    "Shield-mode webhook transitions handled.",
    ["direction"],
)
cf_shield_session_revocations_total = _C(
    "sheaf_cf_shield_session_revocations_total",
    "User sessions invalidated by shield-mode deactivation.",
)
cf_shield_active = _G(
    "sheaf_cf_shield_active",
    "1 when the backend believes shield mode is engaged, else 0. "
    "Mirrors the value /v1/shield-mode/status returns.",
    multiprocess_mode="max",
)

# ---------------------------------------------------------------------------
# Encryption / data integrity
# ---------------------------------------------------------------------------

decrypt_failures_total = _C(
    "sheaf_decrypt_failures_total",
    "Field-decrypt failures by field. Should always be zero; non-zero "
    "indicates key drift or corruption.",
    ["field"],
)
tier_limit_hits_total = _C(
    "sheaf_tier_limit_hits_total",
    "Quota-rejection callsites by limit category and account tier. "
    "Tracks where users bump into per-tier caps; informs pricing / "
    "limit-adjustment decisions.",
    ["limit", "tier"],
)
users_total = _G(
    "sheaf_users_total",
    "All user accounts.",
)
users_pending_delete = _G(
    "sheaf_users_pending_delete",
    "User accounts with status=pending_deletion.",
)

# ---------------------------------------------------------------------------
# Data shape (slow gauges)
# ---------------------------------------------------------------------------

systems_total = _G("sheaf_systems_total", "All systems.")
members_total = _G("sheaf_members_total", "All members across all systems.")
members_custom_front = _G(
    "sheaf_members_custom_front",
    "Members flagged as custom-front entities (non-counting fronters).",
)
fronts_total = _G(
    "sheaf_fronts_total",
    "All front-history rows across all systems (global volume baseline).",
)

# Per-system front counts as a point-in-time DISTRIBUTION, without ever
# labelling by system_id (that would blow up cardinality and leak which
# system is which). `systems_by_front_count` is a snapshot cumulative
# histogram expressed as a gauge: each refresh sets, per `le` threshold, the
# number of systems whose front-history row count is <= that threshold.
# Read across the buckets to see the distribution; `system_front_count_max`
# is the single biggest system, the direct "is anyone an outlier?" signal.
# A gauge (re-set each refresh) rather than a real Histogram because the
# quantity changes slowly and we want a current snapshot, not an all-time
# accumulation. Thresholds are front *counts*, not seconds.
FRONT_COUNT_BUCKETS = (
    1, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 25000, 50000,
    100000,
)
systems_by_front_count = _G(
    "sheaf_systems_by_front_count",
    "Number of systems whose front-history row count is <= the `le` bucket "
    "(a point-in-time cumulative distribution, re-set each refresh). Read "
    "across buckets to see the per-system distribution. See docs/METRICS.md.",
    ["le"],
)
system_front_count_max = _G(
    "sheaf_system_front_count_max",
    "Largest single system's front-history row count - the direct outlier "
    "signal for the retention decision.",
)
fronts_created_total = _C(
    "sheaf_fronts_created_total",
    "Front-history rows created via the API. Switch velocity: counts row "
    "creation, distinct from the HTTP request counter on POST /v1/fronts.",
)

# Journal entries + content-revision (edit-history) volume - the same
# preserve-by-count lens as front history, feeding the journal-revision
# count-cap decision (../sheaf-design-docs/usage-limits-and-tiers.md). The
# generic FRONT_COUNT_BUCKETS thresholds are reused (they are plain counts).
journal_entries_total = _G(
    "sheaf_journal_entries_total",
    "All journal entries across all systems (global volume baseline).",
)
systems_by_journal_entry_count = _G(
    "sheaf_systems_by_journal_entry_count",
    "Number of systems whose journal-entry count is <= the `le` bucket (a "
    "point-in-time cumulative distribution, re-set each refresh).",
    ["le"],
)
system_journal_entry_count_max = _G(
    "sheaf_system_journal_entry_count_max",
    "Largest single system's journal-entry count.",
)

# Content revisions are the edit history for journal entries, member bios,
# and messages (one polymorphic table). The pathological case is a single
# target with a huge revision count (a save-spamming client / bug); these
# answer "is any single target an outlier?" without naming it - the input to
# shifting journal revisions from age-based to count-based retention.
content_revisions_total = _G(
    "sheaf_content_revisions_total",
    "All content-revision (edit-history) rows across journal entries, member "
    "bios, and messages.",
)
targets_by_revision_count = _G(
    "sheaf_targets_by_revision_count",
    "Number of revision targets (one journal entry / member bio / message) "
    "whose revision count is <= the `le` bucket (point-in-time cumulative "
    "distribution, re-set each refresh). See docs/METRICS.md.",
    ["le"],
)
target_revision_count_max = _G(
    "sheaf_target_revision_count_max",
    "Most revisions on any single target - the direct outlier signal for the "
    "journal-revision count-cap decision.",
)
content_revisions_created_total = _C(
    "sheaf_content_revisions_created_total",
    "Content revisions created via the live edit path (not imports). Edit "
    "velocity; the save-spam signal.",
)

# Per-system volume for the remaining bulk-creatable user-content entities
# that gained per-import row caps (board messages, polls, groups, tags,
# custom-field definitions, reminders). Same preserve-by-count lens as front
# history: a global COUNT(*) total, an id-free per-system CDF snapshot re-set
# each refresh (systems whose count is <= each `le` bucket, `+Inf` = all
# systems), the single-largest system, and - where there's one clean create
# choke point - a live-create Counter. FRONT_COUNT_BUCKETS thresholds are
# reused (plain counts). These ground the import row-cap tuning in real usage.

# Board messages. Counted live (deleted_at IS NULL), matching how the board
# summary counts them - soft-deleted rows don't count against a system.
messages_total = _G(
    "sheaf_messages_total",
    "All live board messages (deleted_at IS NULL) across all systems.",
)
systems_by_message_count = _G(
    "sheaf_systems_by_message_count",
    "Number of systems whose live board-message count is <= the `le` bucket "
    "(point-in-time cumulative distribution, re-set each refresh).",
    ["le"],
)
system_message_count_max = _G(
    "sheaf_system_message_count_max",
    "Largest single system's live board-message count.",
)
messages_created_total = _C(
    "sheaf_messages_created_total",
    "Board messages created via the live post path (not imports). Post "
    "velocity, distinct from the HTTP request counter on POST /v1/messages.",
)

# Polls. Two lenses: all polls per system, and OPEN polls per system - the
# latter is what the tier concurrency cap and the import clamp bound. An open
# poll is one whose deadline is still in the future (closes_at > now), the
# same definition the create-path cap uses.
polls_total = _G(
    "sheaf_polls_total",
    "All polls across all systems (global volume baseline).",
)
systems_by_poll_count = _G(
    "sheaf_systems_by_poll_count",
    "Number of systems whose poll count is <= the `le` bucket (point-in-time "
    "cumulative distribution, re-set each refresh).",
    ["le"],
)
system_poll_count_max = _G(
    "sheaf_system_poll_count_max",
    "Largest single system's poll count.",
)
polls_created_total = _C(
    "sheaf_polls_created_total",
    "Polls created via the live create path (not imports).",
)
open_polls_total = _G(
    "sheaf_open_polls_total",
    "Polls currently open (closes_at > now) across all systems. Open polls "
    "are what the tier concurrency cap and the import clamp bound.",
)
systems_by_open_poll_count = _G(
    "sheaf_systems_by_open_poll_count",
    "Number of systems whose OPEN poll count (closes_at > now) is <= the `le` "
    "bucket (point-in-time cumulative distribution, re-set each refresh). The "
    "direct per-system view against the concurrent-open-poll cap.",
    ["le"],
)
system_open_poll_count_max = _G(
    "sheaf_system_open_poll_count_max",
    "Largest single system's open-poll count - the outlier signal against "
    "the concurrent-open-poll cap.",
)

# Groups, tags, custom-field definitions, reminders. Lower-volume per-system
# config entities that gained import row caps. Each has a single clean create
# choke point, so all carry a live-create counter too.
groups_total = _G(
    "sheaf_groups_total",
    "All groups across all systems (global volume baseline).",
)
systems_by_group_count = _G(
    "sheaf_systems_by_group_count",
    "Number of systems whose group count is <= the `le` bucket (point-in-time "
    "cumulative distribution, re-set each refresh).",
    ["le"],
)
system_group_count_max = _G(
    "sheaf_system_group_count_max",
    "Largest single system's group count.",
)
groups_created_total = _C(
    "sheaf_groups_created_total",
    "Groups created via the live create path (not imports).",
)

tags_total = _G(
    "sheaf_tags_total",
    "All tags across all systems (global volume baseline).",
)
systems_by_tag_count = _G(
    "sheaf_systems_by_tag_count",
    "Number of systems whose tag count is <= the `le` bucket (point-in-time "
    "cumulative distribution, re-set each refresh).",
    ["le"],
)
system_tag_count_max = _G(
    "sheaf_system_tag_count_max",
    "Largest single system's tag count.",
)
tags_created_total = _C(
    "sheaf_tags_created_total",
    "Tags created via the live create path (not imports).",
)

custom_fields_total = _G(
    "sheaf_custom_fields_total",
    "All custom-field definitions across all systems (global volume "
    "baseline).",
)
systems_by_custom_field_count = _G(
    "sheaf_systems_by_custom_field_count",
    "Number of systems whose custom-field-definition count is <= the `le` "
    "bucket (point-in-time cumulative distribution, re-set each refresh).",
    ["le"],
)
system_custom_field_count_max = _G(
    "sheaf_system_custom_field_count_max",
    "Largest single system's custom-field-definition count.",
)
custom_fields_created_total = _C(
    "sheaf_custom_fields_created_total",
    "Custom-field definitions created via the live create path (not imports).",
)

reminders_total = _G(
    "sheaf_reminders_total",
    "All reminders across all systems (global volume baseline).",
)
systems_by_reminder_count = _G(
    "sheaf_systems_by_reminder_count",
    "Number of systems whose reminder count is <= the `le` bucket "
    "(point-in-time cumulative distribution, re-set each refresh).",
    ["le"],
)
system_reminder_count_max = _G(
    "sheaf_system_reminder_count_max",
    "Largest single system's reminder count.",
)
reminders_created_total = _C(
    "sheaf_reminders_created_total",
    "Reminders created via the live create path (not imports).",
)

# ---------------------------------------------------------------------------
# Infra
# ---------------------------------------------------------------------------

db_pool_connections = _G(
    "sheaf_db_pool_connections",
    "SQLAlchemy async pool connection counts.",
    ["state"],
)
db_query_duration_seconds = _H(
    "sheaf_db_query_duration_seconds",
    "DB query execution time bucketed by SQL operation. Operation is the "
    "leading verb (select / insert / update / delete / ddl / other) so "
    "cardinality stays bounded.",
    ["operation"],
    buckets=HTTP_LATENCY_BUCKETS,
)
redis_up = _G(
    "sheaf_redis_up",
    "1 if Redis PING succeeded on the last gauge refresh, else 0.",
)
s3_operations_total = _C(
    "sheaf_s3_operations_total",
    "S3 operations attempted by the storage backends, by op and outcome.",
    ["op", "outcome"],
)
s3_operation_duration_seconds = _H(
    "sheaf_s3_operation_duration_seconds",
    "S3 operation runtime.",
    ["op"],
    buckets=DISPATCH_LATENCY_BUCKETS,
)

# ---------------------------------------------------------------------------
# Build info
# ---------------------------------------------------------------------------

build_info = _G(
    "sheaf_build_info",
    "Static build provenance, value always 1. Use labels for filters/joins.",
    ["version", "sheaf_mode", "git_commit"],
    multiprocess_mode="mostrecent",
)


# ---------------------------------------------------------------------------
# Pre-warm: touch every counter that has a bounded label set, so absent-
# metric alerts (which fire when a series has never been observed) work
# from the first scrape. Histograms don't need this — promtool / Grafana
# tolerate empty histogram buckets fine — but counters that "should
# always exist" need a zero observation to materialise the series.
# ---------------------------------------------------------------------------

def prewarm_metrics() -> None:
    for outcome in (
        "success",
        "user_not_found",
        "password_incorrect",
        "locked",
        "totp_required",
        "totp_invalid",
        "recovery_code_used",
        "trusted_device_bypass",
        "captcha_failed",
        "email_unverified",
        "email_revalidation_required",
    ):
        auth_logins_total.labels(outcome=outcome).inc(0)

    for stage in ("requested", "completed", "expired", "abandoned"):
        auth_password_reset_total.labels(stage=stage).inc(0)

    for outcome in ("sent", "completed", "expired", "resend_blocked"):
        auth_email_verification_total.labels(outcome=outcome).inc(0)

    for reason in (
        "logout", "expiry", "mass_invalidation", "password_change", "cf_shield", "admin",
    ):
        auth_sessions_invalidated_total.labels(reason=reason).inc(0)

    for reason in ("login_failures", "totp_failures"):
        auth_lockout_events_total.labels(reason=reason).inc(0)

    for outcome in ("issued", "solved", "failed"):
        captcha_challenges_total.labels(outcome=outcome).inc(0)

    for endpoint in ("sendgrid", "cf_shield", "notification_dispatch"):
        webhook_signature_failures_total.labels(endpoint=endpoint).inc(0)

    for direction in ("activated", "deactivated"):
        cf_shield_engagements_total.labels(direction=direction).inc(0)

    # decrypt_failures should always be zero; pre-touch each field so
    # the absence alert can detect a non-zero rate as soon as it appears.
    for field in ("email", "totp_secret", "recovery_codes", "channel_config", "other"):
        decrypt_failures_total.labels(field=field).inc(0)

    auth_recovery_codes_used_total.inc(0)
    cf_shield_session_revocations_total.inc(0)

    for limit_name in (
        "members", "storage", "polls_concurrent",
        "pushover_user", "pushover_global",
    ):
        for tier in ("free", "plus", "self_hosted"):
            tier_limit_hits_total.labels(limit=limit_name, tier=tier).inc(0)


async def observe_s3(op: str, awaitable):
    """Wrap an awaitable S3 call with the s3_operations counter + duration.

    Use at the call site:
        result = await observe_s3("put", self._run(self.client.put_object, ...))
    Re-raises on failure after labelling outcome=error.
    """
    import time as _time

    start = _time.perf_counter()
    try:
        result = await awaitable
    except Exception:
        s3_operations_total.labels(op=op, outcome="error").inc()
        s3_operation_duration_seconds.labels(op=op).observe(
            _time.perf_counter() - start
        )
        raise
    else:
        s3_operations_total.labels(op=op, outcome="success").inc()
        s3_operation_duration_seconds.labels(op=op).observe(
            _time.perf_counter() - start
        )
        return result


def tier_label(tier: object) -> str:
    """Coerce a user tier value (UserTier enum or string) to a metric label.

    Returns "unknown" for None / unexpected values so the metric stays
    safe to call without bespoke null-checking at every site.
    """
    if tier is None:
        return "unknown"
    value = getattr(tier, "value", tier)
    if value in ("free", "plus", "self_hosted"):
        return value
    return "unknown"
