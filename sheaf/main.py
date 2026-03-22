import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from sheaf.api.v1.router import v1_router
from sheaf.config import SheafMode, _validate_settings, settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("sheaf")


async def _retention_loop() -> None:
    """Periodically prune free-tier front history in aaS mode."""
    from sheaf.database import async_session_factory
    from sheaf.services.front_retention import prune_free_tier_fronts

    interval = settings.retention_check_interval_hours * 3600
    hours = settings.retention_check_interval_hours
    logger.info("Retention task started — checking every %dh", hours)

    while True:
        await asyncio.sleep(interval)
        try:
            async with async_session_factory() as session:
                count = await prune_free_tier_fronts(session)
                await session.commit()
                if count > 0:
                    logger.info("Retention task pruned %d fronts", count)
        except Exception:
            logger.exception("Retention task failed")


async def _promote_admin_emails() -> None:
    """Promote configured admin emails to is_admin=True on startup."""
    if not settings.admin_emails:
        return
    from sqlalchemy import select

    from sheaf.crypto import blind_index
    from sheaf.database import async_session_factory
    from sheaf.models.user import User

    async with async_session_factory() as db:
        for email in settings.admin_emails:
            email_hash = blind_index(email)
            result = await db.execute(select(User).where(User.email_hash == email_hash))
            user = result.scalar_one_or_none()
            if user and not user.is_admin:
                user.is_admin = True
                logger.info("Promoted %s to admin", email)
        await db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _validate_settings()
    # Eagerly initialise encryption key so we get the warning at startup
    settings.get_encryption_key()
    logger.info("Sheaf %s starting in %s mode", "0.1.0", settings.sheaf_mode.value)

    await _promote_admin_emails()

    retention_task = None
    if settings.sheaf_mode == SheafMode.SAAS:
        retention_task = asyncio.create_task(_retention_loop())

    yield

    if retention_task is not None:
        retention_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await retention_task
    logger.info("Sheaf shutting down")


app = FastAPI(
    title="Sheaf",
    description="Open-source plural system tracking",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/v1/docs",
    redoc_url="/v1/redoc",
    openapi_url="/v1/openapi.json",
)

app.include_router(v1_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
