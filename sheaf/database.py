import time
from collections.abc import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sheaf.config import settings

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


def _sql_operation(statement: str) -> str:
    """Map a statement to a small label set. Anything unrecognised becomes
    "other", which keeps the histogram cardinality bounded even when an
    odd statement (PRAGMA, SET, ...) sneaks in."""
    leading = statement.lstrip()[:8].lower()
    if leading.startswith("select"):
        return "select"
    if leading.startswith("insert"):
        return "insert"
    if leading.startswith("update"):
        return "update"
    if leading.startswith("delete"):
        return "delete"
    if leading.startswith(("create", "alter", "drop")):
        return "ddl"
    return "other"


@event.listens_for(engine.sync_engine, "before_cursor_execute")
def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    context._sheaf_query_start = time.perf_counter()


@event.listens_for(engine.sync_engine, "after_cursor_execute")
def _after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    start = getattr(context, "_sheaf_query_start", None)
    if start is None:
        return
    # Local import to avoid cycle: observability.metrics imports config,
    # which is fine, but importing at module level when this module is
    # in the bootstrap path would pull metrics before init_registry runs.
    from sheaf.observability.metrics import db_query_duration_seconds
    db_query_duration_seconds.labels(operation=_sql_operation(statement)).observe(
        time.perf_counter() - start
    )


async def get_db() -> AsyncGenerator[AsyncSession]:
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
