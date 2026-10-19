"""Application settings read from the environment (BAKE-6).

Every variable carries the ``BAKEFLOW_`` prefix, matching the LLM gateway's
``BAKEFLOW_LLM_*`` variables (BAKE-9). Values come from the shell, from
Docker Compose, or from a local ``.env`` in the repository root; nothing
secret is committed (architecture §7.1). See ``.env.example``.
"""

from __future__ import annotations

import logging
import secrets

from functools import lru_cache
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py -> repository root. In the Docker image this is "/".
REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BAKEFLOW_",
        env_file=REPO_ROOT / ".env",
        extra="ignore",
    )

    environment: str = "development"
    app_secret: SecretStr | None = None
    llm_mode: Literal["mock", "live"] = "mock"
    # SQLAlchemy URL, e.g. postgresql+psycopg://user:password@db:5432/bakeflow.
    # None means "no database": the app still starts, readiness reports 503.
    database_url: str | None = None
    # Persist UTC, render in the bakery's zone (architecture §3, [R10]).
    timezone: str = "Australia/Sydney"
    # Built React app served by FastAPI when present (BAKE-10 produces it).
    frontend_dist: Path = REPO_ROOT / "frontend" / "dist"
    # Demo account passwords used by `python -m app.seed` (BAKE-7).
    seed_owner_password: SecretStr | None = None
    seed_customer_password: SecretStr | None = None

    def summary_signing_secret(self) -> bytes:
        value = self.app_secret.get_secret_value() if self.app_secret else ""
        if value:
            if len(value.encode("utf-8")) < 32:
                raise ValueError("BAKEFLOW_APP_SECRET must contain at least 32 bytes.")
            return value.encode("utf-8")
        if self.environment != "development":
            raise ValueError("BAKEFLOW_APP_SECRET is required outside development.")
        logging.getLogger(__name__).warning(
            "BAKEFLOW_APP_SECRET is unset: development summaries expire on restart "
            "and cannot be shared between workers."
        )
        return secrets.token_bytes(32)

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown IANA time zone: {value!r}") from exc
        return value


@lru_cache
def get_settings() -> Settings:
    """Settings for this process, read once."""
    return Settings()
