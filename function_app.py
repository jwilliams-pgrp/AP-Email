from __future__ import annotations

import os
import sys
import base64
import json
from pathlib import Path
from typing import Any, Callable

import azure.functions as func

script_root = Path(os.getenv("AzureWebJobsScriptRoot") or Path(__file__).resolve().parent)
if str(script_root) not in sys.path:
    sys.path.insert(0, str(script_root))


app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)


def _json_response(payload: Any, status_code: int = 200) -> func.HttpResponse:
    import json

    return func.HttpResponse(
        json.dumps(payload, default=str),
        status_code=status_code,
        mimetype="application/json",
    )


def _require_dashboard_identity(req: func.HttpRequest) -> func.HttpResponse | None:
    if os.getenv("APP_ENV", "LOCAL").strip().upper() != "AZURE":
        return None
    principal = _swa_principal({key.lower(): value for key, value in req.headers.items()})
    if principal is None:
        return _json_response({"detail": "Authenticated Static Web Apps identity is required."}, 401)
    if not principal.get("userId"):
        return _json_response({"detail": "Static Web Apps identity did not include a user id."}, 403)
    return None


def _swa_principal(headers: dict[str, str]) -> dict[str, Any] | None:
    header = headers.get("x-ms-client-principal")
    if not header:
        return None
    try:
        principal = json.loads(base64.b64decode(header).decode("utf-8"))
    except Exception:
        return None
    return principal if isinstance(principal, dict) else None


def _int_query(req: func.HttpRequest, name: str, default: int, minimum: int, maximum: int) -> int:
    value = req.params.get(name)
    if value in {None, ""}:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}.")
    return parsed


def _json_body(req: func.HttpRequest) -> dict[str, Any]:
    try:
        body = req.get_json()
    except ValueError as exc:
        raise ValueError("Request body must be valid JSON.") from exc
    if not isinstance(body, dict):
        raise ValueError("Request body must be a JSON object.")
    return body


def _dashboard_json(req: func.HttpRequest, handler: Callable[[], Any]) -> func.HttpResponse:
    auth_response = _require_dashboard_identity(req)
    if auth_response is not None:
        return auth_response
    try:
        return _json_response(handler())
    except ValueError as exc:
        return _json_response({"detail": str(exc)}, 400)
    except Exception as exc:
        from app.api.dashboard_service import DashboardError

        if isinstance(exc, DashboardError):
            return _json_response({"detail": exc.detail}, exc.status_code)
        raise


def _dashboard_file(req: func.HttpRequest, handler: Callable[[], Any]) -> func.HttpResponse:
    auth_response = _require_dashboard_identity(req)
    if auth_response is not None:
        return auth_response
    try:
        result = handler()
    except ValueError as exc:
        return _json_response({"detail": str(exc)}, 400)
    except Exception as exc:
        from app.api.dashboard_service import DashboardError

        if isinstance(exc, DashboardError):
            return _json_response({"detail": exc.detail}, exc.status_code)
        raise
    return func.HttpResponse(
        result.body,
        status_code=result.status_code,
        mimetype=result.media_type,
        headers=result.headers,
    )


@app.route(route="health", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def dashboard_health(req: func.HttpRequest) -> func.HttpResponse:
    return _dashboard_json(req, lambda: __import__("app.api.dashboard_service", fromlist=["health"]).health())


@app.route(route="monitor/summary", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def dashboard_monitor_summary(req: func.HttpRequest) -> func.HttpResponse:
    from app.api import dashboard_service as service

    return _dashboard_json(req, lambda: service.monitor_summary(req.params.get("start_date"), req.params.get("end_date")))


@app.route(route="monitor/throughput", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def dashboard_monitor_throughput(req: func.HttpRequest) -> func.HttpResponse:
    from app.api import dashboard_service as service

    return _dashboard_json(req, lambda: service.monitor_throughput(req.params.get("start_date"), req.params.get("end_date")))


@app.route(route="monitor/escalate-reasons", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def dashboard_monitor_escalate_reasons(req: func.HttpRequest) -> func.HttpResponse:
    from app.api import dashboard_service as service

    return _dashboard_json(req, lambda: service.monitor_escalate_reasons(req.params.get("start_date"), req.params.get("end_date")))


@app.route(route="monitor/ESCALATE-reasons", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def dashboard_monitor_ESCALATE_reasons(req: func.HttpRequest) -> func.HttpResponse:
    return dashboard_monitor_escalate_reasons(req)


@app.route(route="monitor/destinations", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def dashboard_monitor_destinations(req: func.HttpRequest) -> func.HttpResponse:
    from app.api import dashboard_service as service

    return _dashboard_json(req, lambda: service.monitor_destinations(req.params.get("start_date"), req.params.get("end_date")))


@app.route(route="monitor/escalate-emails", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def dashboard_monitor_escalate_emails(req: func.HttpRequest) -> func.HttpResponse:
    from app.api import dashboard_service as service

    return _dashboard_json(req, lambda: service.monitor_escalate_emails(_int_query(req, "limit", 25, 1, 100)))


@app.route(route="monitor/ESCALATE-emails", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def dashboard_monitor_ESCALATE_emails(req: func.HttpRequest) -> func.HttpResponse:
    return dashboard_monitor_escalate_emails(req)


@app.route(route="monitor/recent-runs", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def dashboard_monitor_recent_runs(req: func.HttpRequest) -> func.HttpResponse:
    from app.api import dashboard_service as service

    return _dashboard_json(
        req,
        lambda: service.monitor_recent_runs(
            _int_query(req, "limit", 25, 1, 100),
            req.params.get("start_date"),
            req.params.get("end_date"),
            req.params.get("q") or "",
        ),
    )


@app.route(route="emails/search", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def dashboard_email_search(req: func.HttpRequest) -> func.HttpResponse:
    from app.api import dashboard_service as service

    return _dashboard_json(
        req,
        lambda: service.email_search(
            req.params.get("q") or "",
            req.params.get("outcome"),
            _int_query(req, "limit", 50, 1, 200),
        ),
    )


@app.route(route="emails/{email_id}", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def dashboard_email_detail(req: func.HttpRequest) -> func.HttpResponse:
    from app.api import dashboard_service as service

    return _dashboard_json(req, lambda: service.email_detail(req.route_params["email_id"]))


@app.route(route="emails/{email_id}/html", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def dashboard_email_html(req: func.HttpRequest) -> func.HttpResponse:
    from app.api import dashboard_service as service

    return _dashboard_file(req, lambda: service.email_html(req.route_params["email_id"]))


@app.route(route="emails/{email_id}/inline/{cid_token}", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def dashboard_email_inline(req: func.HttpRequest) -> func.HttpResponse:
    from app.api import dashboard_service as service

    return _dashboard_file(
        req,
        lambda: service.email_inline_attachment(req.route_params["email_id"], req.route_params["cid_token"]),
    )


@app.route(route="emails/{email_id}/runs", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def dashboard_email_runs(req: func.HttpRequest) -> func.HttpResponse:
    from app.api import dashboard_service as service

    return _dashboard_json(req, lambda: service.email_detail(req.route_params["email_id"])["audit_runs"])


@app.route(route="audit-runs/{run_id}", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def dashboard_audit_run(req: func.HttpRequest) -> func.HttpResponse:
    from app.api import dashboard_service as service

    return _dashboard_json(req, lambda: service.audit_run(req.route_params["run_id"]))


@app.route(route="audit-runs/{run_id}/steps", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def dashboard_audit_steps(req: func.HttpRequest) -> func.HttpResponse:
    from app.api import dashboard_service as service

    return _dashboard_json(req, lambda: service.audit_steps(req.route_params["run_id"]))


@app.route(route="artifacts/{artifact_ref}", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def dashboard_artifact_ref(req: func.HttpRequest) -> func.HttpResponse:
    from app.api import dashboard_service as service

    return _dashboard_file(req, lambda: service.artifact(req.route_params["artifact_ref"]))


@app.route(route="artifacts", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def dashboard_artifact_query(req: func.HttpRequest) -> func.HttpResponse:
    from app.api import dashboard_service as service

    def handler():
        path = req.params.get("path")
        if not path:
            raise ValueError("path is required.")
        return service.artifact(path)

    return _dashboard_file(req, handler)


@app.route(route="attachments/{attachment_id}/download", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def dashboard_attachment_download(req: func.HttpRequest) -> func.HttpResponse:
    from app.api import dashboard_service as service

    return _dashboard_file(req, lambda: service.attachment_download(req.route_params["attachment_id"]))


@app.route(route="workflow/rules", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def dashboard_workflow_rules(req: func.HttpRequest) -> func.HttpResponse:
    from app.api import dashboard_service as service

    return _dashboard_json(req, service.workflow_rules)


@app.route(route="workflow/rules/{rule_code}", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def dashboard_workflow_rule(req: func.HttpRequest) -> func.HttpResponse:
    from app.api import dashboard_service as service

    def handler():
        rule_code = req.route_params["rule_code"]
        for row in service.workflow_rules():
            if row.get("rule_code") == rule_code:
                return row
        raise service.DashboardError(404, "Workflow rule not found.")

    return _dashboard_json(req, handler)


@app.route(route="workflow/destinations", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def dashboard_workflow_destinations(req: func.HttpRequest) -> func.HttpResponse:
    from app.api import dashboard_service as service

    return _dashboard_json(req, service.workflow_destinations)


@app.route(route="workflow/runtime-config", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def dashboard_workflow_runtime_config(req: func.HttpRequest) -> func.HttpResponse:
    from app.api import dashboard_service as service

    return _dashboard_json(req, service.workflow_runtime_config)


@app.route(route="workflow/audit-events", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def dashboard_workflow_audit_events(req: func.HttpRequest) -> func.HttpResponse:
    from app.api import dashboard_service as service

    return _dashboard_json(req, lambda: service.management_audit_events(_int_query(req, "limit", 50, 1, 200)))


@app.route(route="workflow/ownership", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def dashboard_workflow_ownership(req: func.HttpRequest) -> func.HttpResponse:
    from app.api import dashboard_service as service

    return _dashboard_json(req, service.workflow_ownership)


@app.route(route="workflow/ownership", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def dashboard_create_workflow_ownership(req: func.HttpRequest) -> func.HttpResponse:
    from app.api import dashboard_service as service

    return _dashboard_json(req, lambda: service.create_workflow_ownership(_json_body(req)))


@app.route(route="workflow/ownership/{ownership_name}", methods=["PATCH"], auth_level=func.AuthLevel.ANONYMOUS)
def dashboard_update_workflow_ownership(req: func.HttpRequest) -> func.HttpResponse:
    from app.api import dashboard_service as service

    return _dashboard_json(
        req,
        lambda: service.update_workflow_ownership(req.route_params["ownership_name"], _json_body(req)),
    )


@app.route(route="workflow/asset-lookup", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def dashboard_workflow_asset_lookup(req: func.HttpRequest) -> func.HttpResponse:
    from app.api import dashboard_service as service

    return _dashboard_json(req, service.workflow_asset_lookup)


@app.route(route="workflow/asset-custom", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def dashboard_workflow_asset_custom(req: func.HttpRequest) -> func.HttpResponse:
    from app.api import dashboard_service as service

    return _dashboard_json(req, service.workflow_asset_custom)


@app.route(route="workflow/asset-custom", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def dashboard_create_workflow_asset_custom(req: func.HttpRequest) -> func.HttpResponse:
    from app.api import dashboard_service as service

    return _dashboard_json(req, lambda: service.create_workflow_asset_custom(_json_body(req)))


@app.route(route="workflow/asset-custom/{asset_custom_id}", methods=["PATCH"], auth_level=func.AuthLevel.ANONYMOUS)
def dashboard_update_workflow_asset_custom(req: func.HttpRequest) -> func.HttpResponse:
    from app.api import dashboard_service as service

    return _dashboard_json(
        req,
        lambda: service.update_workflow_asset_custom(req.route_params["asset_custom_id"], _json_body(req)),
    )


@app.route(route="workflow/asset-custom/{asset_custom_id}", methods=["DELETE"], auth_level=func.AuthLevel.ANONYMOUS)
def dashboard_delete_workflow_asset_custom(req: func.HttpRequest) -> func.HttpResponse:
    from app.api import dashboard_service as service

    return _dashboard_json(req, lambda: service.delete_workflow_asset_custom(req.route_params["asset_custom_id"]))


@app.route(route="workflow/assets", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def dashboard_workflow_assets(req: func.HttpRequest) -> func.HttpResponse:
    from app.api import dashboard_service as service

    return _dashboard_json(req, service.workflow_assets)


@app.route(route="monitor/assets", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def dashboard_monitor_assets(req: func.HttpRequest) -> func.HttpResponse:
    return dashboard_workflow_assets(req)


@app.route(route="workflow/assets", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def dashboard_create_workflow_asset(req: func.HttpRequest) -> func.HttpResponse:
    from app.api import dashboard_service as service

    return _dashboard_json(req, lambda: service.create_workflow_asset(_json_body(req)))


@app.route(route="monitor/assets", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def dashboard_create_monitor_asset(req: func.HttpRequest) -> func.HttpResponse:
    return dashboard_create_workflow_asset(req)


@app.route(route="workflow/assets/{asset_id}", methods=["PATCH"], auth_level=func.AuthLevel.ANONYMOUS)
def dashboard_update_workflow_asset(req: func.HttpRequest) -> func.HttpResponse:
    from app.api import dashboard_service as service

    return _dashboard_json(req, lambda: service.update_workflow_asset(req.route_params["asset_id"], _json_body(req)))


@app.route(route="monitor/assets/{asset_id}", methods=["PATCH"], auth_level=func.AuthLevel.ANONYMOUS)
def dashboard_update_monitor_asset(req: func.HttpRequest) -> func.HttpResponse:
    return dashboard_update_workflow_asset(req)


@app.route(route="workflow/assets/{asset_id}", methods=["DELETE"], auth_level=func.AuthLevel.ANONYMOUS)
def dashboard_delete_workflow_asset(req: func.HttpRequest) -> func.HttpResponse:
    from app.api import dashboard_service as service

    return _dashboard_json(req, lambda: service.deactivate_workflow_asset(req.route_params["asset_id"]))


@app.route(route="monitor/assets/{asset_id}", methods=["DELETE"], auth_level=func.AuthLevel.ANONYMOUS)
def dashboard_delete_monitor_asset(req: func.HttpRequest) -> func.HttpResponse:
    return dashboard_delete_workflow_asset(req)


@app.route(route="workflow/rules/{rule_code}", methods=["PATCH"], auth_level=func.AuthLevel.ANONYMOUS)
def dashboard_update_workflow_rule(req: func.HttpRequest) -> func.HttpResponse:
    from app.api import dashboard_service as service

    return _dashboard_json(req, lambda: service.update_workflow_rule(req.route_params["rule_code"], _json_body(req)))


@app.route(
    route="workflow/rules/{rule_code}/conditions/{condition_key}",
    methods=["PATCH"],
    auth_level=func.AuthLevel.ANONYMOUS,
)
def dashboard_update_workflow_rule_condition(req: func.HttpRequest) -> func.HttpResponse:
    return _dashboard_json(
        req,
        lambda: (_ for _ in ()).throw(
            __import__("app.api.dashboard_service", fromlist=["DashboardError"]).DashboardError(
                501,
                "Workflow rule condition updates are not implemented yet.",
            )
        ),
    )


@app.route(route="workflow/destinations/{destination_code}", methods=["PATCH"], auth_level=func.AuthLevel.ANONYMOUS)
def dashboard_update_workflow_destination(req: func.HttpRequest) -> func.HttpResponse:
    return _dashboard_json(
        req,
        lambda: (_ for _ in ()).throw(
            __import__("app.api.dashboard_service", fromlist=["DashboardError"]).DashboardError(
                501,
                "Workflow destination updates are not implemented yet.",
            )
        ),
    )


@app.route(route="workflow/runtime-config/{config_key}", methods=["PATCH"], auth_level=func.AuthLevel.ANONYMOUS)
def dashboard_update_runtime_config(req: func.HttpRequest) -> func.HttpResponse:
    from app.api import dashboard_service as service

    return _dashboard_json(req, lambda: service.update_runtime_config(req.route_params["config_key"], _json_body(req)))


@app.route(route="process-graph-intake", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def process_graph_intake(req: func.HttpRequest) -> func.HttpResponse:
    expected_principal_id = os.getenv("LOGIC_APP_PRINCIPAL_ID")
    if os.getenv("APP_ENV", "LOCAL").strip().upper() == "AZURE" and expected_principal_id:
        actual_principal_id = req.headers.get("x-ms-client-principal-id")
        if actual_principal_id != expected_principal_id:
            return func.HttpResponse('{"status":"unauthorized"}', status_code=401, mimetype="application/json")
    if os.getenv("AP_PROCESS_GRAPH_INTAKE", "true").strip().lower() not in {"1", "true", "yes", "on"}:
        return func.HttpResponse('{"status":"disabled"}', status_code=202, mimetype="application/json")
    from ap_automation.repositories.postgres import PostgresRepository
    from ap_automation.services.azure_openai_extractor import AzureOpenAIExtractor
    from ap_automation.services.escalate_sync import EscalateMailboxSync
    from ap_automation.services.graph_mailbox import GraphMailboxClient
    from ap_automation.services.local_processor import LocalProcessor
    from ap_automation.services.teams_notifier import TeamsNotifier

    database_url = os.environ["DATABASE_URL"]
    project_root = script_root
    repository = PostgresRepository(database_url)
    graph_mailbox = GraphMailboxClient.from_env()
    teams_notifier = TeamsNotifier.from_env() if os.environ.get("TEAMS-WEBHOOK-URL-PROPERTIES-AP") else None
    processor = LocalProcessor(
        project_root,
        repository,
        repository,
        AzureOpenAIExtractor(project_root),
        graph_mailbox,
        teams_notifier,
    )
    envelope = graph_mailbox.fetch_latest_from_intake()
    if envelope is None:
        return func.HttpResponse('{"status":"empty"}', status_code=200, mimetype="application/json")
    run_id = processor.process_graph_email(envelope)
    EscalateMailboxSync(graph_mailbox, repository).sync()
    return func.HttpResponse(f'{{"status":"processed","run_id":"{run_id}"}}', status_code=200, mimetype="application/json")
