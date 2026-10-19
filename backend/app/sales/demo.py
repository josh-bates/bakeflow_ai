"""Explicit loopback-only UC-01 demo using seeded data; not production login.

Run `python -m app.sales.demo` after migrations and seeding PostgreSQL.
Normal app.main does not enable this identity shortcut.
"""
import secrets
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.config import get_settings
from app.db.session import get_sessionmaker
from app.main import create_app
from app.sales.intake import CustomerIdentity
from app.sales.routes import current_customer
from app.shared.records import Customer


def create_demo_app():
    settings = get_settings()
    if settings.environment != "development" or settings.llm_mode != "mock" or not settings.database_url:
        raise RuntimeError("Demo requires development, mock mode and a seeded database.")
    app = create_app(settings)
    sessions = get_sessionmaker(settings.database_url)
    with sessions() as db:
        customer = db.scalars(select(Customer)).one()
        identity = CustomerIdentity(customer.user_id, customer.customer_id, "Demo customer")
    csrf = secrets.token_urlsafe(32)

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        host = request.url.hostname
        origin = request.headers.get("origin")
        if (host not in {"127.0.0.1", "localhost", "::1"}
                or request.client is None
                or request.client.host not in {"127.0.0.1", "::1"}
                or (origin and urlsplit(origin).netloc != request.url.netloc)):
            return JSONResponse({"detail": "Local demo only."}, status_code=403)
        request.state.csrf_token = csrf
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    app.dependency_overrides[current_customer] = lambda: identity
    return app


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(create_demo_app(), host="127.0.0.1", port=4176)
