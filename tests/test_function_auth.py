from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ap_automation.services.auth import (
    auth_diagnostics_from_headers,
    has_dashboard_user_role,
    principal_from_headers,
    principal_matches_object_id,
)
from function_app import _require_dashboard_identity, _require_hosted_auth_diagnostic_identity, _require_logic_app_identity


def _principal_header(payload: dict[str, object]) -> dict[str, str]:
    encoded = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
    return {"x-ms-client-principal": encoded}


def test_principal_from_headers_normalizes_roles() -> None:
    principal = principal_from_headers(_principal_header({"userId": "user-1", "userRoles": ["User"]}))

    assert principal is not None
    assert principal.user_id == "user-1"
    assert principal.roles == frozenset({"user"})
    assert has_dashboard_user_role(principal)


def test_principal_from_headers_rejects_malformed_header() -> None:
    assert principal_from_headers({"x-ms-client-principal": "not-base64"}) is None


def test_principal_from_headers_allows_swa_user_with_default_roles() -> None:
    principal = principal_from_headers(
        _principal_header({"userId": "user-1", "userRoles": ["user", "anonymous", "authenticated"]})
    )

    assert principal is not None
    assert has_dashboard_user_role(principal)


def test_auth_diagnostics_returns_sanitized_facts_only() -> None:
    diagnostics = auth_diagnostics_from_headers(
        _principal_header(
            {
                "userId": "user-1",
                "userDetails": "Jeremy.Williams@hillwood.com",
                "userRoles": ["user", "anonymous", "authenticated"],
            }
        )
        | {"Authorization": "Bearer secret-token"}
    )

    assert diagnostics["has_x_ms_client_principal"]
    assert diagnostics["principal_user_id_present"]
    assert diagnostics["principal_roles"] == ["anonymous", "authenticated", "user"]
    assert diagnostics["has_dashboard_user_role"]
    assert "Authorization" not in diagnostics
    assert "secret-token" not in str(diagnostics)
    assert "Jeremy.Williams" not in str(diagnostics)


def test_principal_matches_logic_app_object_id_from_header_fallback() -> None:
    principal = principal_from_headers({"x-ms-client-principal-id": "11111111-2222-3333-4444-555555555555"})

    assert principal_matches_object_id(principal, "11111111-2222-3333-4444-555555555555")


def test_dashboard_identity_allows_local_without_headers(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "LOCAL")

    assert _require_dashboard_identity(SimpleNamespace(headers={})) is None


def test_dashboard_identity_rejects_missing_azure_principal(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "AZURE")

    response = _require_dashboard_identity(SimpleNamespace(headers={}))

    assert response is not None
    assert response.status_code == 401


def test_dashboard_identity_requires_user_role(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "AZURE")
    req = SimpleNamespace(headers=_principal_header({"userId": "user-1", "userRoles": ["authenticated"]}))

    response = _require_dashboard_identity(req)

    assert response is not None
    assert response.status_code == 403


def test_dashboard_identity_allows_swa_user_role(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "AZURE")
    req = SimpleNamespace(headers=_principal_header({"userId": "user-1", "userRoles": ["user", "anonymous", "authenticated"]}))

    assert _require_dashboard_identity(req) is None


def test_hosted_auth_diagnostics_requires_identity_in_azure(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "AZURE")

    response = _require_hosted_auth_diagnostic_identity(SimpleNamespace(headers={}))

    assert response is not None
    assert response.status_code == 401


def test_hosted_auth_diagnostics_allows_easyauth_header_facts(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "AZURE")
    req = SimpleNamespace(headers={"x-ms-client-principal-name": "Jeremy.Williams@hillwood.com"})

    assert _require_hosted_auth_diagnostic_identity(req) is None


def test_logic_app_identity_allows_local_without_headers(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "LOCAL")
    monkeypatch.delenv("LOGIC_APP_PRINCIPAL_ID", raising=False)

    assert _require_logic_app_identity(SimpleNamespace(headers={})) is None


def test_logic_app_identity_requires_configured_expected_id(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "AZURE")
    monkeypatch.delenv("LOGIC_APP_PRINCIPAL_ID", raising=False)

    response = _require_logic_app_identity(SimpleNamespace(headers={}))

    assert response is not None
    assert response.status_code == 401


def test_logic_app_identity_rejects_wrong_principal(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "AZURE")
    monkeypatch.setenv("LOGIC_APP_PRINCIPAL_ID", "11111111-2222-3333-4444-555555555555")
    req = SimpleNamespace(headers={"x-ms-client-principal-id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"})

    response = _require_logic_app_identity(req)

    assert response is not None
    assert response.status_code == 403


def test_logic_app_identity_allows_matching_principal(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "AZURE")
    monkeypatch.setenv("LOGIC_APP_PRINCIPAL_ID", "11111111-2222-3333-4444-555555555555")
    req = SimpleNamespace(headers={"x-ms-client-principal-id": "11111111-2222-3333-4444-555555555555"})

    assert _require_logic_app_identity(req) is None
