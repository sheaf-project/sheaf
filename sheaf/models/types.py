"""Custom SQLAlchemy column types."""

from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.types import TypeDecorator


class InetStr(TypeDecorator):
    """Postgres ``INET`` that always reads back as a plain string.

    The asyncpg driver decodes ``inet``/``cidr`` columns into Python
    ``ipaddress`` objects, but the rest of the app (Pydantic response
    models, ``==`` comparisons against request strings, logging) expects
    a string. Normalise on the way out so a single integration point
    handles it instead of every reader remembering to ``str()``. Writes
    accept a string and let Postgres cast.
    """

    impl = INET
    cache_ok = True

    def process_result_value(self, value, dialect):
        return str(value) if value is not None else None
