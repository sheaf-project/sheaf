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
from sheaf.observability.registry import get_registry

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

DbPoolState = Literal["checked_in", "checked_out"]
StatusClass = Literal["1xx", "2xx", "3xx", "4xx", "5xx"]


# ---------------------------------------------------------------------------
# Wrappers binding every metric to the shared registry
# ---------------------------------------------------------------------------

def _C(name: str, doc: str, labels: list[str] | None = None) -> Counter:
    return Counter(name, doc, labels or [], registry=get_registry())


def _H(
    name: str, doc: str, labels: list[str] | None = None,
    buckets: tuple = HTTP_LATENCY_BUCKETS,
) -> Histogram:
    return Histogram(
        name, doc, labels or [], buckets=buckets, registry=get_registry()
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
        registry=get_registry(),
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
job_consecutive_failures = _G(
    "sheaf_job_consecutive_failures",
    "Consecutive failures of each job since the last success.",
    ["job"],
    multiprocess_mode="max",
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

# ---------------------------------------------------------------------------
# Encryption / data integrity
# ---------------------------------------------------------------------------

decrypt_failures_total = _C(
    "sheaf_decrypt_failures_total",
    "Field-decrypt failures by field. Should always be zero; non-zero "
    "indicates key drift or corruption.",
    ["field"],
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

# ---------------------------------------------------------------------------
# Infra
# ---------------------------------------------------------------------------

db_pool_connections = _G(
    "sheaf_db_pool_connections",
    "SQLAlchemy async pool connection counts.",
    ["state"],
)
redis_up = _G(
    "sheaf_redis_up",
    "1 if Redis PING succeeded on the last gauge refresh, else 0.",
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
