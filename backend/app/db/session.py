"""Engine and session factory (BAKE-6).

Engines are created lazily and cached per URL, so unit tests that never touch
the database do not need PostgreSQL.
"""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


@lru_cache
def get_engine(database_url: str) -> Engine:
    # pre_ping replaces connections dropped while the database restarted.
    return create_engine(database_url, pool_pre_ping=True)


@lru_cache
def get_sessionmaker(database_url: str) -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(database_url), expire_on_commit=False)
