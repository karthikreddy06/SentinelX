import os
import sys
from pathlib import Path
from urllib.parse import urlparse, urlunparse

# Ensure backend directory is in sys.path
backend_dir = Path(__file__).resolve().parents[1]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.database.connection import get_db
from app.main import app


def get_test_db_url() -> str:
    parsed = urlparse(settings.DATABASE_URL)
    return urlunparse(parsed._replace(path="/sentinelx_test"))


# Programmatically derived test database engine
test_db_url = get_test_db_url()
os.environ["TEST_DATABASE_URL"] = test_db_url

test_engine = create_engine(test_db_url, pool_pre_ping=True)
TestingSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=test_engine,
)


@pytest.fixture(scope="session", autouse=True)
def apply_migrations():
    """Ensure database schema is up to date on test DB using Alembic migrations."""
    root_dir = Path(__file__).resolve().parents[2]
    alembic_ini_path = root_dir / "alembic.ini"
    cfg = Config(str(alembic_ini_path))
    command.upgrade(cfg, "head")
    yield


@pytest.fixture(autouse=True)
def clean_database():
    """Truncate tables between test runs to ensure complete isolation."""
    yield
    with test_engine.connect() as conn:
        conn.execute(text("TRUNCATE TABLE transactions RESTART IDENTITY CASCADE"))
        conn.commit()


@pytest.fixture
def db_session():
    """Provide a direct database session for testing DB-level operations."""
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session):
    """FastAPI TestClient with overridden get_db dependency pointing to sentinelx_test."""
    def override_get_db():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
