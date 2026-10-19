"""Start a persistent local PostgreSQL demo without Docker (optional dependency).

From backend/: .venv/bin/python scripts/run_local_demo.py
Data stays in .local/postgres-demo; this launcher never resets it.
"""
from pathlib import Path
import os
import secrets
import sys

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


def main():
    import pgserver
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import select, func
    import uvicorn

    data = BACKEND.parent / ".local" / "postgres-demo"
    data.parent.mkdir(parents=True, exist_ok=True)
    server = pgserver.get_server(data)
    try:
        url = server.get_uri().replace("postgresql://", "postgresql+psycopg://", 1)
        os.environ["BAKEFLOW_DATABASE_URL"] = url
        os.environ["BAKEFLOW_ENVIRONMENT"] = "development"
        os.environ["BAKEFLOW_LLM_MODE"] = "mock"
        config = Config(str(BACKEND / "alembic.ini"))
        config.set_main_option("script_location", str(BACKEND / "alembic"))
        config.attributes["database_url"] = url
        command.upgrade(config, "head")

        from app.db.session import get_sessionmaker
        from app.shared.store import ValidatedRecordStore
        from app.shared.records import UserAccount
        from app.seed import seed
        from app.sales.demo import create_demo_app
        sessions = get_sessionmaker(url)
        with sessions() as db:
            empty = db.scalar(select(func.count()).select_from(UserAccount)) == 0
        if empty:
            seed(ValidatedRecordStore(sessions), owner_password=secrets.token_urlsafe(24),
                 customer_password=secrets.token_urlsafe(24))
        print("Local Mock demo: http://127.0.0.1:4176 (PostgreSQL data is preserved)", flush=True)
        uvicorn.run(create_demo_app(), host="127.0.0.1", port=4176)
    finally:
        server.cleanup()


if __name__ == "__main__":
    main()
