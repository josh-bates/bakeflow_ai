"""Loopback-only Mock demo using the same real sign-in as the normal app.

Run `python -m app.sales.demo` after migrations and seeding PostgreSQL.
Seeded accounts must sign in; there is no identity shortcut.
"""
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.main import create_app


def create_demo_app():
    settings = get_settings()
    if settings.environment != "development" or settings.llm_mode != "mock" or not settings.database_url:
        raise RuntimeError("Demo requires development, mock mode and a seeded database.")
    app = create_app(settings)
    @app.middleware("http")
    async def local_only(request: Request, call_next):
        host = request.url.hostname
        origin = request.headers.get("origin")
        if (host not in {"127.0.0.1", "localhost", "::1"}
                or request.client is None
                or request.client.host not in {"127.0.0.1", "::1"}
                or (origin and urlsplit(origin).netloc != request.url.netloc)):
            return JSONResponse({"detail": "Local demo only."}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    return app


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(create_demo_app(), host="127.0.0.1", port=4176)
