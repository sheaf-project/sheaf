import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session as _SyncSession

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
# SHORT request cap is applied per-transaction to sessions that opt in via
# session.info (get_db, request_session), and jobs stay uncapped by default
# (opting into db_job_statement_timeout_ms via job_session()). See the
# after_begin listener below.
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


# Sessions opt into a Postgres statement_timeout by setting this key in
# session.info; the after_begin listener below re-applies it at the start of
# EVERY transaction the session opens. The previous design issued a single
# SET LOCAL when the session was created, which silently evaporated at the
# first mid-request commit (SET LOCAL is transaction-scoped), leaving the
# rest of the request uncapped. The 2026-08-13 incident's variant of this
# class - a request-tier session created outside get_db with no cap at all -
# let one coalesce query spill 19 GB of query temp; see request_session().
_TIMEOUT_INFO_KEY = "statement_timeout_ms"


@event.listens_for(_SyncSession, "after_begin")
def _apply_statement_timeout(session, transaction, connection) -> None:
    """Apply the session's opted-in statement_timeout to each new transaction.

    Registered on the Session class, so it fires for every session drawn from
    the shared pool - but it is a no-op unless the session set
    `info[_TIMEOUT_INFO_KEY]`, so background jobs that use
    async_session_factory() directly stay uncapped as designed. SET LOCAL is
    transaction-scoped, so nothing leaks to the next checkout of the pooled
    connection. Postgres SET takes no bind parameters; the value is a pydantic
    int setting (never user input) re-cast to int here, so there is no
    injection surface.
    """
    timeout_ms = session.info.get(_TIMEOUT_INFO_KEY)
    if not timeout_ms or timeout_ms <= 0:
        return
    connection.exec_driver_sql(f"SET LOCAL statement_timeout = {int(timeout_ms)}")


async def get_db() -> AsyncGenerator[AsyncSession]:
    async with async_session_factory() as session:
        # Bound the request path so a pathological O(history) query can't pin
        # a pooled connection indefinitely. The info key makes the after_begin
        # listener re-apply the cap on every transaction, so it survives
        # mid-request commits. Background jobs use async_session_factory() /
        # job_session() directly and are not affected.
        session.info[_TIMEOUT_INFO_KEY] = settings.db_statement_timeout_ms
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def request_session() -> AsyncGenerator[AsyncSession]:
    """Session for request-tier work that cannot use Depends(get_db).

    Same engine/pool and the same request statement_timeout cap as get_db,
    without the DI lifecycle: no auto-commit, caller manages transactions.
    For paths that serve a user request but manage their own session - e.g.
    the SSE front stream, which builds its snapshot in a short-lived session
    so no DB connection is held while the stream is open.

    Every session that runs queries on behalf of a user request MUST carry
    the request timeout cap. The 2026-08-13 incident: the stream snapshot
    used a bare async_session_factory() session, so the coalesce query it
    ran had no statement_timeout and kept spilling query temp for ~13
    minutes after the request-path twin of the same query had been killed
    at 30 s - filling the disk and taking Postgres down for everyone. Use
    this instead of async_session_factory() for anything request-shaped.
    """
    async with async_session_factory() as session:
        session.info[_TIMEOUT_INFO_KEY] = settings.db_statement_timeout_ms
        yield session


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

    The after_begin listener re-applies the cap on every transaction, so a
    job that commits between units of work keeps its ceiling for each one
    (this previously only covered the first transaction after entry). Left
    as an opt-in because the default ceiling is unlimited.
    """
    async with async_session_factory() as session:
        session.info[_TIMEOUT_INFO_KEY] = settings.db_job_statement_timeout_ms
        yield session
