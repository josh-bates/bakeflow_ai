"""Database integration: declarative base, engine and sessions (BAKE-6).

BAKE-7 builds the shared record store and the guarded unit of work on top of
this package.
"""

from app.db.base import Base
from app.db.session import get_engine, get_sessionmaker

__all__ = ["Base", "get_engine", "get_sessionmaker"]
