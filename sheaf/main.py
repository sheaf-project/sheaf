import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from sheaf import __version__
from sheaf.api.v1.router import v1_router
from sheaf.config import _validate_settings, settings
from sheaf.middleware.body_size import BodyTooLargeError, MaxBodySizeMiddleware
from sheaf.middleware.origin_check import OriginCheckMiddleware
from sheaf.middleware.rate_limit import RateLimitMiddleware
from sheaf.observability import (
    MetricsMiddleware,
    init_registry,
    setup_metrics_endpoint,
)
from sheaf.observability.metrics import build_info, prewarm_metrics

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("sheaf")


async def _fast_gauges_loop() -> None:
    """Refresh fast-moving metrics gauges (redis_up, db_pool, outbox_depth)
    on a short cadence so up/down detection isn't bounded by the
    job-runner's coarse wake interval. The slow sweep stays on the job
    runner."""
    from sheaf.observability.gauges import refresh_fast_gauges

    interval = max(settings.metrics_fast_gauge_refresh_seconds, 1)
    while True:
        try:
            await refresh_fast_gauges()
        except Exception:
            logger.exception("Fast-gauges refresh raised; continuing")
        await asyncio.sleep(interval)


async def _promote_admin_emails() -> None:
    """Promote configured admin emails to is_admin=True on startup."""
    emails = settings.admin_email_list
    if not emails:
        return
    from sqlalchemy import select

    from sheaf.crypto import blind_index
    from sheaf.database import async_session_factory
    from sheaf.models.user import User

    async with async_session_factory() as db:
        for email in emails:
            email_hash = blind_index(email)
            result = await db.execute(select(User).where(User.email_hash == email_hash))
            user = result.scalar_one_or_none()
            if user:
                changed = False
                if not user.is_admin:
                    user.is_admin = True
                    changed = True
                if not user.email_verified:
                    user.email_verified = True
                    changed = True
                from sheaf.models.user import AccountStatus

                if user.account_status != AccountStatus.ACTIVE:
                    user.account_status = AccountStatus.ACTIVE
                    changed = True
                if changed:
                    from sheaf.redact import redact_email

                    logger.info(
                        "Promoted %s to admin (verified, active)",
                        redact_email(email),
                    )
        await db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _validate_settings()
    # Eagerly initialise encryption key so we get the warning at startup
    settings.get_encryption_key()
    logger.info("Sheaf %s starting in %s mode", __version__, settings.sheaf_mode.value)

    # Metrics: init registry before any bump call site has a chance to fire.
    init_registry()
    build_info.labels(
        version=__version__,
        sheaf_mode=settings.sheaf_mode.value,
        git_commit=settings.sheaf_git_commit or "unknown",
    ).set(1)
    prewarm_metrics()
    setup_metrics_endpoint(app, settings)

    await _promote_admin_emails()

    # Dev-only startup tasks (sheaf_dev not installed in production)
    try:
        from sheaf.database import async_session_factory
        from sheaf_dev.jobs import ensure_dev_announcement

        async with async_session_factory() as db:
            await ensure_dev_announcement(db)
    except ImportError:
        pass

    from sheaf.services.import_runner import import_runner_loop
    from sheaf.services.jobs import job_runner_loop
    from sheaf.services.leader import leader_loop
    from sheaf.services.notifications.dispatcher import dispatcher_loop

    # The background loops: the periodic job registry, the notification
    # dispatcher, and the import runner. Imports get their own fast loop,
    # not the slow jobs.py registry — the registry wakes far too slowly
    # for an import a user is actively waiting on. The test stack
    # disables the import loop so import tests can drive the runner
    # manually without a live loop racing them.
    loop_factories = [
        ("jobs", job_runner_loop),
        ("dispatcher", dispatcher_loop),
    ]
    if settings.import_runner_enabled:
        loop_factories.append(("imports", import_runner_loop))

    # With leader election (default), every replica runs this task but
    # only the advisory-lock holder actually starts the loops; the rest
    # stand by and take over within seconds if the leader dies. With it
    # disabled, the loops run unconditionally in this process (the old
    # single-instance behaviour).
    if settings.leader_election_enabled:
        background_tasks = [asyncio.create_task(leader_loop(loop_factories))]
    else:
        background_tasks = [
            asyncio.create_task(factory(), name=name)
            for name, factory in loop_factories
        ]

    # Fast-gauges loop: redis_up / db_pool / outbox_depth refreshed every
    # ~10s so up/down detection isn't bounded by the job-runner cadence.
    # The full DB+Redis sweep stays on the jobs.py registry (slow path).
    # Deliberately NOT leader-gated: it only reads, and every replica
    # should report its own pool/redis view.
    if settings.metrics_enabled:
        background_tasks.append(asyncio.create_task(_fast_gauges_loop()))

    yield

    for task in background_tasks:
        task.cancel()
    for task in background_tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await task
    logger.info("Sheaf shutting down")


app = FastAPI(
    title="Sheaf",
    description="Open-source plural system tracking",
    version=__version__,
    lifespan=lifespan,
    docs_url="/v1/docs",
    redoc_url="/v1/redoc",
    openapi_url="/v1/openapi.json",
)


@app.exception_handler(BodyTooLargeError)
async def body_too_large_handler(
    request: Request, exc: BodyTooLargeError
) -> JSONResponse:
    mb = exc.max_bytes // (1024 * 1024)
    return JSONResponse(
        status_code=413,
        content={"detail": f"Request body too large. Max: {mb}MB"},
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    # Log the reason for every deliberate client/server error so a bare
    # status code in the access log can be traced to *why* without
    # reproducing. `exc.detail` is the same user-facing string already
    # returned in the body; no request body or secrets are logged. 5xx is
    # surfaced at WARNING, 4xx at INFO (filter-friendly on a busy public
    # instance where 401/404 probes are routine). The response is byte-for-
    # byte the FastAPI default, headers included.
    if exc.status_code >= 500:
        logger.warning(
            "%s %s -> %d: %s",
            request.method, request.url.path, exc.status_code, exc.detail,
        )
    elif exc.status_code >= 400:
        logger.info(
            "%s %s -> %d: %s",
            request.method, request.url.path, exc.status_code, exc.detail,
        )
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    # 422s are request-shape mismatches. Log the field locations, messages,
    # and error types - deliberately NOT the submitted `input` values, which
    # can carry user data - so a malformed client request is diagnosable. The
    # response body keeps the FastAPI default shape (which does include the
    # inputs, same as before this handler existed).
    safe = [
        {"loc": e.get("loc"), "msg": e.get("msg"), "type": e.get("type")}
        for e in exc.errors()
    ]
    logger.info(
        "%s %s -> 422 validation: %s", request.method, request.url.path, safe
    )
    return JSONResponse(
        status_code=422, content={"detail": jsonable_encoder(exc.errors())}
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(
        "Unhandled exception on %s %s", request.method, request.url.path
    )
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        # HSTS only when the request arrived over HTTPS (via reverse proxy or
        # direct TLS). Emitting HSTS on plain-HTTP bootstrap would hard-fail
        # local dev and first-run setups.
        forwarded_proto = request.headers.get("x-forwarded-proto", "").lower()
        if forwarded_proto == "https" or request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = (
            "accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
            "magnetometer=(), microphone=(), payment=(), usb=()"
        )
        if settings.allow_external_images:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; img-src 'self' data: blob: https:; "
                "style-src 'self' 'unsafe-inline'; frame-ancestors 'none'"
            )
        else:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; img-src 'self' data: blob:; "
                "style-src 'self' 'unsafe-inline'; frame-ancestors 'none'"
            )
        return response


# Innermost (added first = closest to the routes): CSRF Origin check on
# cookie-authenticated mutations. Runs after the rate limiter so CSRF
# probes still consume rate-limit budget.
app.add_middleware(OriginCheckMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(
    MaxBodySizeMiddleware,
    max_bytes=settings.max_request_body_size_mb * 1024 * 1024,
)
# Outermost middleware so the duration histogram captures total
# user-facing latency including body-size and rate-limit checks.
# Cheap no-op when metrics_enabled=false.
if settings.metrics_enabled:
    app.add_middleware(MetricsMiddleware)

app.include_router(v1_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


# RFC 9116 security.txt. The contact and policy point at the upstream project
# because that's where Sheaf-software vulnerabilities should be reported;
# operator-specific issues (instance config, infra) are outside the scope of
# this file and would use the operator's own channels.
def _security_txt_body() -> str:
    expires = (datetime.now(UTC) + timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        # Two channels, mirroring SECURITY.md: email first, then the
        # GitHub private security advisory form. RFC 9116 reads multiple
        # Contact fields in listed preference order.
        "Contact: mailto:security@sheaf.sh",
        "Contact: https://github.com/sheaf-project/sheaf/security/advisories/new",
        f"Expires: {expires}",
        # The team key is published in-repo rather than on a public
        # keyserver — keyserver entries are append-only and can't be
        # rotated or removed. The raw URL serves the ASCII-armored
        # block; SECURITY.md carries the same fingerprint for the
        # human-readable path.
        # Fingerprint: 90AC C2BB 6C88 6DD8 EBD0  11B9 BBB8 ABBC 92D6 A17C
        "Encryption: https://raw.githubusercontent.com/sheaf-project/sheaf/main/SECURITY-PGP-KEY.asc",
        "Preferred-Languages: en",
        "Policy: https://github.com/sheaf-project/sheaf/blob/main/SECURITY.md",
    ]
    if settings.sheaf_base_url:
        canonical = settings.sheaf_base_url.rstrip("/") + "/.well-known/security.txt"
        lines.append(f"Canonical: {canonical}")
    return "\n".join(lines) + "\n"


@app.get("/.well-known/security.txt", include_in_schema=False)
@app.get("/security.txt", include_in_schema=False)
async def security_txt() -> PlainTextResponse:
    return PlainTextResponse(_security_txt_body(), media_type="text/plain; charset=utf-8")
