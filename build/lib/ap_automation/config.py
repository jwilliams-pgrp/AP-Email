from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class AppEnv(str, Enum):
    LOCAL = "LOCAL"
    AZURE = "AZURE"


LOCAL_DATABASE_URL = "postgresql://postgres@localhost:5432/apautomation"


@dataclass(frozen=True)
class RuntimeConfig:
    app_env: AppEnv
    database_url: str | None
    artifact_container: str
    artifact_account_url: str | None
    allow_local_dashboard_auth_bypass: bool
    enable_outbound_email_forwarding: bool

    @property
    def is_azure(self) -> bool:
        return self.app_env == AppEnv.AZURE

    @property
    def is_local(self) -> bool:
        return self.app_env == AppEnv.LOCAL

    def dashboard_dsn(self) -> str:
        dsn = os.getenv("AP_DASHBOARD_DSN") or self.database_url
        if dsn:
            return dsn
        if self.is_local:
            return LOCAL_DATABASE_URL
        raise RuntimeError("AP_DASHBOARD_DSN or DATABASE_URL is required when APP_ENV=AZURE.")

    def processor_dsn(self) -> str:
        if self.database_url:
            return self.database_url
        if self.is_local:
            return LOCAL_DATABASE_URL
        raise RuntimeError("DATABASE_URL is required when APP_ENV=AZURE.")


def load_runtime_config() -> RuntimeConfig:
    app_env = _app_env(os.getenv("APP_ENV", AppEnv.LOCAL.value))
    return RuntimeConfig(
        app_env=app_env,
        database_url=os.getenv("DATABASE_URL"),
        artifact_container=os.getenv("AP_ARTIFACT_CONTAINER", "ap-artifacts"),
        artifact_account_url=_artifact_account_url(),
        allow_local_dashboard_auth_bypass=_bool_env("AP_ALLOW_LOCAL_AUTH_BYPASS", default=app_env == AppEnv.LOCAL),
        enable_outbound_email_forwarding=(
            app_env == AppEnv.AZURE
            and _bool_env("AP_ENABLE_OUTBOUND_EMAIL_FORWARDING", default=False)
        ),
    )


def project_root_from(path: str | Path) -> Path:
    return Path(path).resolve()


def _app_env(value: str) -> AppEnv:
    normalized = value.strip().upper()
    try:
        return AppEnv(normalized)
    except ValueError as exc:
        raise RuntimeError("APP_ENV must be LOCAL or AZURE.") from exc


def _artifact_account_url() -> str | None:
    explicit = os.getenv("AZURE_STORAGE_ACCOUNT_URL")
    if explicit:
        return explicit
    account_name = os.getenv("AZURE_STORAGE_ACCOUNT_NAME")
    if account_name:
        return f"https://{account_name}.blob.core.windows.net"
    return None


def _bool_env(name: str, *, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
