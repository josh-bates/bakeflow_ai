"""FastAPI entry point (BAKE-6).

One process serves the JSON API under ``/api`` and, once a React build exists,
the single-page app (architecture §2.1, §7.1). Run locally from ``backend/``
with ``uvicorn app.main:app --reload``; Docker Compose runs the same app.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings, get_settings
from app.db.session import get_engine


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="BakeFlow AI", version="0.1.0")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        """Liveness: the process is up. Does not touch the database."""
        return {"status": "ok", "environment": settings.environment}

    @app.get("/api/health/ready")
    def ready() -> dict[str, str]:
        """Readiness: the configured database answers ``SELECT 1``."""
        if not settings.database_url:
            raise HTTPException(status_code=503, detail="database not configured")
        try:
            with get_engine(settings.database_url).connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError:
            raise HTTPException(status_code=503, detail="database unavailable") from None
        return {"status": "ok", "database": "ok"}

    # Registered last so that every /api route above takes precedence.
    _serve_frontend(app, settings.frontend_dist)
    return app


def _serve_frontend(app: FastAPI, dist: Path) -> None:
    """Serve the built SPA; unknown non-API paths fall back to index.html."""
    index = dist / "index.html"
    if not index.is_file():
        return
    root = dist.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path.split("/", 1)[0] == "api":
            raise HTTPException(status_code=404)
        candidate = (root / path).resolve()
        if candidate.is_file() and candidate.is_relative_to(root):
            return FileResponse(candidate)
        return FileResponse(index)


app = create_app()
