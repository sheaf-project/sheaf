import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sheaf.config import settings

# Single shared engine + pool. The request path and every background loop
# (job runner, dispatcher, import runner, export builder) draw from this one
# pool; gauges/leader/import_runner import `engine` directly for pool stats
# and advisory-lock connections, so it stays the canonical handle.
#
# statement_timeout is deliberately NOT set on the engine/connection here.
# A connection-level cap would apply to every session drawn from the pool,
# including the long-running background jobs (export builds, retention
# sweeps, analytics) that legitimately outlast any request. Instead the
# SHORT request cap is applied per-transaction inside get_db, and jobs stay
# uncapped by default (opting into db_job_statement_timeout_ms via
# job_session()). See _set_local_statement_timeout below.
engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_timeout=settings.db_pool_timeout,
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


async def _set_local_statement_timeout(session: AsyncSession, timeout_ms: int) -> None:
    """Apply a transaction-local Postgres statement_timeout to `session`.

    A value <= 0 is a no-op (unlimited). SET LOCAL is scoped to the current
    transaction, so the cap neither leaks to the next caller that checks out
    this pooled connection nor bleeds past a commit. Postgres SET does not
    accept bind parameters, so the value is formatted in directly - it comes
    from a pydantic int setting, never user input, and is re-cast to int
    here, so there is no injection surface.
    """
    if timeout_ms <= 0:
        return
    await session.execute(text(f"SET LOCAL statement_timeout = {int(timeout_ms)}"))


async def get_db() -> AsyncGenerator[AsyncSession]:
    async with async_session_factory() as session:
        # Bound the request path so a pathological O(history) query can't pin
        # a pooled connection indefinitely. Applied per-transaction, so it
        # never touches the background jobs, which use async_session_factory()
        # / job_session() directly and legitimately run longer than a request.
        await _set_local_statement_timeout(session, settings.db_statement_timeout_ms)
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def job_session() -> AsyncGenerator[AsyncSession]:
    """Session for background jobs that want an explicit statement_timeout
    ceiling (db_job_statement_timeout_ms, default 0 = unlimited).

    Same engine/pool as everything else; the only difference from
    async_session_factory() is the optional per-transaction cap. Jobs today
    use async_session_factory() directly and are therefore uncapped - adopt
    this where a job issues unbounded-size queries and you want a safety
    ceiling that is still far above the short request timeout. Unlike get_db
    this does NOT auto-commit; the job manages its own transactions.

    Note: SET LOCAL is per-transaction, so for a job that commits between
    units of work the cap re-applies only to the first transaction after
    entry. Jobs here follow an execute-then-commit pattern (not
    `async with db.begin()`), so the pre-emptive SET joins the same
    transaction as the job's first query. Left as an opt-in because the
    default ceiling is unlimited.
    """
    async with async_session_factory() as session:
        await _set_local_statement_timeout(
            session, settings.db_job_statement_timeout_ms
        )
        yield session
