from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.app import create_app
from backend.config import REPO_ROOT, Settings
from backend.db import init_db, make_engine, make_session_factory
from backend.schemes import Scheme, load_schemes
from backend.seed import seed_database


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}")


@pytest.fixture
def schemes() -> dict[str, Scheme]:
    return load_schemes(REPO_ROOT / "schemes")


@pytest.fixture
def client(settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def seeded_client(client) -> TestClient:
    with client.app.state.session_factory() as session:
        seed_database(session)
        session.commit()
    return client


@pytest.fixture
def db_session(client) -> Iterator[Session]:
    """A session on the same database the API client uses."""
    with client.app.state.session_factory() as session:
        yield session


@pytest.fixture
def bare_session(settings) -> Iterator[Session]:
    """A session on a fresh database, without the API."""
    engine = make_engine(settings.database_url)
    init_db(engine)
    with make_session_factory(engine)() as session:
        yield session
    engine.dispose()
