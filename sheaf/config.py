import logging
import sys
from enum import StrEnum
from ipaddress import IPv4Network, IPv6Network, ip_network
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings

logger = logging.getLogger("sheaf")


class SheafMode(StrEnum):
    SELFHOSTED = "selfhosted"
    SAAS = "saas"


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+asyncpg://sheaf:changeme@db:5432/sheaf"

    # Connection pool. Single-process asyncio app: one pool is shared by the
    # request path and every background loop (job runner, dispatcher, import
    # runner, export builder). Defaults sized for one uvicorn process; raise
    # pool_size/max_overflow together with Postgres max_connections if you
    # run more processes, and mind that each process opens its own pool.
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout: int = 30  # seconds to wait for a free connection

    # Request-path Postgres statement_timeout (milliseconds), applied
    # per-transaction inside get_db so a pathological O(history) query can't
    # pin a pooled connection indefinitely. 0 = unlimited. Deliberately does
    # NOT apply to background jobs - see db_job_statement_timeout_ms.
    db_statement_timeout_ms: int = 30000

    # Statement timeout (milliseconds) for background jobs that opt into
    # database.job_session(). Background work (export builds, retention
    # sweeps, analytics) legitimately runs far longer than a request, so this
    # defaults to 0 = unlimited; set a large ceiling if you want a backstop
    # against a runaway job query without capping normal long jobs.
    db_job_statement_timeout_ms: int = 0

    # Readiness probe (/health/ready) per-dependency timeout in seconds. Kept
    # tight so a wedged DB or Redis surfaces to the load balancer fast rather
    # than hanging the health check.
    health_check_timeout_seconds: float = 2.0

    # Redis
    redis_url: str = "redis://redis:6379/0"

    # Encryption
    sheaf_encryption_key: str | None = None
    sheaf_data_dir: Path = Path("data")

    # Auth
    jwt_secret_key: str = "changeme-in-production"
    jwt_access_token_expire_minutes: int = 15
    jwt_refresh_token_expire_days: int = 30
    jwt_algorithm: str = "HS256"
    session_expire_hours: int = 24

    # Mode
    sheaf_mode: SheafMode = SheafMode.SELFHOSTED

    # Build provenance — populated by Docker build args at image build time.
    # Empty when running from source (dev) or from an image built without CI.
    sheaf_git_commit: str = ""
    sheaf_git_tag: str = ""
    sheaf_build_time: str = ""

    # aaS settings
    free_tier_front_retention_days: int = 30
    retention_check_interval_hours: int = 6

    # Account deletion
    account_deletion_grace_days: int = 7
    account_deletion_reminder_days: str = "5,3,1"  # send reminders N days before deletion

    # Unverified account cleanup
    unverified_account_cleanup_days: int = 7  # delete never-verified accounts after N days

    # Scheduled jobs
    job_check_interval_minutes: int = 15  # how often job runner wakes up
    orphan_cleanup_interval_hours: int = 24  # how often orphan file cleanup runs
    job_log_retention_days: int = 30  # how long to keep job run logs

    # Incident-response master kill switch for data-deleting background jobs.
    # Flip to False to halt EVERY job marked `destructive=True` in one move -
    # the "stop all data deletion" lever an operator can reach for while
    # investigating a suspected retention bug, instead of editing each job's
    # interval to 0 one at a time.
    # Non-deleting jobs and operational cleanups are unaffected. The one
    # deliberate exception is the security-event (IP) cleanup below, which is a
    # privacy obligation kept on its own switch so a blanket pause can't silence
    # it.
    destructive_jobs_enabled: bool = True

    # Separate switch for the security-event (IP) cleanup only. That sweep
    # enforces the 30-day IP retention promise (see security_event_retention_days)
    # and is a privacy-policy obligation, so it is deliberately NOT gated by the
    # destructive_jobs_enabled master switch - an incident pause must not leave
    # IPs sitting past their retention window. Set False only if you must stop
    # IP minimisation for a specific, considered reason.
    security_event_cleanup_enabled: bool = True

    # File storage
    storage_backend: str = "filesystem"  # "filesystem" or "s3"
    storage_path: Path = Path("data/files")
    s3_bucket: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_region: str = "us-east-1"
    s3_endpoint: str = ""  # For MinIO/R2 compatibility
    s3_presign_endpoint: str = ""  # External URL for presigned URLs (if different from s3_endpoint)
    s3_public_url: str = ""  # CDN URL prefix, if any
    # Dedicated key for HMAC-signed image URLs. Required when running the
    # signed + CDN paradigm (selfhost-utils/cf-image-worker), because the
    # Cloudflare Worker needs the same key and we don't want to hand it
    # the JWT secret. Left empty, the backend derives the signing key from
    # jwt_secret_key — fine for the non-CDN paradigms.
    file_signing_key: str = ""
    max_upload_size_mb: int = 5
    # Per-purpose overrides. 0 means "inherit max_upload_size_mb" so existing
    # deploys keep the single-knob behaviour until they set these explicitly.
    max_avatar_size_mb: int = 0
    max_bio_image_size_mb: int = 0
    # Global hard cap on request body size (MB) enforced by middleware before
    # the body is buffered anywhere. Must be >= the largest per-endpoint cap
    # (currently the 100MB import endpoint) plus a little multipart overhead.
    max_request_body_size_mb: int = 110
    # OpenPlural import: max size (MB) of foreign data Sheaf cannot model
    # (other apps' `extensions` namespaces, the chat/relationships modules,
    # front_events/front_comments, non-tag taxonomy) that gets preserved as
    # an opaque archive on the system and re-emitted on the next OpenPlural
    # export. Measured on the raw (pre-compression) JSON; over this, the
    # residual is dropped with a warning rather than stored unbounded.
    openplural_max_preserved_mb: int = 8

    # Storage quotas per tier (MB). 0 = unlimited.
    storage_quota_free_mb: int = 50
    storage_quota_plus_mb: int = 500
    storage_quota_selfhosted_mb: int = 0  # unlimited
    # Member limits per tier. 0 = unlimited.
    member_limit_free: int = 512
    member_limit_plus: int = 0  # unlimited
    member_limit_selfhosted: int = 0  # unlimited

    # Per-IMPORT-JOB hard row caps. These bound how many rows of each entity a
    # SINGLE import can create in one transaction - parse-bomb / resource
    # protection (each front, for instance, does a per-row flush), NOT a
    # per-tenant product limit like the member cap above. A crafted or simply
    # huge export can otherwise force hundreds of thousands of inserts in one
    # go; these caps fail the job cleanly first. 0 = unlimited/disabled for
    # that entity. Enforced by every importer before its write loop and
    # predicted at preview. Self-hosters with genuinely large systems can raise
    # them.
    import_max_fronts: int = 100000
    import_max_journal_entries: int = 50000
    import_max_messages: int = 100000
    import_max_revisions: int = 100000
    import_max_polls: int = 10000
    import_max_groups: int = 10000
    import_max_tags: int = 10000
    import_max_custom_fields: int = 10000

    # Revision-history retention caps per tier. 0 = unlimited.
    # Covers both journal entries and member bios under a single cap.
    journal_max_revisions_free: int = 50
    journal_max_revisions_plus: int = 100
    journal_max_revisions_selfhosted: int = 0
    journal_max_revision_days_free: int = 30
    journal_max_revision_days_plus: int = 365
    journal_max_revision_days_selfhosted: int = 0
    # How often the revision-retention GC sweep runs.
    journal_gc_interval_hours: int = 6
    # Debounce/checkpoint window (minutes) for live revision capture. Within
    # this window of the newest unpinned revision's inserted_at, a fresh save
    # REPLACES that revision's captured content in place instead of appending
    # a new row - so a burst of rapid saves collapses into a single checkpoint
    # while a long editing session still accrues a new checkpoint roughly every
    # revision_debounce_minutes. inserted_at is deliberately not refreshed on
    # replace, anchoring the window to when each checkpoint was born. 0 =
    # disabled (every content-changing save appends a row, the old behaviour).
    revision_debounce_minutes: int = 5
    # Notice period before a tier downgrade trims revision history.
    tier_downgrade_grace_days: int = 14

    # Pinned-revision caps per tier (per target — i.e. per journal entry or
    # member bio). 0 = unlimited. Pinned revisions are exempt from the rolling
    # retention sweep and form a separate budget from the count/day caps above.
    pinned_revision_max_per_target_free: int = 3
    pinned_revision_max_per_target_plus: int = 5
    pinned_revision_max_per_target_selfhosted: int = 10

    # Allow external images in bios/descriptions. If False, CSP blocks
    # external image loading — only hosted uploads are displayed.
    allow_external_images: bool = True

    # Global toggle for image uploads. When False, only admins and users
    # with can_upload_images=True on their account can upload.
    allow_image_uploads: bool = True
    # Bio-image toggle, narrower than allow_image_uploads. When False, avatar
    # uploads still work but bio/description embeds are rejected. Admins and
    # per-user can_upload_images still bypass. The master switch
    # allow_image_uploads wins if it is False.
    allow_bio_images: bool = True

    # Master switch for animated avatars (GIF / animated WebP). When False
    # (default), uploads of animated formats are flattened to their first
    # frame and re-encoded as static WebP. When True, eligibility is decided
    # per-user by tier + the can_upload_animated_images override.
    allow_animated_uploads: bool = False
    # Longest-edge cap (px) for stored images. Anything larger is downscaled
    # during the server-side normalization pass; aspect ratio preserved.
    max_image_dimension: int = 4096
    # Frame-count cap for animated uploads. Rejected outright above this.
    max_animated_frames: int = 100
    # Decompression-bomb guard: reject before decoding when the declared
    # pixel-count * 4 bytes would exceed this. 100 MB default.
    max_animated_decoded_bytes: int = 100 * 1024 * 1024
    # Concurrency cap on the Pillow normalisation pass. Each in-flight
    # normalize_image call can hold up to `max_animated_decoded_bytes`
    # of decoded bitmap in the threadpool worker, so unbounded
    # concurrency on a small instance can OOM. 4 is a reasonable
    # default for a 2 vCPU box; raise when the instance class grows
    # and memory budget allows. Excess uploads queue at the semaphore
    # rather than failing — paired with the per-user rate limit on
    # the endpoint, total backlog stays bounded.
    image_normalize_concurrency: int = 4
    # Per-import cap on images restored from an export-with-images
    # archive. The storage quota bounds restored BYTES but not how many
    # normalize_image passes one job can demand: a zip stuffed with
    # thousands of tiny PNGs would otherwise buy hours of Pillow CPU on
    # the import lane for pennies of quota. Far above any realistic
    # export; an import that hits it surfaces a warning and strips the
    # remaining references rather than failing. Selfhosters with very
    # large systems can raise it.
    max_import_restored_images: int = 20_000
    # Concurrency cap on Argon2 password hashing/verification. Each
    # in-flight hash holds ~64MiB at default params, so this bounds both
    # CPU and memory under a login burst; excess callers queue at the
    # semaphore (see sheaf/auth/passwords.py) rather than failing.
    password_hash_concurrency: int = 4

    # Image serving mode: "signed" (default) or "unsigned".
    # "signed": HMAC-signed serve URLs with expiry — prevents hotlinking.
    #   S3: private bucket; serve endpoint redirects to a presigned S3 URL.
    #   Filesystem: HMAC token required on all serve requests.
    # "unsigned": no token required — anyone with a URL can access files.
    #   Easier to set up, but effectively provides free image hosting.
    #   For S3: set S3_PUBLIC_URL to a Cloudflare-proxied domain and use
    #   Cloudflare hotlink protection rules as the alternative mechanism.
    image_serving: str = "signed"

    # Signed URL expiry window in seconds. Window-based: all requests within
    # the same window get the same URL, enabling browser image caching.
    # Must be a clean divisor of a day (e.g. 3600). Default: 1 hour.
    file_url_expiry_seconds: int = 3600

    # Email
    email_backend: str = "none"  # "none", "smtp", "ses", or "sendgrid"

    # Soft-bounce tolerance. A soft bounce is transient (greylisting, full
    # mailbox, temporary MTA failure) and routinely a false positive - our
    # own rspamd greylist trips it on the first delivery attempt. So a
    # single soft bounce must NOT block mail: the address is only flagged
    # undeliverable once `email_soft_bounce_count` reaches this threshold
    # without an intervening successful delivery (which resets the count).
    # A `delivered` provider event clears the soft state entirely.
    email_soft_bounce_threshold: int = 5
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_tls: bool = True
    ses_region: str = ""
    ses_from: str = ""
    ses_access_key: str = ""
    ses_secret_key: str = ""
    # SQS queue that receives SES bounce/complaint events (via SNS).
    # When unset, the SES events processor job is disabled. The queue and
    # SNS subscription are provisioned in sheaf-infra (Terraform).
    ses_events_queue_url: str = ""

    # SendGrid
    sendgrid_api_key: str = ""
    sendgrid_from: str = ""
    # Shared secret for the SendGrid Event Webhook. Configure SendGrid to
    # POST to /v1/webhooks/sendgrid/events?token=<this value>.
    # When empty, the webhook endpoint returns 404.
    # Legacy fallback only — prefer the signed-webhook key below.
    sendgrid_webhook_secret: str = ""
    # Base64 DER ECDSA public key for SendGrid's Signed Event Webhook.
    # Enable "Signed Event Webhook" in the SendGrid UI and paste the
    # verification key here. When set, requests must carry a valid
    # signature and the query-string token is ignored.
    sendgrid_webhook_public_key: str = ""
    # Max age (seconds) of a signed webhook request before it's rejected
    # as a possible replay.
    sendgrid_webhook_max_skew_seconds: int = 600

    # SMTP2GO webhook. SMTP2GO does not sign payloads (no HMAC), so the
    # endpoint is guarded by a shared secret in the URL: configure the
    # SMTP2GO webhook to POST to
    # /v1/webhooks/smtp2go/events?token=<this value> with JSON output.
    # When empty, the webhook endpoint returns 404. Pair with SMTP2GO's
    # IP allowlist (webhooks.smtp2go.com) at the proxy for defence in
    # depth. Feeds the same deliverability lifecycle as the SES/SendGrid
    # handlers.
    smtp2go_webhook_secret: str = ""

    # Registration
    registration_mode: str = "open"  # "open", "approval", "invite", "closed"
    invite_codes_enabled: bool = False  # Accept invite codes in open/approval modes too
    email_verification: str = "off"  # "off" or "required"
    password_reset_rate_limit_minutes: int = 15
    # Public base URL of the instance. Required when email is enabled (used in
    # verification/reset links); also seeds the JWT issuer claim and decides
    # whether auth cookies carry the Secure flag. Empty = assume HTTPS (Secure).
    # An explicit http:// URL opts in to non-Secure cookies for plain-HTTP dev.
    sheaf_base_url: str = ""

    # Shared Universal Link / App Link host for mobile_push activation
    # URLs. Every instance routes mobile_push redemption links through
    # this host because the mobile app's associated-domains entitlement
    # is baked in at build time and trusts only one origin. The default
    # points at the public sheaf.sh website; self-hosters who fork and
    # republish the apps override this to their own host.
    mobile_link_base_url: str = "https://sheaf.sh"

    # Admin dashboard step-up authentication level.
    # "none"     — any admin can access the dashboard immediately.
    # "password" — admin must re-enter their password (valid for 2 hours).
    # "totp"     — admin must enter a TOTP code; requires TOTP to be enabled on the account.
    # Applies to session-cookie and JWT auth. API keys with admin:* scope are exempt.
    admin_auth_level: str = "none"

    # Admin bootstrap — comma-separated emails, auto-promoted to is_admin on startup.
    # Stored as a raw string because pydantic-settings v2 JSON-parses list[str] fields
    # before validators run, silently dropping plain comma-separated values.
    # Env var: SHEAF_ADMIN_EMAILS=you@example.com,colleague@example.com
    sheaf_admin_emails: str = ""

    @property
    def admin_email_list(self) -> list[str]:
        """Return sheaf_admin_emails as a parsed list."""
        return [e.strip() for e in self.sheaf_admin_emails.split(",") if e.strip()]

    # Rate limiting
    rate_limit_enabled: bool = True
    rate_limit_global_per_ip: int = 600  # requests per window (all endpoints combined)
    rate_limit_global_window: int = 60  # window in seconds

    # Per-account combined write rate limit. A single shared bucket across
    # the whole mutating surface (fronts, journals, messages, members,
    # reminders): one account cannot exceed this many writes per minute in
    # total, regardless of how the writes split across endpoints or whether
    # they arrive over a session, a JWT, or an API key (all three draw down
    # the same per-account budget). Under preserve-by-default this bounds a
    # looping client from creating unbounded rows. This is DB-protection, not
    # a product limit - self-hosters running a heavy integration can raise it
    # or set 0 to disable, but it is on by default so a buggy client benefits
    # from the bound too. 0 = disabled.
    write_rate_per_user_per_min: int = 60

    # Per-SYSTEM front-switch guard. Separate from the per-user write limit:
    # keyed on the system (which may have several legitimate writers), it
    # specifically catches a stuck switch-client or looping integration
    # hammering POST /fronts. A token bucket allows a sustained rate of
    # front_switch_rate_per_system_per_min with short bursts up to
    # front_switch_rate_burst absorbed. Also DB-protection, not a product
    # limit; on by default. Raise or disable for a system that genuinely
    # switches very fast. 0 (rate) = disabled.
    front_switch_rate_per_system_per_min: int = 20
    front_switch_rate_burst: int = 10

    # Per-user rate-limit hit history (admin abuse triage). Blocked
    # checks attributable to an authenticated user are recorded to a
    # capped Redis list so an admin can see what an account has tripped
    # recently. Bounded twice: max entries per user, and a retention
    # TTL in hours. Disable to record nothing (the read endpoint then
    # just returns empty histories).
    rate_limit_history_enabled: bool = True
    rate_limit_history_hours: int = 48
    rate_limit_history_max_entries: int = 200

    # Per-account login lockout. Any combination of wrong-password and
    # wrong-TOTP attempts counts. On reaching max_failures, the account is
    # locked for lockout_minutes. A successful login clears both fields;
    # attempts arriving after an expired lockout reset the counter instead
    # of incrementing, so the user isn't instantly re-locked on one typo.
    login_max_failures: int = 10
    login_lockout_minutes: int = 15

    # Trusted proxies — comma-separated IPs and/or CIDR ranges that are allowed
    # to set X-Forwarded-For. Only these peers' forwarded headers are trusted
    # for rate limiting and IP logging. If empty, X-Forwarded-For is never read
    # (direct IP is used).
    # Common values: "127.0.0.1", "172.16.0.0/12" (docker-compose bridge range),
    # "10.0.0.0/8", "::1"
    trusted_proxies: str = ""

    @field_validator("trusted_proxies")
    @classmethod
    def _validate_trusted_proxies(cls, v: str) -> str:
        """Fail fast at startup if any entry isn't a valid IP or CIDR."""
        if not v:
            return v
        for entry in v.split(","):
            entry = entry.strip()
            if not entry:
                continue
            try:
                ip_network(entry, strict=False)
            except ValueError as exc:
                raise ValueError(
                    f"Invalid entry in TRUSTED_PROXIES: {entry!r}. "
                    f"Expected an IP or CIDR (e.g. 127.0.0.1 or 172.16.0.0/12). "
                    f"Parse error: {exc}"
                ) from exc
        return v

    @property
    def trusted_proxy_networks(self) -> list[IPv4Network | IPv6Network]:
        """Return trusted_proxies parsed as a list of ip_network objects.

        A bare IP parses as a /32 (or /128) network, so membership checks
        uniformly use `in` against this list.
        """
        if not self.trusted_proxies:
            return []
        return [
            ip_network(entry.strip(), strict=False)
            for entry in self.trusted_proxies.split(",")
            if entry.strip()
        ]

    # Extra origins allowed to make cookie-authenticated mutations,
    # comma-separated (e.g. "https://app.example.net"). Same-host requests
    # and SHEAF_BASE_URL are always allowed; this is the escape hatch for
    # multi-origin deployments. Scheme is ignored; the host[:port] is what
    # gets compared.
    csrf_trusted_origins: str = ""

    # Legal links for the footer (optional). Empty = hide.
    terms_url: str = ""
    privacy_url: str = ""

    # Operator support contact, surfaced on the in-app Support page
    # (optional). Each is independent; the whole operator section hides
    # if all are empty. status_url is the operator's own status page -
    # a selfhoster's status page is theirs, so it's set here rather than
    # baked into the static project section.
    support_email: str = ""
    support_url: str = ""
    support_note: str = ""
    status_url: str = ""
    # Path to a file of operator-authored freeform text rendered on the
    # Support page. Basic markdown is supported; any raw HTML in the file is
    # stripped server-side at load time (see read_custom_support_text), so
    # the API never emits tags and no client has to be trusted to sanitise.
    # Re-read when the file's mtime/size changes, so edits land without a
    # restart. Empty = nothing extra shown.
    custom_support_text_file: str = ""

    # Captcha (signup gate; optionally login).
    # Provider: "" (disabled) | "altcha". Altcha is in-process proof-of-work
    # with no third-party dependency — see sheaf/services/captcha.py.
    captcha_provider: str = ""
    altcha_hmac_key: str = ""
    # PoW cost (PBKDF2 iteration count). Higher = harder. ~50k ≈ low-single-digit
    # seconds on modern hardware; 500k starts to feel sluggish. Tune up if you
    # see abuse.
    altcha_complexity: int = 50000
    # Signup is always gated when CAPTCHA_PROVIDER is set. Login is opt-in
    # because it's the hotter UX path and captchas on login are a friction
    # trade-off you may not want unless under active credential-stuffing.
    captcha_on_login: bool = False

    # Front-change notifications
    notifications_dispatch_interval_seconds: int = 5

    # Import runner tick interval. Short by default because a user
    # who just clicked "import" expects something to happen within a
    # few seconds, not a minute. Empty-queue ticks are a single indexed
    # query returning no rows.
    import_runner_interval_seconds: int = 5

    # Whether the in-process import-runner loop starts at app boot.
    # On in production. The test stack flips this off so the import
    # tests can drive the runner deterministically (manually, often
    # with a stubbed PK API) without a live loop racing them.
    import_runner_enabled: bool = True

    # How long terminal ImportJob rows live before the cleanup job
    # deletes them. The uploaded payload blob is wiped at finalize
    # time independently; this only controls the user-visible report.
    # 30 days matches the job_runs log retention so 'what was happening
    # around the same time' queries line up.
    import_job_retention_days: int = 30

    # How long terminal notification_outbox rows are kept for audit before
    # the cleanup sweep deletes them. Both delivered and dropped rows
    # (filtered out, revoked, permanent failure) stamp delivered_at, so a
    # single age cutoff covers everything in a done state.
    notification_outbox_retention_days: int = 30

    # How long security-event rows (login attempts, registrations,
    # password resets/changes, with originating IP) are kept before the
    # cleanup sweep deletes them. IP is personal data, so a bounded
    # window does the minimisation: long enough to investigate an abuse
    # report that lands weeks late, short enough not to hoard. Operators
    # with a longer legal basis can raise it; self-hosters set their own.
    security_event_retention_days: int = 30

    # Account activity log (activity_events) retention. Generous default: it
    # is the user's own record of consequential/automated actions and carries
    # no IP, so the minimisation pressure is lower than security_events.
    # Bounded only so the table doesn't grow forever.
    activity_event_retention_days: int = 365

    # A job stuck in `running` longer than this is presumed orphaned by
    # a crashed worker; the recovery sweep resets it to `pending` for a
    # retry. Generous — a large PluralKit API import paginating switch
    # history legitimately runs tens of seconds, never minutes.
    import_stale_running_minutes: int = 15
    # Export builds that crash or get deployed over leave the job RUNNING
    # forever; rows older than this are reset to pending (or failed after
    # repeated stalls). Generous: a huge image-laden export can be slow.
    export_stale_running_minutes: int = 30
    # Outbox rows claimed by a dispatcher that died before delivering are
    # eligible for re-claim after this lease. Deliveries finish in seconds;
    # the lease only has to be comfortably above worst-case delivery time.
    notifications_claim_lease_minutes: int = 15
    # Exactly one replica runs the background loops, elected via a
    # Postgres advisory lock. Disable to restore run-everywhere behaviour
    # (single-instance deploys never notice either way).
    leader_election_enabled: bool = True
    activation_code_ttl_days: int = 7
    # VAPID keys for web push. Generate with `vapid --gen` (py-vapid) or any
    # WebPush helper. Empty = web_push destination type is rejected.
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    # Contact URI for push services (mailto:ops@example.com or https://...).
    # Required when web push is enabled.
    vapid_subject: str = ""
    # Pushover app token (issued by pushover.net) used for the shared,
    # deployment-wide Pushover app. Empty = Pushover destination type is
    # rejected with 501 unless a recipient supplies their own app_token via
    # the channel's destination_config (BYO mode bypasses both the absent
    # default and the monthly cap).
    pushover_app_token: str = ""
    # Monthly cap on shared-app Pushover deliveries. Pushover charges per app
    # per month: 10000 free, then $50/10k extra (one-off, not subscription).
    # Sheaf tracks deployment-wide usage in Redis keyed by YYYY-MM and
    # transient-fails shared-app deliveries once the cap is hit. Channels
    # with BYO app_token bypass this counter entirely. Set to 0 to disable
    # tracking and let Pushover-side enforcement be the only ceiling.
    pushover_max_per_month: int = 10000
    # Minimum debounce_seconds for shared-app Pushover channels. One chatty
    # system can otherwise burn the whole monthly cap for everyone on the
    # instance — 30 minutes is a reasonable baseline that still lets active
    # users get reasonably timely pings without budget runaway. BYO channels
    # are exempt; they get whatever debounce the recipient configured.
    pushover_shared_app_min_debounce_seconds: int = 1800
    # Per-user-tier monthly Pushover allowance on the shared app. Stops one
    # Sheaf user from burning everyone else's allotment within the global
    # cap. 0 = unlimited (per-user check skipped for that tier; the
    # deployment-wide cap is the only ceiling). BYO channels bypass this
    # too — they're on the recipient's own Pushover quota, not ours.
    pushover_user_max_per_month_free: int = 100
    pushover_user_max_per_month_plus: int = 1000
    pushover_user_max_per_month_self_hosted: int = 0
    # Username + avatar URL Discord renders for our webhook deliveries.
    # avatar URL must be publicly reachable PNG/JPEG (Discord rejects SVG).
    # Empty avatar = falls back to the webhook's default avatar; empty
    # username = "Sheaf".
    discord_webhook_username: str = "Sheaf"
    discord_webhook_avatar_url: str = ""
    # User-Agent sent for outbound webhook deliveries.
    webhook_user_agent: str = "Sheaf-Notifications/1.0"
    # How many concurrent dispatches per destination type. Cheap to raise;
    # bound by your egress + downstream rate limits.
    notifications_concurrency_web_push: int = 10
    notifications_concurrency_webhook: int = 5
    notifications_concurrency_ntfy: int = 5
    notifications_concurrency_pushover: int = 5
    notifications_concurrency_fcm: int = 10
    notifications_concurrency_apns: int = 10

    # Mobile push (FCM + APNs). Both creds are long-term static secrets,
    # shaped like the existing VAPID keys. Each accepts a path or inline
    # content; path wins when both are set.
    #
    # FCM service account JSON (download from Firebase Console -> Project
    # Settings -> Service Accounts). Empty = FCM destination type is
    # rejected with 501. The FCM project id is read from the JSON itself.
    fcm_service_account_path: str = ""
    fcm_service_account_json: str = ""

    # APNs auth (Apple Developer -> Certificates, Identifiers & Profiles
    # -> Keys -> APNs). One .p8 key authenticates against both
    # api.sandbox.push.apple.com and api.push.apple.com; the dispatcher
    # picks the host per-device based on the apns_dev / apns_prod
    # platform value. Any of TEAM_ID / KEY_ID / BUNDLE_ID / (P8_PATH or
    # P8_KEY) being empty disables APNs (channel creation rejects with
    # 501).
    apns_team_id: str = ""
    apns_key_id: str = ""
    apns_bundle_id: str = ""
    # Opt-in flag for accepting apns_dev tokens / channels. Production
    # deployments should leave this off so dev-environment device tokens
    # can't be registered against the prod backend (which would orphan
    # them anyway, since the prod APNs host bounces sandbox tokens).
    # Flip on for dev / staging / self-hosted-with-TestFlight setups.
    apns_dev_enabled: bool = False
    # Optional override used as the apns-topic header for apns_dev
    # devices when set. Falls back to apns_bundle_id when unset. Only
    # relevant if dev and prod builds ever ship under different bundle
    # ids (a common pattern when supporting side-by-side installs).
    apns_bundle_id_dev: str = ""
    apns_p8_path: str = ""
    apns_p8_key: str = ""

    # Per-account soft cap on push_device_token rows. When exceeded on
    # register, the row with the oldest last_seen_at is evicted before
    # the new one is inserted. 0 = unlimited.
    notifications_mobile_tokens_per_account_max: int = 20

    # Polls
    # All three premium levers (close-window, retention, concurrent open
    # polls) are tier-scaled with 0 == "no upper bound". Frontend pulls
    # the effective per-user limits from /v1/polls/server-config so the
    # create form can clamp + show upsell hints.
    #
    # Close-window: minimum is shared, maximum is per tier.
    poll_min_close_seconds: int = 3600
    poll_max_close_seconds_free: int = 14 * 86400
    poll_max_close_seconds_plus: int = 90 * 86400
    poll_max_close_seconds_self_hosted: int = 0
    # Default retention (days a closed poll is kept before purge). Used
    # when the caller doesn't specify per-poll retention. The per-tier
    # MAX caps the value the user can request.
    poll_retention_default_days: int = 30
    poll_max_retention_days_free: int = 30
    poll_max_retention_days_plus: int = 180
    poll_max_retention_days_self_hosted: int = 0
    # Concurrent open polls per system. Counted against polls whose
    # closes_at is in the future.
    poll_max_concurrent_open_free: int = 5
    poll_max_concurrent_open_plus: int = 20
    poll_max_concurrent_open_self_hosted: int = 0
    # How often the poll cleanup job runs.
    poll_cleanup_interval_hours: int = 6

    # Async data export jobs
    # Sync GET /v1/export assembles the whole account in memory and
    # JSON-serialises it on the event loop. Above this many rows (members +
    # fronts + journal entries + messages) it refuses and points the user at
    # the async POST /v1/export/jobs flow, which streams to a file on disk.
    # Guards the event loop against a multi-hundred-MB in-process serialise.
    # 0 = no limit (the async path is always available regardless).
    export_sync_max_rows: int = 50000
    # Lifetime of a generated export file before it's auto-deleted from
    # storage and the job row marked EXPIRED. 72h gives the user three days
    # to grab it; long enough for "I'll do this from my desktop later",
    # short enough to limit blast radius if a download URL leaks.
    export_job_ttl_hours: int = 72
    # How often the cleanup worker sweeps for expired jobs.
    export_cleanup_interval_seconds: int = 3600
    # How often the build worker polls for pending export jobs to assemble.
    # Exports are a deferred "request now, download/email later" flow with no
    # wake signal, so a minute of pickup latency is fine - and since the job
    # runner wakes at the smallest registered interval, a tight value here
    # spins the whole registry loop needlessly.
    export_build_interval_seconds: int = 60
    # Where the build worker drops its temp zip while assembling.
    # Empty = use the system default (tempfile picks $TMPDIR or /tmp).
    # Set this when running on small root volumes or when the system
    # tempdir is on tmpfs — exports of users with many images can
    # reach hundreds of MB on disk before they're uploaded out.
    export_build_tmp_dir: str = ""
    # Per-user concurrency: refuse a new export request when one is still
    # pending/running. Stops users (or attackers with a hijacked session)
    # from queueing many large exports back-to-back.
    export_max_concurrent_per_user: int = 1
    # Optional dedicated S3 bucket for exports. Strongly recommended in
    # production: lets you set an S3 lifecycle expiry rule (belt-and-braces
    # with the cleanup worker) AND lets you point exports at an endpoint
    # that bypasses any CDN fronting on your image bucket — exports
    # contain decrypted personal data that shouldn't pass through CDN
    # TLS termination. When unset, exports fall back to the main
    # `s3_bucket` (fine for dev, not recommended for production).
    s3_export_bucket: str = ""
    s3_export_endpoint: str = ""
    s3_export_presign_endpoint: str = ""

    # Shield mode (Cloudflare break-glass DDoS posture).
    # When enabled, the operator's cf-shield script POSTs to
    # /v1/internal/shield-mode/state to flip Sheaf's view of shield
    # state. Users with disable_cdn_during_ddos=true get their sessions
    # invalidated on the up edge so they don't unwittingly traverse the
    # CDN. Default off so selfhosters without a Cloudflare break-glass
    # setup never see the toggle in their UI and never have to think
    # about the webhook.
    shield_mode_enabled: bool = False
    # HMAC shared secret with the cf-shield script. Required when
    # shield_mode_enabled is true; ignored otherwise. Used to verify
    # the webhook signature on /v1/internal/shield-mode/state.
    shield_mode_webhook_secret: str = ""

    # Prometheus /metrics exposure.
    # bind:
    #   "main"      — /metrics mounted on the API listener (always token-gated).
    #   "separate"  — second listener on metrics_bind_host:metrics_bind_port.
    #                 Default for safety: 127.0.0.1, no auth required at that bind.
    #                 Flip to a non-loopback bind for remote scraping and turn auth on.
    #   "disabled"  — endpoint not exposed anywhere.
    metrics_enabled: bool = True
    metrics_bind: str = "separate"
    metrics_bind_host: str = "127.0.0.1"
    metrics_bind_port: int = 8090
    metrics_auth: str = "none"  # "none" | "token"
    metrics_token: str = ""
    # The gauge refresher is registered with the job runner. The runner
    # itself only wakes every job_check_interval_minutes, so values below
    # that are effectively rounded up. 60 seconds is a reasonable floor
    # for a per-15-minute loop without pretending we can refresh faster.
    metrics_gauge_refresh_seconds: int = 60
    # Fast-gauges refresh interval for the small set of metrics that
    # genuinely move fast (redis_up, db pool connection counts, outbox
    # depth). Bounded below by job_check_interval_minutes * 60 same as
    # the slow refresh; values below that effectively round up.
    metrics_fast_gauge_refresh_seconds: int = 10

    # Server
    sheaf_port: int = 8000
    sheaf_host: str = "0.0.0.0"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    def get_encryption_key(self) -> bytes:
        """Get or auto-generate the encryption key (32 bytes, hex-encoded on disk).

        This key is required long-term — it encrypts emails / TOTP secrets AND
        keys the blind-index used to look up users by email at login. Losing
        it means nobody can log in, even with the correct password. Set
        SHEAF_ENCRYPTION_KEY explicitly in production rather than relying on
        the auto-generated file in the data volume.
        """
        if self.sheaf_encryption_key:
            return self.sheaf_encryption_key.encode()

        key_path = self.sheaf_data_dir / "encryption.key"

        if key_path.exists():
            return key_path.read_bytes().strip()

        # Auto-generate 32 random bytes, hex-encode for storage
        import secrets

        self.sheaf_data_dir.mkdir(parents=True, exist_ok=True)
        key = secrets.token_hex(32).encode()
        key_path.write_bytes(key)
        key_path.chmod(0o600)

        logger.warning("=" * 72)
        logger.warning(
            "AUTO-GENERATED ENCRYPTION KEY — BACK THIS UP OR YOU "
            "LOSE ALL ENCRYPTED DATA FOREVER"
        )
        logger.warning(
            "This key encrypts emails and TOTP secrets AND keys the blind-"
            "index the login endpoint uses to find users by email. Losing "
            "it means no one will be able to log in."
        )
        # Log only the path, never the value — log output routinely ends
        # up in journald, container logs, and log shippers, none of which
        # should hold the key material. Same posture as the JWT autogen.
        logger.warning("Key file: %s", key_path.resolve())
        logger.warning(
            "Set SHEAF_ENCRYPTION_KEY in .env to use your own key "
            "and suppress this warning."
        )
        logger.warning("=" * 72)

        return key


settings = Settings()


# Defensive cap on the custom support text so a huge (or runaway) file
# can't bloat the public /auth/config response. Generous for a blurb.
_MAX_SUPPORT_TEXT_CHARS = 20_000

# Cache for the operator's custom support text, keyed on the source file's
# (mtime_ns, size) so edits are reflected without a restart while a hot
# /auth/config endpoint isn't re-reading the file on every call.
_support_text_cache: tuple[str, tuple[int, int], str | None] | None = None


def _strip_html(text: str) -> str:
    """Remove every HTML tag from text, keeping the inner text content.

    The custom support text is markdown, but the file is operator-authored
    and we don't want to trust any consumer to neutralise embedded HTML. So
    we strip it here, at the trust boundary, with an empty tag allowlist:
    `<script>x</script>` becomes `x`, markdown syntax is left untouched. Any
    client (web, mobile, third party) then only ever receives tag-free
    markdown.
    """
    import nh3

    return nh3.clean(text, tags=set(), attributes={})


def read_custom_support_text() -> str | None:
    """Return the operator's custom support text (markdown, HTML stripped).

    Reads CUSTOM_SUPPORT_TEXT_FILE, caching on the file's mtime+size.
    Returns None when the setting is unset, the file is missing/unreadable,
    or it's empty. HTML is stripped here at load time so the API never
    emits raw tags regardless of how a client renders the result.
    """
    global _support_text_cache
    path_str = settings.custom_support_text_file
    if not path_str:
        return None
    p = Path(path_str)
    try:
        st = p.stat()
    except OSError:
        return None
    sig = (st.st_mtime_ns, st.st_size)
    if (
        _support_text_cache is not None
        and _support_text_cache[0] == path_str
        and _support_text_cache[1] == sig
    ):
        return _support_text_cache[2]
    try:
        raw = p.read_text(encoding="utf-8")[:_MAX_SUPPORT_TEXT_CHARS]
    except OSError:
        return None
    result = _strip_html(raw).strip() or None
    _support_text_cache = (path_str, sig, result)
    return result


_JWT_SECRET_DEFAULT = "changeme-in-production"


def _maybe_auto_generate_jwt_secret() -> None:
    """Auto-generate and persist a JWT signing secret if the operator left
    the default in place.

    Mirrors the encryption-key pattern: on first start, write a random
    secret to the data dir with 0600 perms and use it. SaaS mode still
    refuses to start with the default, but selfhost previously only
    warned — that path now resolves to a real, persistent secret so the
    next restart finds it instead of forging a new one (which would
    invalidate every issued JWT).
    """
    if settings.jwt_secret_key != _JWT_SECRET_DEFAULT:
        return  # operator set their own; nothing to do

    key_path = settings.sheaf_data_dir / "jwt_secret"

    if key_path.exists():
        secret = key_path.read_text().strip()
        if secret:
            settings.jwt_secret_key = secret
            return

    import secrets as _secrets

    settings.sheaf_data_dir.mkdir(parents=True, exist_ok=True)
    secret = _secrets.token_urlsafe(48)
    key_path.write_text(secret)
    key_path.chmod(0o600)
    settings.jwt_secret_key = secret

    logger.warning("=" * 72)
    logger.warning(
        "AUTO-GENERATED JWT_SECRET_KEY — back this up alongside the "
        "encryption key. Losing it invalidates every issued JWT but is "
        "otherwise recoverable (users just have to log in again)."
    )
    logger.warning("Secret file: %s", key_path.resolve())
    logger.warning(
        "Set JWT_SECRET_KEY in .env to use your own value and suppress "
        "this warning."
    )
    logger.warning("=" * 72)


def _validate_settings() -> None:
    """Check for insecure defaults and warn loudly."""
    # Resolve JWT secret first so the default-check below only fires
    # when the operator explicitly set it back to the placeholder.
    _maybe_auto_generate_jwt_secret()

    problems = []
    if settings.jwt_secret_key == _JWT_SECRET_DEFAULT:
        problems.append("JWT_SECRET_KEY is set to the default value")
    if "changeme" in settings.database_url:
        problems.append("DATABASE_URL contains default password")

    if (
        settings.image_serving == "unsigned"
        and settings.storage_backend == "s3"
        and not settings.s3_public_url
    ):
        logger.warning(
            "IMAGE_SERVING=unsigned with S3 and no S3_PUBLIC_URL: files are publicly "
            "accessible via direct S3 URLs. Set S3_PUBLIC_URL to a Cloudflare-proxied "
            "domain with hotlink protection, or switch to IMAGE_SERVING=signed."
        )

    if settings.email_verification == "required" and settings.email_backend == "none":
        logger.critical(
            "EMAIL_VERIFICATION=required but EMAIL_BACKEND=none — "
            "cannot send verification emails. Set EMAIL_BACKEND to smtp or ses."
        )
        sys.exit(1)

    if settings.email_backend != "none" and not settings.sheaf_base_url:
        logger.critical(
            "EMAIL_BACKEND is configured but SHEAF_BASE_URL is not set — "
            "email links require a base URL. Set SHEAF_BASE_URL (e.g. https://sheaf.example.com)."
        )
        sys.exit(1)

    # Legal links: not required to start, but strongly encouraged for any
    # public-facing instance. Missing links mean users can't see what they're
    # agreeing to and operators may have GDPR/consent compliance gaps.
    if not settings.terms_url:
        logger.warning(
            "TERMS_URL is not set. The login/registration page will not show a "
            "Terms of Service link. Strongly recommended for public instances."
        )
    if not settings.privacy_url:
        logger.warning(
            "PRIVACY_URL is not set. The login/registration page will not show a "
            "Privacy Policy link. Strongly recommended for public instances, and "
            "may be required under GDPR/CCPA depending on jurisdiction."
        )

    # Custom support text: warn (don't refuse) if the operator pointed at a
    # file we can't read, so a typo'd path surfaces at startup instead of as
    # a silently-missing card on the Support page.
    if settings.custom_support_text_file:
        try:
            Path(settings.custom_support_text_file).read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning(
                "CUSTOM_SUPPORT_TEXT_FILE=%s could not be read (%s). The Support "
                "page will not show the custom text until this is fixed.",
                settings.custom_support_text_file,
                exc,
            )

    if settings.captcha_provider and settings.captcha_provider != "altcha":
        logger.critical(
            "CAPTCHA_PROVIDER=%s is not recognised. Supported: altcha. "
            "Leave empty to disable.",
            settings.captcha_provider,
        )
        sys.exit(1)

    if settings.captcha_provider == "altcha" and not settings.altcha_hmac_key:
        logger.critical(
            "CAPTCHA_PROVIDER=altcha but ALTCHA_HMAC_KEY is not set. "
            "Generate a strong random string (e.g. `openssl rand -hex 32`) and set it."
        )
        sys.exit(1)

    if settings.registration_mode == "approval" and settings.email_backend == "none":
        logger.warning(
            "REGISTRATION_MODE=approval with EMAIL_BACKEND=none — "
            "users won't receive notification when approved. Consider configuring email."
        )

    if settings.metrics_enabled and settings.metrics_bind not in ("main", "separate", "disabled"):
        logger.critical(
            "METRICS_BIND=%s is not recognised. Supported: main, separate, disabled.",
            settings.metrics_bind,
        )
        sys.exit(1)

    if settings.metrics_enabled and settings.metrics_auth not in ("none", "token"):
        logger.critical(
            "METRICS_AUTH=%s is not recognised. Supported: none, token.",
            settings.metrics_auth,
        )
        sys.exit(1)

    # Token is required when metrics_auth=token OR when metrics_bind=main
    # (mounting on the public listener always forces auth, regardless of the
    # metrics_auth setting). Without it, operators would discover the missing
    # token at first scrape attempt rather than at startup.
    _metrics_needs_token = settings.metrics_enabled and (
        settings.metrics_auth == "token" or settings.metrics_bind == "main"
    )
    if _metrics_needs_token and not settings.metrics_token:
        logger.critical(
            "METRICS_TOKEN is required when METRICS_AUTH=token or METRICS_BIND=main. "
            "Generate one with `openssl rand -hex 32` and set it."
        )
        sys.exit(1)

    # Unauthenticated /metrics on a non-loopback / non-RFC1918 bind is a
    # foot-gun: the auth funnel, lockout counters, and rate-limit signal
    # become readable by anything that can reach the port. We can't
    # judge external reachability with full confidence though — a docker
    # container binding 0.0.0.0 is still fronted by the host's own port-
    # publish posture, and the .env config that points to a non-private
    # bind may be deliberate. So warn, don't refuse.
    if (
        settings.metrics_enabled
        and settings.metrics_bind == "separate"
        and settings.metrics_auth == "none"
    ):
        from ipaddress import ip_address

        try:
            _bind_ip = ip_address(settings.metrics_bind_host)
            # `is_private` includes 0.0.0.0/8 (the "this network" range)
            # which would falsely class 0.0.0.0 as safe — but as a listen
            # address that's the bind-everywhere wildcard. Same goes for
            # :: on v6. Exclude unspecified explicitly.
            _bind_is_safe = (
                not _bind_ip.is_unspecified
                and (_bind_ip.is_loopback or _bind_ip.is_private)
            )
        except ValueError:
            # Hostname rather than an IP — can't judge reachability;
            # warn so the operator confirms they meant to skip auth.
            _bind_is_safe = False
        if not _bind_is_safe:
            logger.warning(
                "METRICS_BIND_HOST=%s with METRICS_AUTH=none: anything that "
                "can reach this address+port will see auth-funnel, lockout, "
                "and rate-limit metrics. Confirm the perimeter (firewall / "
                "container port-publish posture) is doing the work — or set "
                "METRICS_AUTH=token + METRICS_TOKEN.",
                settings.metrics_bind_host,
            )

    if settings.shield_mode_enabled and not settings.shield_mode_webhook_secret:
        logger.critical(
            "SHIELD_MODE_ENABLED=true but SHIELD_MODE_WEBHOOK_SECRET is not set. "
            "The cf-shield script needs a shared HMAC secret to authenticate the "
            "state-flip webhook. Generate one with `openssl rand -hex 32` and set "
            "it in both the backend env and the operator's SSM parameter."
        )
        sys.exit(1)

    if settings.sheaf_mode == SheafMode.SAAS and problems:
        logger.critical("REFUSING TO START IN SAAS MODE WITH INSECURE DEFAULTS:")
        for p in problems:
            logger.critical("  - %s", p)
        sys.exit(1)
    elif problems:
        for p in problems:
            logger.warning("INSECURE DEFAULT: %s", p)
