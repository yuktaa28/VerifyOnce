"""SQLAlchemy engine, session factory and table creation."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine, event, inspect
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def _enable_sqlite_foreign_keys(dbapi_connection, _record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def make_engine(url: str) -> Engine:
    is_sqlite = url.startswith("sqlite")
    engine = create_engine(url, connect_args={"check_same_thread": False} if is_sqlite else {})
    if is_sqlite:
        event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def init_db(engine: Engine) -> None:
    """Create tables (and SQLite immutability triggers) if they do not exist yet."""
    database = engine.url.database
    if engine.url.get_backend_name() == "sqlite" and database and database != ":memory:":
        Path(database).parent.mkdir(parents=True, exist_ok=True)
    from backend import models  # noqa: F401  (registers the tables on Base.metadata)

    Base.metadata.create_all(engine)


def schema_problems(engine: Engine) -> list[str]:
    """Tables or columns the code expects but the database lacks (an older database).

    There are no migrations: an out-of-date demo database is rebuilt with
    scripts/demo_reset.py.
    """
    from backend import models  # noqa: F401

    inspector = inspect(engine)
    problems = []
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            problems.append(f"missing table {table.name}")
            continue
        present = {c["name"] for c in inspector.get_columns(table.name)}
        missing = [c.name for c in table.columns if c.name not in present]
        if missing:
            problems.append(f"{table.name} lacks {', '.join(missing)}")
    return problems
