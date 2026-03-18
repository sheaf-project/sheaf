import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from sheaf.api.v1.router import v1_router
from sheaf.config import _validate_settings, settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("sheaf")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _validate_settings()
    # Eagerly initialise encryption key so we get the warning at startup
    settings.get_encryption_key()
    logger.info("Sheaf %s starting in %s mode", "0.1.0", settings.sheaf_mode.value)
    yield
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
