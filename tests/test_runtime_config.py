from __future__ import annotations

from ap_automation.config import AppEnv, LOCAL_DATABASE_URL, load_runtime_config


def test_local_runtime_defaults_to_passwordless_local_database(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AP_DASHBOARD_DSN", raising=False)
    monkeypatch.setenv("APP_ENV", "LOCAL")

    config = load_runtime_config()

    assert config.app_env == AppEnv.LOCAL
    assert config.processor_dsn() == LOCAL_DATABASE_URL
    assert "password=" not in config.dashboard_dsn().lower()


def test_azure_runtime_requires_explicit_database_url(monkeypatch):
    monkeypatch.setenv("APP_ENV", "AZURE")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    config = load_runtime_config()

    try:
        config.processor_dsn()
    except RuntimeError as exc:
        assert "DATABASE_URL" in str(exc)
    else:
        raise AssertionError("Expected AZURE runtime to require explicit DATABASE_URL.")
