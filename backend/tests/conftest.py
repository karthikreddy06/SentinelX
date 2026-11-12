import os
from pathlib import Path
import sys
from urllib.parse import urlparse, urlunparse
import uuid

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
from app.core.rate_limiter import rate_limiter
from app.core.security import create_access_token, hash_password
from app.database import connection as db_connection
from app.database.connection import get_db
from app.fraud.cache import clear_all_fraud_cache
from app.main import app as fastapi_app
from app.models.user import User, UserRole


def get_test_db_url() -> str:
    parsed = urlparse(settings.DATABASE_URL)
    return urlunparse(parsed._replace(path="/sentinelx_test"))


test_db_url = get_test_db_url()
os.environ["TEST_DATABASE_URL"] = test_db_url

test_engine = create_engine(test_db_url, pool_pre_ping=True)
TestingSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=test_engine,
)

# Ensure background tasks and services use the isolated test database
db_connection.SessionLocal = TestingSessionLocal


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
    """Truncate tables and reset in-memory limiters before and after test runs to ensure isolation."""
    rate_limiter.reset()
    clear_all_fraud_cache()
    with test_engine.connect() as conn:
        conn.execute(
            text(
                "TRUNCATE TABLE account_network_risk, outbox_events, investigation_notes, investigation_evidence, investigation_alerts, investigations, alert_events, alerts, fraud_scores, audit_logs, refresh_tokens, users, transactions RESTART IDENTITY CASCADE"
            )
        )
        conn.commit()
    yield
    rate_limiter.reset()
    clear_all_fraud_cache()
    with test_engine.connect() as conn:
        conn.execute(
            text(
                "TRUNCATE TABLE account_network_risk, outbox_events, investigation_notes, investigation_evidence, investigation_alerts, investigations, alert_events, alerts, fraud_scores, audit_logs, refresh_tokens, users, transactions RESTART IDENTITY CASCADE"
            )
        )
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

    fastapi_app.dependency_overrides[get_db] = override_get_db
    with TestClient(fastapi_app) as test_client:
        yield test_client
    fastapi_app.dependency_overrides.clear()


@pytest.fixture
def create_test_user(db_session):
    """Factory fixture to create test users with specified roles."""

    def _create(
        email: str = "user@test.local",
        password: str = "SecurePassword123!",
        role: UserRole = UserRole.INVESTIGATOR,
        full_name: str = "Test User",
        is_active: bool = True,
    ) -> User:
        user = User(
            email=email.lower(),
            hashed_password=hash_password(password),
            full_name=full_name,
            role=role,
            is_active=is_active,
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
        return user

    return _create


@pytest.fixture
def admin_user(create_test_user):
    return create_test_user(
        email="admin@test.local",
        password="AdminPassword123!",
        role=UserRole.ADMIN,
        full_name="Admin User",
    )


@pytest.fixture
def investigator_user(create_test_user):
    return create_test_user(
        email="investigator@test.local",
        password="InvestigatorPass123!",
        role=UserRole.INVESTIGATOR,
        full_name="Investigator User",
    )


@pytest.fixture
def analyst_user(create_test_user):
    return create_test_user(
        email="analyst@test.local",
        password="AnalystPassword123!",
        role=UserRole.ANALYST,
        full_name="Analyst User",
    )


@pytest.fixture
def admin_headers(admin_user):
    token = create_access_token(
        user_id=str(admin_user.id),
        email=admin_user.email,
        role=admin_user.role.value,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def investigator_headers(investigator_user):
    token = create_access_token(
        user_id=str(investigator_user.id),
        email=investigator_user.email,
        role=investigator_user.role.value,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def analyst_headers(analyst_user):
    token = create_access_token(
        user_id=str(analyst_user.id),
        email=analyst_user.email,
        role=analyst_user.role.value,
    )
    return {"Authorization": f"Bearer {token}"}
