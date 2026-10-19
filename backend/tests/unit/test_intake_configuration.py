"""Review regression checks for deployment settings and the local demo boundary."""
import pytest
from app.config import Settings


def test_secret_comes_from_environment_and_is_redacted(monkeypatch):
    value = "test-only-long-signing-key-not-a-real-secret"
    monkeypatch.setenv("BAKEFLOW_APP_SECRET", value)
    settings = Settings(_env_file=None, environment="production")
    assert settings.summary_signing_secret() == value.encode()
    assert value not in repr(settings)


@pytest.mark.parametrize("environment", ["production", "demo", "test"])
def test_no_random_secret_outside_development(environment):
    with pytest.raises(ValueError, match="required outside development"):
        Settings(_env_file=None, environment=environment, app_secret="").summary_signing_secret()


def test_development_fallback_is_random_and_warns(caplog):
    settings = Settings(_env_file=None, app_secret="")
    assert settings.summary_signing_secret() != settings.summary_signing_secret()
    assert "expire on restart" in caplog.text


def test_short_configured_key_is_rejected_without_revealing_it():
    with pytest.raises(ValueError) as error:
        Settings(_env_file=None, app_secret="short-private-value").summary_signing_secret()
    assert "short-private-value" not in str(error.value)
