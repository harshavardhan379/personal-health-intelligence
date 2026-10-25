"""Database connection and session management (YGH-21, YGH-94).

Environment-based configuration: reads DATABASE_URL, falls back to a local default
that matches docker-compose.yml's postgres service.

Timeouts (the "database" policy in packages/observability/resilience.py): 5 s to connect, 30 s per
statement, 10 s waiting for a pooled connection, so a stuck database fails a request instead of
hanging it. Connections are recycled hourly so a firewall that silently drops idle ones cannot
hand out a dead one.
"""

import os
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from packages.observability.resilience import (
    DATABASE_CONNECT_TIMEOUT_S,
    DATABASE_POOL_TIMEOUT_S,
    DATABASE_STATEMENT_TIMEOUT_MS,
)

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg://phi:phi_dev_password@localhost:5433/phi",
)


def engine_options(url: str) -> dict:
    """create_engine options for `url`. The timeouts are Postgres settings; any other database
    (the in-memory SQLite the tests use) gets none of them."""
    options: dict = {"pool_pre_ping": True}  # avoids handing out dead connections after a restart
    if make_url(url).get_backend_name() == "postgresql":
        options.update(
            pool_timeout=DATABASE_POOL_TIMEOUT_S,
            pool_recycle=3600,
            connect_args={
                "connect_timeout": DATABASE_CONNECT_TIMEOUT_S,
                "options": f"-c statement_timeout={DATABASE_STATEMENT_TIMEOUT_MS}",
            },
        )
    return options


engine = create_engine(DATABASE_URL, **engine_options(DATABASE_URL))
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: one session per request, always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
