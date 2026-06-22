from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ap_automation.config import AppEnv
from ap_automation.services.auth import auth_diagnostics_from_headers, has_dashboard_user_role, principal_from_headers

from . import dashboard_service as service

_rewrite_cid_urls = service._rewrite_cid_urls


connect = service.connect
require_local_artifact = service.require_local_artifact


def _resolve_inline_attachment_path(email_id: str, cid_token: str):
    service.connect = connect
    service.require_local_artifact = require_local_artifact
    try:
        return service._resolve_inline_attachment_path(email_id, cid_token)
    except service.DashboardError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _asset_payload(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return service._asset_payload(payload)
    except service.DashboardError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


app = FastAPI(title="AP Automation Dashboard API")
_cors_origins = [
    origin.strip()
    for origin in os.getenv("AP_DASHBOARD_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(service.DashboardError)
async def dashboard_error_handler(_request: Request, exc: service.DashboardError) -> JSONResponse:
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


@app.middleware("http")
async def require_swa_identity(request: Request, call_next):
    if service.RUNTIME_CONFIG.app_env != AppEnv.AZURE:
        return await call_next(request)
    if not request.url.path.startswith("/api"):
        return await call_next(request)
    if request.url.path == "/api/auth/diagnostics":
        return await call_next(request)
    principal = principal_from_headers(dict(request.headers))
    if principal is None:
        return JSONResponse({"detail": "Authenticated Static Web Apps identity is required."}, status_code=401)
    if not principal.user_id:
        return JSONResponse({"detail": "Static Web Apps identity did not include a user id."}, status_code=403)
    if not has_dashboard_user_role(principal):
        return JSONResponse({"detail": "Static Web Apps identity is not assigned the dashboard user role."}, status_code=403)
    return await call_next(request)


def _require_hosted_auth_diagnostic_identity(headers: dict[str, str]) -> None:
    if service.RUNTIME_CONFIG.app_env != AppEnv.AZURE:
        return
    diagnostics = auth_diagnostics_from_headers(headers)
    if (
        not diagnostics["has_x_ms_client_principal"]
        and not diagnostics["has_x_ms_client_principal_id"]
        and not diagnostics["has_x_ms_client_principal_name"]
    ):
        raise HTTPException(status_code=401, detail="Authenticated hosted identity is required.")


def _file_response(result: service.DashboardResponse) -> Response:
    return Response(
        result.body,
        status_code=result.status_code,
        media_type=result.media_type,
        headers=result.headers,
    )


@app.get("/api/health")
def health() -> dict[str, Any]:
    return service.health()


@app.get("/api/auth/diagnostics")
def auth_diagnostics(request: Request) -> dict[str, Any]:
    headers = dict(request.headers)
    _require_hosted_auth_diagnostic_identity(headers)
    return auth_diagnostics_from_headers(headers)


@app.get("/api/monitor/summary")
def monitor_summary(start_date: str | None = None, end_date: str | None = None) -> dict[str, Any]:
    return service.monitor_summary(start_date, end_date)


@app.get("/api/monitor/throughput")
def monitor_throughput(start_date: str | None = None, end_date: str | None = None) -> list[dict[str, Any]]:
    return service.monitor_throughput(start_date, end_date)


@app.get("/api/monitor/escalate-reasons")
@app.get("/api/monitor/ESCALATE-reasons")
def monitor_escalate_reasons(start_date: str | None = None, end_date: str | None = None) -> list[dict[str, Any]]:
    return service.monitor_escalate_reasons(start_date, end_date)


@app.get("/api/monitor/destinations")
def monitor_destinations(start_date: str | None = None, end_date: str | None = None) -> list[dict[str, Any]]:
    return service.monitor_destinations(start_date, end_date)


@app.get("/api/monitor/escalate-emails")
@app.get("/api/monitor/ESCALATE-emails")
def monitor_escalate_emails(limit: int = Query(25, ge=1, le=100)) -> list[dict[str, Any]]:
    return service.monitor_escalate_emails(limit)


@app.get("/api/monitor/recent-runs")
def monitor_recent_runs(
    limit: int = Query(25, ge=1, le=100),
    start_date: str | None = None,
    end_date: str | None = None,
    q: str = "",
) -> list[dict[str, Any]]:
    return service.monitor_recent_runs(limit, start_date, end_date, q)


@app.get("/api/emails/search")
def email_search(
    q: str = "",
    outcome: str | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> list[dict[str, Any]]:
    return service.email_search(q, outcome, limit)


@app.get("/api/emails/{email_id}")
def email_detail(email_id: str) -> dict[str, Any]:
    return service.email_detail(email_id)


@app.get("/api/emails/{email_id}/html")
def email_html(email_id: str) -> Response:
    return _file_response(service.email_html(email_id))


@app.get("/api/emails/{email_id}/inline/{cid_token:path}")
def email_inline_attachment(email_id: str, cid_token: str) -> Response:
    return _file_response(service.email_inline_attachment(email_id, cid_token))


@app.get("/api/emails/{email_id}/runs")
def email_runs(email_id: str) -> list[dict[str, Any]]:
    return service.email_detail(email_id)["audit_runs"]


@app.get("/api/audit-runs/{run_id}")
def audit_run(run_id: str) -> dict[str, Any]:
    return service.audit_run(run_id)


@app.get("/api/audit-runs/{run_id}/steps")
def audit_steps(run_id: str) -> list[dict[str, Any]]:
    return service.audit_steps(run_id)


@app.get("/api/artifacts")
def artifact(path: str) -> Response:
    return _file_response(service.artifact(path))


@app.get("/api/artifacts/{artifact_ref:path}")
def artifact_by_ref(artifact_ref: str) -> Response:
    return _file_response(service.artifact(artifact_ref))


@app.get("/api/attachments/{attachment_id}/download")
def attachment_download(attachment_id: str) -> Response:
    return _file_response(service.attachment_download(attachment_id))


@app.get("/api/workflow/rules")
def workflow_rules() -> list[dict[str, Any]]:
    return service.workflow_rules()


@app.get("/api/workflow/rules/{rule_code}")
def workflow_rule(rule_code: str) -> dict[str, Any]:
    for row in service.workflow_rules():
        if row.get("rule_code") == rule_code:
            return row
    raise HTTPException(status_code=404, detail="Workflow rule not found.")


@app.get("/api/workflow/destinations")
def workflow_destinations() -> list[dict[str, Any]]:
    return service.workflow_destinations()


@app.get("/api/workflow/runtime-config")
def workflow_runtime_config() -> list[dict[str, Any]]:
    return service.workflow_runtime_config()


@app.get("/api/workflow/process-control")
def workflow_process_control() -> dict[str, Any]:
    return service.workflow_process_control()


@app.patch("/api/workflow/process-control")
def update_workflow_process_control(payload: dict[str, Any]) -> dict[str, Any]:
    return service.update_workflow_process_control(payload)


@app.get("/api/workflow/audit-events")
def management_audit_events(limit: int = Query(50, ge=1, le=200)) -> list[dict[str, Any]]:
    return service.management_audit_events(limit)


@app.get("/api/workflow/ownership")
def workflow_ownership() -> list[dict[str, Any]]:
    return service.workflow_ownership()


@app.post("/api/workflow/ownership")
def create_workflow_ownership(payload: dict[str, Any]) -> dict[str, Any]:
    return service.create_workflow_ownership(payload)


@app.patch("/api/workflow/ownership/{ownership_name}")
def update_workflow_ownership(ownership_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    return service.update_workflow_ownership(ownership_name, payload)


@app.get("/api/workflow/asset-lookup")
def workflow_asset_lookup() -> list[dict[str, Any]]:
    return service.workflow_asset_lookup()


@app.get("/api/workflow/asset-custom")
def workflow_asset_custom() -> list[dict[str, Any]]:
    return service.workflow_asset_custom()


@app.post("/api/workflow/asset-custom")
def create_workflow_asset_custom(payload: dict[str, Any]) -> dict[str, Any]:
    return service.create_workflow_asset_custom(payload)


@app.patch("/api/workflow/asset-custom/{asset_custom_id}")
def update_workflow_asset_custom(asset_custom_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return service.update_workflow_asset_custom(asset_custom_id, payload)


@app.delete("/api/workflow/asset-custom/{asset_custom_id}")
def delete_workflow_asset_custom(asset_custom_id: str) -> dict[str, Any]:
    return service.delete_workflow_asset_custom(asset_custom_id)


@app.get("/api/workflow/assets")
@app.get("/api/monitor/assets")
def workflow_assets() -> list[dict[str, Any]]:
    return service.workflow_assets()


@app.post("/api/workflow/assets")
@app.post("/api/monitor/assets")
def create_workflow_asset(payload: dict[str, Any]) -> dict[str, Any]:
    return service.create_workflow_asset(payload)


@app.patch("/api/workflow/assets/{asset_id}")
@app.patch("/api/monitor/assets/{asset_id}")
def update_workflow_asset(asset_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return service.update_workflow_asset(asset_id, payload)


@app.delete("/api/workflow/assets/{asset_id}")
@app.delete("/api/monitor/assets/{asset_id}")
def deactivate_workflow_asset(asset_id: str) -> dict[str, Any]:
    return service.deactivate_workflow_asset(asset_id)


@app.patch("/api/workflow/rules/{rule_code}")
def update_workflow_rule(rule_code: str, payload: dict[str, Any]) -> dict[str, Any]:
    return service.update_workflow_rule(rule_code, payload)


@app.patch("/api/workflow/rules/{rule_code}/conditions/{condition_key}")
def update_workflow_rule_condition(rule_code: str, condition_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    raise HTTPException(status_code=501, detail="Workflow rule condition updates are not implemented yet.")


@app.patch("/api/workflow/destinations/{destination_code}")
def update_workflow_destination(destination_code: str, payload: dict[str, Any]) -> dict[str, Any]:
    raise HTTPException(status_code=501, detail="Workflow destination updates are not implemented yet.")


@app.patch("/api/workflow/runtime-config/{config_key}")
def update_runtime_config(config_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    return service.update_runtime_config(config_key, payload)
