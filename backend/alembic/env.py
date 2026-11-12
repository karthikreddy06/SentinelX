import os
import sys
from logging.config import fileConfig
from pathlib import Path

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parents[1]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import settings
from app.database.connection import Base
import app.models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url() -> str:
    return os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or settings.DATABASE_URL


def ensure_uuidv7_exists(connection) -> None:
    """Ensure uuidv7() function exists on PostgreSQL < 18."""
    check = connection.exec_driver_sql(
        "SELECT 1 FROM pg_proc WHERE proname = 'uuidv7'"
    ).scalar()
    if not check:
        connection.exec_driver_sql("""
            CREATE OR REPLACE FUNCTION uuidv7() RETURNS uuid AS $$
            SELECT encode(
              set_byte(
                set_byte(
                  overlay(uuid_send(gen_random_uuid()) placing substring(int8send(floor(extract(epoch from clock_timestamp()) * 1000)::bigint) from 3) from 1 for 6),
                  6, (get_byte(uuid_send(gen_random_uuid()), 6) & 15) | 112
                ),
                8, (get_byte(uuid_send(gen_random_uuid()), 8) & 63) | 128
              ),
              'hex')::uuid;
            $$ LANGUAGE sql;
        """)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        ensure_uuidv7_exists(connection)
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
