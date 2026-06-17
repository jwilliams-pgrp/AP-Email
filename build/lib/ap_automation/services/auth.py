from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AuthPrincipal:
    user_id: str | None
    roles: frozenset[str]
    raw: dict[str, Any]


def auth_diagnostics_from_headers(headers: dict[str, str]) -> dict[str, Any]:
    normalized = {key.lower(): value for key, value in headers.items()}
    principal = principal_from_headers(headers)
    auth_header_names = sorted(
        key
        for key in normalized
        if key.startswith("x-ms-client-principal")
        or key.startswith("x-ms-token-")
        or key in {"x-ms-auth-token", "x-ms-authentication-token"}
    )
    return {
        "has_x_ms_client_principal": "x-ms-client-principal" in normalized,
        "has_x_ms_client_principal_id": bool((normalized.get("x-ms-client-principal-id") or "").strip()),
        "has_x_ms_client_principal_name": bool((normalized.get("x-ms-client-principal-name") or "").strip()),
        "has_x_ms_client_principal_idp": bool((normalized.get("x-ms-client-principal-idp") or "").strip()),
        "present_auth_header_names": auth_header_names,
        "principal_decoded": principal is not None and bool(principal.raw),
        "principal_user_id_present": principal is not None and bool(principal.user_id),
        "principal_roles": sorted(principal.roles) if principal is not None else [],
        "principal_keys": sorted(str(key) for key in principal.raw.keys()) if principal is not None else [],
        "has_dashboard_user_role": has_dashboard_user_role(principal) if principal is not None else False,
    }


def principal_from_headers(headers: dict[str, str]) -> AuthPrincipal | None:
    normalized = {key.lower(): value for key, value in headers.items()}
    header = normalized.get("x-ms-client-principal")
    if not header:
        principal_id = normalized.get("x-ms-client-principal-id")
        if principal_id:
            return AuthPrincipal(user_id=principal_id.strip() or None, roles=frozenset(), raw={})
        return None
    try:
        decoded = base64.b64decode(header).decode("utf-8")
        principal = json.loads(decoded)
    except Exception:
        return None
    if not isinstance(principal, dict):
        return None
    user_id = _first_text(principal, "userId", "user_id", "oid", "objectId")
    return AuthPrincipal(user_id=user_id, roles=_roles_from_principal(principal), raw=principal)


def has_dashboard_user_role(principal: AuthPrincipal) -> bool:
    return "user" in principal.roles


def principal_matches_object_id(principal: AuthPrincipal | None, expected_object_id: str | None) -> bool:
    expected = (expected_object_id or "").strip().lower()
    if not expected or principal is None:
        return False
    candidates = {value.lower() for value in _principal_id_candidates(principal) if value}
    return expected in candidates


def _roles_from_principal(principal: dict[str, Any]) -> frozenset[str]:
    roles_value = principal.get("userRoles") or principal.get("roles")
    roles: set[str] = set()
    if isinstance(roles_value, list):
        roles.update(str(role).strip().lower() for role in roles_value if str(role).strip())
    elif isinstance(roles_value, str):
        roles.update(role.strip().lower() for role in roles_value.replace(";", ",").split(",") if role.strip())
    claims = principal.get("claims")
    if isinstance(claims, list):
        for claim in claims:
            if not isinstance(claim, dict):
                continue
            typ = str(claim.get("typ") or claim.get("type") or "").lower()
            if typ in {"roles", "role"}:
                value = str(claim.get("val") or claim.get("value") or "").strip().lower()
                if value:
                    roles.add(value)
    return frozenset(roles)


def _principal_id_candidates(principal: AuthPrincipal) -> set[str]:
    candidates = {principal.user_id or ""}
    raw = principal.raw
    for key in ("userId", "user_id", "oid", "objectId"):
        value = raw.get(key)
        if isinstance(value, str):
            candidates.add(value.strip())
    claims = raw.get("claims")
    if isinstance(claims, list):
        for claim in claims:
            if not isinstance(claim, dict):
                continue
            typ = str(claim.get("typ") or claim.get("type") or "").lower()
            if typ in {
                "oid",
                "objectidentifier",
                "http://schemas.microsoft.com/identity/claims/objectidentifier",
                "http://schemas.microsoft.com/identity/claims/userobjectid",
            }:
                value = str(claim.get("val") or claim.get("value") or "").strip()
                if value:
                    candidates.add(value)
    return candidates


def _first_text(principal: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = principal.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None
