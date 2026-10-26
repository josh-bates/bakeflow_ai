"""Alembic migration environment (BAKE-6).

The database URL comes from ``BAKEFLOW_DATABASE_URL`` via ``app.config``,
never from ``alembic.ini``; tests pass their own URL in
``config.attributes["database_url"]``. Import ORM model modules below so that
``alembic revision --autogenerate`` can see their tables.
"""

from logging.config import fileConfig

from sqlalchemy import create_engine, pool

from alembic import context
from app.config import get_settings
from app.db.base import Base
import app.auth.models  # noqa: F401

# Model modules register their tables on Base.metadata when imported.
import app.shared.records  # noqa: E402,F401  (BAKE-7 core records)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    url = config.attributes.get("database_url") or get_settings().database_url
    if not url:
        raise RuntimeError(
            "BAKEFLOW_DATABASE_URL is not set; see .env.example in the repository root."
        )
    return url


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of connecting (``alembic upgrade --sql``)."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Connect to the database and apply migrations."""
    connectable = create_engine(_database_url(), poolclass=pool.NullPool)

    with connectable.connect() as connection:
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
