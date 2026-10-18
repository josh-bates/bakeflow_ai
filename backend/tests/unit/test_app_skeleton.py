"""BAKE-6: application skeleton — settings, health checks and SPA serving.

Run from ``backend/``: ``pytest tests/unit/test_app_skeleton.py``.
No PostgreSQL is needed; readiness is exercised with SQLite URLs.
"""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.main import create_app


def make_settings(**values) -> Settings:
    # _env_file=None keeps a developer's local .env out of the tests.
    return Settings(_env_file=None, **values)


def client_for(**values) -> TestClient:
    return TestClient(create_app(make_settings(**values)))


def test_settings_read_prefixed_environment_variables(monkeypatch):
    monkeypatch.setenv("BAKEFLOW_DATABASE_URL", "sqlite://")
    monkeypatch.setenv("BAKEFLOW_ENVIRONMENT", "demo")

    settings = make_settings()

    assert settings.database_url == "sqlite://"
    assert settings.environment == "demo"
    assert settings.timezone == "Australia/Sydney"


def test_settings_reject_unknown_timezone():
    with pytest.raises(ValidationError, match="unknown IANA time zone"):
        make_settings(timezone="Sydney/Bakery")


def test_health_does_not_need_a_database():
    response = client_for(database_url=None).get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "environment": "development"}


def test_ready_reports_reachable_database():
    response = client_for(database_url="sqlite://").get("/api/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_ready_is_503_without_database_url():
    response = client_for(database_url=None).get("/api/health/ready")

    assert response.status_code == 503
    assert response.json()["detail"] == "database not configured"


def test_ready_is_503_when_database_unreachable(tmp_path):
    missing = (tmp_path / "no-such-dir" / "bakeflow.db").as_posix()

    response = client_for(database_url=f"sqlite:///{missing}").get("/api/health/ready")

    assert response.status_code == 503
    assert response.json()["detail"] == "database unavailable"


@pytest.fixture
def spa_client(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<div id=root></div>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log('app')", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("outside the build", encoding="utf-8")
    return TestClient(create_app(make_settings(frontend_dist=dist)))


def test_spa_serves_index_and_built_assets(spa_client):
    assert spa_client.get("/").text == "<div id=root></div>"
    assert spa_client.get("/assets/app.js").text == "console.log('app')"


def test_spa_falls_back_to_index_for_client_routes(spa_client):
    response = spa_client.get("/owner/orders/42")

    assert response.status_code == 200
    assert response.text == "<div id=root></div>"


def test_spa_keeps_unknown_api_paths_as_json_404(spa_client):
    response = spa_client.get("/api/no-such-endpoint")

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/json"


def test_spa_does_not_serve_files_outside_the_build(spa_client):
    response = spa_client.get("/%2E%2E/secret.txt")

    assert "outside the build" not in response.text


def test_no_frontend_build_means_api_only(tmp_path):
    client = client_for(frontend_dist=tmp_path / "missing")

    assert client.get("/").status_code == 404
    assert client.get("/api/health").status_code == 200
