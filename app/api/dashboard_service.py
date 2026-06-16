from __future__ import annotations

import base64
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import psycopg
from psycopg.rows import dict_row

from ap_automation.config import load_runtime_config
from ap_automation.services.local_artifacts import artifact_store_from_env


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCAL_ROOT = PROJECT_ROOT / "local"
RUNTIME_CONFIG = load_runtime_config()
DSN = RUNTIME_CONFIG.dashboard_dsn()
ARTIFACTS = artifact_store_from_env(PROJECT_ROOT)


def connect():
    if RUNTIME_CONFIG.app_env.value == "AZURE" and "password=" not in DSN.lower():
        try:
            from azure.identity import DefaultAzureCredential
        except Exception as exc:
            raise RuntimeError("azure-identity is required for Azure dashboard Postgres authentication.") from exc
        token = DefaultAzureCredential().get_token("https://ossrdbms-aad.database.windows.net/.default").token
        return psycopg.connect(DSN, password=token, row_factory=dict_row)
    return psycopg.connect(DSN, row_factory=dict_row)


class DashboardError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class DashboardResponse:
    def __init__(
        self,
        body: bytes | str,
        media_type: str,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.body = body
        self.media_type = media_type
        self.status_code = status_code
        self.headers = headers or {}


def json_ready(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, list):
        return [json_ready(item) for item in value]
    if isinstance(value, dict):
        return {key: json_ready(item) for key, item in value.items()}
    return value


def cutoff(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def default_date_window() -> tuple[date, date]:
    end = datetime.now(timezone.utc).date()
    return end - timedelta(days=14), end


def parse_date_window(start_date: str | None = None, end_date: str | None = None) -> tuple[datetime, datetime, date, date]:
    default_start, default_end = default_date_window()
    try:
        start_day = date.fromisoformat(start_date) if start_date else default_start
        end_day = date.fromisoformat(end_date) if end_date else default_end
    except ValueError as exc:
        raise DashboardError(400, "Dates must use YYYY-MM-DD format.") from exc
    if start_day > end_day:
        raise DashboardError(400, "start_date must be on or before end_date.")
    start = datetime.combine(start_day, datetime.min.time(), tzinfo=timezone.utc)
    end_exclusive = datetime.combine(end_day + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    return start, end_exclusive, start_day, end_day


def _email_search_predicate() -> str:
    return """
              %s = '%%' or lower(coalesce(e.subject, '')) like %s
              or lower(coalesce(e.sender_email, '')) like %s
              or lower(coalesce(e.metadata ->> 'sender_name', '')) like %s
              or lower(coalesce(e.metadata::text, '')) like %s
              or lower(coalesce(e.source_message_id, '')) like %s
              or lower(coalesce(e.idempotency_key, '')) like %s
              or lower(e.email_id::text) like %s
              or lower(coalesce(d.reason, '')) like %s
              or lower(coalesce(d.destination_code, '')) like %s
              or lower(coalesce(d.matched_rule_code, '')) like %s
              or lower(coalesce(d.extracted_fields::text, '')) like %s
              or lower(coalesce(i.vendor_name, '')) like %s
              or lower(coalesce(i.invoice_number, '')) like %s
              or lower(coalesce(i.amount::text, '')) like %s
              or lower(coalesce(i.metadata::text, '')) like %s
              or lower(coalesce(x.parsed_output::text, '')) like %s
    """


def _email_search_params(q: str) -> list[Any]:
    pattern = f"%{q.lower()}%"
    return [pattern] * 17


def _latest_extraction_lateral_sql() -> str:
    return """
              select x2.*
              from extractions x2
              where x2.email_id = e.email_id
              order by x2.created_at desc
              limit 1
    """


def require_local_artifact(path_value: str | None) -> Path:
    if not path_value:
        raise DashboardError(404, "Artifact path is missing.")
    path = (PROJECT_ROOT / path_value).resolve()
    try:
        path.relative_to(LOCAL_ROOT.resolve())
    except ValueError as exc:
        raise DashboardError(403, "Artifact path is outside the approved local artifact root.") from exc
    if not path.exists() or not path.is_file():
        raise DashboardError(404, f"Artifact does not exist: {path_value}")
    return path


def read_artifact_bytes(path_value: str | None) -> bytes:
    if not path_value:
        raise DashboardError(404, "Artifact path is missing.")
    if path_value.startswith("blob://"):
        try:
            return ARTIFACTS.read_bytes(path_value)
        except Exception as exc:
            raise DashboardError(404, "Blob artifact could not be read.") from exc
    return require_local_artifact(path_value).read_bytes()


def read_artifact_text(path_value: str | None) -> str:
    if not path_value:
        raise DashboardError(404, "Artifact path is missing.")
    if path_value.startswith("blob://"):
        try:
            return ARTIFACTS.read_text(path_value)
        except Exception as exc:
            raise DashboardError(404, "Blob artifact could not be read.") from exc
    return require_local_artifact(path_value).read_text(encoding="utf-8")


def swa_principal(headers: dict[str, str]) -> dict[str, Any] | None:
    header = headers.get("x-ms-client-principal")
    if not header:
        return None
    try:
        decoded = base64.b64decode(header).decode("utf-8")
        principal = json.loads(decoded)
    except Exception:
        return None
    return principal if isinstance(principal, dict) else None


_CID_URL_PATTERN = re.compile(r"""(?P<prefix>["'\(])cid:(?P<cid>[^"'\)\s>]+)""", re.IGNORECASE)


def _normalize_content_id(value: str | None) -> str:
    if not value:
        return ""
    return value.strip().strip("<>").strip().lower()


def _resolve_inline_attachment_path(email_id: str, cid_token: str) -> tuple[Path | str, str]:
    token = _normalize_content_id(cid_token)
    if not token:
        raise DashboardError(404, "Inline attachment not found.")
    with connect() as conn:
        rows = conn.execute(
            """
            select file_name, content_type, storage_path, metadata
            from attachments
            where email_id = %s
            """,
            (email_id,),
        ).fetchall()
    for row in rows:
        metadata = row.get("metadata") or {}
        content_id = _normalize_content_id(metadata.get("content_id") if isinstance(metadata, dict) else None)
        file_name = _normalize_content_id(row.get("file_name"))
        if token == content_id or token == file_name:
            storage_path = row["storage_path"]
            if isinstance(storage_path, str) and storage_path.startswith("blob://"):
                return storage_path, row.get("content_type") or "application/octet-stream"
            return require_local_artifact(storage_path), row.get("content_type") or "application/octet-stream"
    raise DashboardError(404, "Inline attachment not found.")


def _rewrite_cid_urls(html: str, email_id: str) -> str:
    def replacer(match: re.Match[str]) -> str:
        cid_value = quote(match.group("cid"), safe="")
        return f'{match.group("prefix")}/api/emails/{email_id}/inline/{cid_value}'

    return _CID_URL_PATTERN.sub(replacer, html)


def health() -> dict[str, Any]:
    with connect() as conn:
        db_time = conn.execute("select now() as db_time").fetchone()["db_time"]
    return {"status": "ok", "db_time": json_ready(db_time), "mode": RUNTIME_CONFIG.app_env.value}


def monitor_summary(start_date: str | None = None, end_date: str | None = None) -> dict[str, Any]:
    start, end_exclusive, start_day, end_day = parse_date_window(start_date, end_date)
    with connect() as conn:
        outcome_rows = conn.execute(
            """
            select d.outcome::text as outcome, count(*)::int as count
            from decisions d
            where d.created_at >= %s and d.created_at < %s
            group by d.outcome
            """,
            (start, end_exclusive),
        ).fetchall()
        run_rows = conn.execute(
            """
            select status, count(*)::int as count
            from audit_runs
            where started_at >= %s and started_at < %s
            group by status
            """,
            (start, end_exclusive),
        ).fetchall()
        open_escalate = conn.execute("select count(*)::int as count from escalate_queue").fetchone()["count"]
        avg_seconds = conn.execute(
            """
            select avg(extract(epoch from completed_at - started_at)) as seconds
            from audit_runs
            where started_at >= %s and started_at < %s and completed_at is not null
            """,
            (start, end_exclusive),
        ).fetchone()["seconds"]
        confidence = conn.execute(
            """
            select
              count(*) filter (where confidence >= 0.95)::int as high,
              count(*) filter (where confidence >= 0.90 and confidence < 0.95)::int as medium,
              count(*) filter (where confidence < 0.90)::int as low
            from decisions
            where created_at >= %s and created_at < %s
            """,
            (start, end_exclusive),
        ).fetchone()

    outcomes = {row["outcome"]: row["count"] for row in outcome_rows}
    runs = {row["status"]: row["count"] for row in run_rows}
    total = sum(outcomes.values())

    def rate(name: str) -> float:
        return round((outcomes.get(name, 0) / total) * 100, 1) if total else 0.0

    return json_ready(
        {
            "start_date": start_day.isoformat(),
            "end_date": end_day.isoformat(),
            "days": (end_day - start_day).days + 1,
            "total_processed": total,
            "outcomes": outcomes,
            "runs": runs,
            "rates": {
                "AUTO": rate("AUTO"),
                "ESCALATE": rate("ESCALATE"),
                "FILE": rate("FILE"),
                "FLAG": rate("FLAG"),
                "DISCARD": rate("DISCARD"),
            },
            "failed_run_rate": round((runs.get("failed", 0) / max(sum(runs.values()), 1)) * 100, 1),
            "open_escalate_count": open_escalate,
            "avg_processing_seconds": round(float(avg_seconds), 2) if avg_seconds is not None else None,
            "confidence": confidence,
        }
    )


def monitor_throughput(start_date: str | None = None, end_date: str | None = None) -> list[dict[str, Any]]:
    start, end_exclusive, _start_day, _end_day = parse_date_window(start_date, end_date)
    with connect() as conn:
        decision_rows = conn.execute(
            """
            select date_trunc('day', d.created_at)::date as day, d.outcome::text as outcome, count(*)::int as count
            from decisions d
            where d.created_at >= %s and d.created_at < %s and d.outcome in ('AUTO', 'ESCALATE', 'FILE')
            group by 1, 2
            order by 1
            """,
            (start, end_exclusive),
        ).fetchall()
        failed_rows = conn.execute(
            """
            select date_trunc('day', started_at)::date as day, count(*)::int as count
            from audit_runs
            where started_at >= %s and started_at < %s and status = 'failed'
            group by 1
            order by 1
            """,
            (start, end_exclusive),
        ).fetchall()
    by_day: dict[str, dict[str, Any]] = {}
    outcome_to_category = {"AUTO": "automated", "ESCALATE": "escalate", "FILE": "filed"}
    for row in decision_rows:
        key = row["day"].isoformat()
        by_day.setdefault(key, {"day": key, "automated": 0, "escalate": 0, "failed": 0, "filed": 0})
        by_day[key][outcome_to_category[row["outcome"]]] = row["count"]
    for row in failed_rows:
        key = row["day"].isoformat()
        by_day.setdefault(key, {"day": key, "automated": 0, "escalate": 0, "failed": 0, "filed": 0})
        by_day[key]["failed"] = row["count"]
    return [by_day[key] for key in sorted(by_day)]


def monitor_escalate_reasons(start_date: str | None = None, end_date: str | None = None) -> list[dict[str, Any]]:
    start, end_exclusive, _start_day, _end_day = parse_date_window(start_date, end_date)
    with connect() as conn:
        rows = conn.execute(
            """
            select coalesce(rq.reason, d.reason) as reason, count(*)::int as count
            from decisions d
            left join escalate_queue rq on rq.decision_id = d.decision_id
            where d.created_at >= %s and d.created_at < %s and d.outcome in ('ESCALATE', 'FLAG')
            group by 1
            order by count(*) desc, reason
            limit 10
            """,
            (start, end_exclusive),
        ).fetchall()
    return rows


def monitor_destinations(start_date: str | None = None, end_date: str | None = None) -> list[dict[str, Any]]:
    start, end_exclusive, _start_day, _end_day = parse_date_window(start_date, end_date)
    with connect() as conn:
        return conn.execute(
            """
            select coalesce(d.destination_code, 'NONE') as destination_code,
                   coalesce(rd.display_name, 'No destination') as display_name,
                   count(*)::int as count
            from decisions d
            left join routing_destinations rd on rd.destination_code = d.destination_code
            where d.created_at >= %s and d.created_at < %s
            group by 1, 2
            order by count(*) desc, display_name
            limit 10
            """,
            (start, end_exclusive),
        ).fetchall()


def monitor_escalate_emails(limit: int = 25) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            select rq.escalate_id::text, rq.email_id::text, rq.status, rq.priority, rq.reason,
                   rq.assigned_to, rq.created_at, e.subject, e.sender_email, e.office_web_link,
                   e.metadata ->> 'sender_name' as sender_name,
                   d.outcome::text as outcome, d.confidence
            from escalate_queue rq
            join emails e on e.email_id = rq.email_id
            left join decisions d on d.decision_id = rq.decision_id
            order by
              case rq.priority when 'high' then 0 when 'normal' then 1 else 2 end,
              rq.created_at
            limit %s
            """,
            (limit,),
        ).fetchall()
    return json_ready(rows)


def monitor_recent_runs(
    limit: int = 25,
    start_date: str | None = None,
    end_date: str | None = None,
    q: str = "",
) -> list[dict[str, Any]]:
    start, end_exclusive, _start_day, _end_day = parse_date_window(start_date, end_date)
    params: list[Any] = [start, end_exclusive, *_email_search_params(q), limit]
    with connect() as conn:
        rows = conn.execute(
            f"""
            select ar.run_id::text, ar.email_id::text, ar.status, ar.started_at, ar.completed_at,
                   ar.final_outcome::text as final_outcome, e.subject, e.sender_email, d.reason, d.destination_code
            from audit_runs ar
            left join emails e on e.email_id = ar.email_id
            left join lateral (
              select reason, destination_code, matched_rule_code, extracted_fields
              from decisions d2
              where d2.run_id = ar.run_id and d2.document_item_id is null
              order by d2.created_at desc
              limit 1
            ) d on true
            left join lateral (
              {_latest_extraction_lateral_sql()}
            ) x on true
            left join lateral (
              select *
              from invoices i2
              where i2.email_id = e.email_id
              order by i2.created_at desc
              limit 1
            ) i on true
            where ar.started_at >= %s and ar.started_at < %s
              and ({_email_search_predicate()})
            order by ar.started_at desc, e.received_at desc nulls last, e.created_at desc nulls last
            limit %s
            """,
            params,
        ).fetchall()
    return json_ready(rows)


def email_search(
    q: str = "",
    outcome: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    params: list[Any] = _email_search_params(q)
    outcome_filter = ""
    if outcome:
        outcome_filter = "and d.outcome = %s"
        params.append(outcome)
    params.append(limit)
    with connect() as conn:
        rows = conn.execute(
            f"""
            select e.email_id::text, e.subject, e.sender_email, e.received_at,
                   d.outcome::text as outcome, d.reason, d.destination_code,
                   d.confidence, d.created_at as decision_at,
                   i.vendor_name,
                   i.invoice_number,
                   i.metadata ->> 'property_code' as property_code,
                   i.metadata ->> 'property_name' as property_name
            from emails e
            left join lateral (
              select * from decisions d2 where d2.email_id = e.email_id order by d2.created_at desc limit 1
            ) d on true
            left join lateral (
              {_latest_extraction_lateral_sql()}
            ) x on true
            left join lateral (
              select *
              from invoices i2
              where i2.email_id = e.email_id
              order by i2.created_at desc
              limit 1
            ) i on true
            where ({_email_search_predicate()})
            {outcome_filter}
            order by e.received_at desc nulls last, e.created_at desc nulls last
            limit %s
            """,
            params,
        ).fetchall()
    return json_ready(rows)


def email_detail(email_id: str) -> dict[str, Any]:
    with connect() as conn:
        email = conn.execute("select *, email_id::text as email_id from emails where email_id = %s", (email_id,)).fetchone()
        if not email:
            raise DashboardError(404, "Email not found.")
        attachments = conn.execute(
            "select *, attachment_id::text as attachment_id from attachments where email_id = %s order by file_name",
            (email_id,),
        ).fetchall()
        extractions = conn.execute(
            "select *, extraction_id::text as extraction_id from extractions where email_id = %s order by created_at desc",
            (email_id,),
        ).fetchall()
        decisions = conn.execute(
            "select *, decision_id::text as decision_id, run_id::text as run_id from decisions where email_id = %s order by created_at desc",
            (email_id,),
        ).fetchall()
        actions = conn.execute(
            "select *, action_id::text as action_id, decision_id::text as decision_id from actions where email_id = %s order by created_at desc",
            (email_id,),
        ).fetchall()
        escalations = conn.execute(
            "select *, escalate_id::text as escalate_id, decision_id::text as decision_id from escalate_queue where email_id = %s order by created_at desc",
            (email_id,),
        ).fetchall()
        runs = conn.execute(
            "select *, run_id::text as run_id, email_id::text as email_id from audit_runs where email_id = %s order by started_at desc",
            (email_id,),
        ).fetchall()
    return json_ready(
        {
            "email": email,
            "attachments": attachments,
            "extractions": extractions,
            "decisions": decisions,
            "actions": actions,
            "escalate_queue": escalations,
            "audit_runs": runs,
            "html_available": bool(email.get("html_storage_path")),
        }
    )


def attachment_download(attachment_id: str) -> DashboardResponse:
    with connect() as conn:
        row = conn.execute(
            "select file_name, content_type, storage_path from attachments where attachment_id = %s",
            (attachment_id,),
        ).fetchone()
    if not row:
        raise DashboardError(404, "Attachment not found.")
    filename = row["file_name"] or Path(str(row["storage_path"])).name
    body = read_artifact_bytes(row["storage_path"])
    return DashboardResponse(
        body,
        row["content_type"] or "application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


def email_html(email_id: str) -> DashboardResponse:
    with connect() as conn:
        row = conn.execute("select html_storage_path from emails where email_id = %s", (email_id,)).fetchone()
    if not row:
        raise DashboardError(404, "Email not found.")
    html = read_artifact_text(row["html_storage_path"])
    return DashboardResponse(_rewrite_cid_urls(html, email_id), "text/html")


def email_inline_attachment(email_id: str, cid_token: str) -> DashboardResponse:
    artifact_path, content_type = _resolve_inline_attachment_path(email_id, cid_token)
    return DashboardResponse(read_artifact_bytes(str(artifact_path)), content_type)


def audit_run(run_id: str) -> dict[str, Any]:
    with connect() as conn:
        run = conn.execute("select *, run_id::text as run_id, email_id::text as email_id from audit_runs where run_id = %s", (run_id,)).fetchone()
        if not run:
            raise DashboardError(404, "Audit run not found.")
        steps = conn.execute(
            "select *, step_id::text as step_id, run_id::text as run_id from audit_steps where run_id = %s order by sequence_number",
            (run_id,),
        ).fetchall()
    return json_ready({"run": run, "steps": steps})


def audit_steps(run_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "select *, step_id::text as step_id, run_id::text as run_id from audit_steps where run_id = %s order by sequence_number",
            (run_id,),
        ).fetchall()
    return json_ready(rows)


def artifact(path: str) -> DashboardResponse:
    return DashboardResponse(read_artifact_bytes(path), "application/octet-stream")


def workflow_rules() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            select wr.*, wr.outcome::text as outcome,
                   coalesce(jsonb_object_agg(wrc.condition_key, wrc.condition_value)
                     filter (where wrc.condition_key is not null), '{}'::jsonb) as conditions
            from workflow_rules wr
            left join workflow_rule_conditions wrc on wrc.rule_code = wr.rule_code
            group by wr.rule_code
            order by wr.priority, wr.rule_code
            """
        ).fetchall()
    return json_ready(rows)


def workflow_destinations() -> list[dict[str, Any]]:
    with connect() as conn:
        return json_ready(conn.execute("select * from routing_destinations order by destination_code").fetchall())


def workflow_ownership() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            select
              o.ownership,
              o.destination,
              o.created_at
            from ownership o
            order by o.ownership
            """
        ).fetchall()
    return json_ready(rows)


def _ownership_payload(payload: dict[str, Any]) -> dict[str, str]:
    ownership = str(payload.get("ownership") or "").strip()
    destination = str(payload.get("destination") or "").strip()
    if not ownership:
        raise DashboardError(400, "ownership is required.")
    if not destination:
        raise DashboardError(400, "destination is required.")
    return {"ownership": ownership, "destination": destination}


def create_workflow_ownership(payload: dict[str, Any]) -> dict[str, Any]:
    record = _ownership_payload(payload)
    with connect() as conn:
        with conn.transaction():
            destination = conn.execute(
                "select destination_code from routing_destinations where destination_code = %s",
                (record["destination"],),
            ).fetchone()
            if not destination:
                raise DashboardError(400, "Destination does not exist.")
            existing = conn.execute(
                "select ownership from ownership where ownership = %s",
                (record["ownership"],),
            ).fetchone()
            if existing:
                raise DashboardError(400, "Ownership already exists.")
            created = conn.execute(
                """
                insert into ownership (ownership, destination)
                values (%(ownership)s, %(destination)s)
                returning ownership, destination, created_at
                """,
                record,
            ).fetchone()
            conn.execute(
                """
                insert into management_audit_events (changed_table, changed_key, change_type, old_value, new_value, reason, request_metadata)
                values ('ownership', %s, 'insert', null, %s, %s, %s)
                """,
                (
                    created["ownership"],
                    json.dumps(json_ready(created)),
                    payload.get("change_reason"),
                    json.dumps({"source": "local_dashboard"}),
                ),
            )
    return json_ready(created)


def update_workflow_ownership(ownership_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    record = _ownership_payload({**payload, "ownership": ownership_name})
    with connect() as conn:
        with conn.transaction():
            destination = conn.execute(
                "select destination_code from routing_destinations where destination_code = %s",
                (record["destination"],),
            ).fetchone()
            if not destination:
                raise DashboardError(400, "Destination does not exist.")
            old = conn.execute(
                """
                select ownership, destination, created_at
                from ownership
                where ownership = %s
                for update
                """,
                (record["ownership"],),
            ).fetchone()
            if not old:
                raise DashboardError(404, "Ownership not found.")
            updated = conn.execute(
                """
                update ownership
                set destination = %(destination)s
                where ownership = %(ownership)s
                returning ownership, destination, created_at
                """,
                record,
            ).fetchone()
            conn.execute(
                """
                insert into management_audit_events (changed_table, changed_key, change_type, old_value, new_value, reason, request_metadata)
                values ('ownership', %s, 'update', %s, %s, %s, %s)
                """,
                (
                    record["ownership"],
                    json.dumps(json_ready(old)),
                    json.dumps(json_ready(updated)),
                    payload.get("change_reason"),
                    json.dumps({"source": "local_dashboard"}),
                ),
            )
    return json_ready(updated)


def workflow_asset_lookup() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            select
              asset_source as "Asset Source",
              asset_lookup_id as "Lookup ID",
              asset_name as "Asset Name",
              asset_alias as "Asset Alias",
              address as "Address",
              ownership as "Ownership",
              destination_code as "Destination Code",
              destination_active as "Destination Active",
              asset_type as "Asset Type",
              market_name as "Market",
              market_area as "Market Area",
              comment as "Comment"
            from vw_asset_lookup
            order by asset_source, asset_alias nulls last, asset_name, asset_lookup_id
            """
        ).fetchall()
    return json_ready(rows)


def workflow_asset_custom() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            select
              id::text as asset_custom_id,
              asset_name,
              asset_alias,
              address,
              comment,
              destination_code,
              created_at
            from asset_custom
            order by asset_name, asset_alias nulls last, id
            """
        ).fetchall()
    return json_ready(rows)


def _nullable_text(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    return str(value).strip() or None


def _asset_custom_payload(payload: dict[str, Any]) -> dict[str, Any]:
    asset_name = _nullable_text(payload, "asset_name")
    destination_code = _nullable_text(payload, "destination_code")
    if not asset_name:
        raise DashboardError(400, "asset_name is required.")
    if not destination_code:
        raise DashboardError(400, "destination_code is required.")
    return {
        "asset_name": asset_name,
        "asset_alias": _nullable_text(payload, "asset_alias"),
        "address": _nullable_text(payload, "address"),
        "comment": _nullable_text(payload, "comment"),
        "destination_code": destination_code,
    }


def _validate_destination(conn: Any, destination_code: str) -> None:
    destination = conn.execute(
        "select destination_code from routing_destinations where destination_code = %s",
        (destination_code,),
    ).fetchone()
    if not destination:
        raise DashboardError(400, "Destination does not exist.")


def create_workflow_asset_custom(payload: dict[str, Any]) -> dict[str, Any]:
    record = _asset_custom_payload(payload)
    with connect() as conn:
        with conn.transaction():
            _validate_destination(conn, record["destination_code"])
            created = conn.execute(
                """
                insert into asset_custom (
                  asset_name, asset_alias, address, comment, destination_code
                )
                values (
                  %(asset_name)s, %(asset_alias)s, %(address)s, %(comment)s, %(destination_code)s
                )
                returning
                  id::text as asset_custom_id,
                  asset_name, asset_alias, address, comment, destination_code, created_at
                """,
                record,
            ).fetchone()
            conn.execute(
                """
                insert into management_audit_events (changed_table, changed_key, change_type, old_value, new_value, reason, request_metadata)
                values ('asset_custom', %s, 'insert', null, %s, %s, %s)
                """,
                (
                    created["asset_custom_id"],
                    json.dumps(json_ready(created)),
                    payload.get("change_reason"),
                    json.dumps({"source": "local_dashboard"}),
                ),
            )
    return json_ready(created)


def update_workflow_asset_custom(asset_custom_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    record = _asset_custom_payload(payload)
    with connect() as conn:
        with conn.transaction():
            _validate_destination(conn, record["destination_code"])
            old = conn.execute(
                """
                select
                  id::text as asset_custom_id,
                  asset_name, asset_alias, address, comment, destination_code, created_at
                from asset_custom
                where id = %s::bigint
                for update
                """,
                (asset_custom_id,),
            ).fetchone()
            if not old:
                raise DashboardError(404, "Asset custom row not found.")
            updated = conn.execute(
                """
                update asset_custom
                set asset_name = %(asset_name)s,
                    asset_alias = %(asset_alias)s,
                    address = %(address)s,
                    comment = %(comment)s,
                    destination_code = %(destination_code)s
                where id = %(asset_custom_id)s::bigint
                returning
                  id::text as asset_custom_id,
                  asset_name, asset_alias, address, comment, destination_code, created_at
                """,
                {**record, "asset_custom_id": asset_custom_id},
            ).fetchone()
            conn.execute(
                """
                insert into management_audit_events (changed_table, changed_key, change_type, old_value, new_value, reason, request_metadata)
                values ('asset_custom', %s, 'update', %s, %s, %s, %s)
                """,
                (
                    asset_custom_id,
                    json.dumps(json_ready(old)),
                    json.dumps(json_ready(updated)),
                    payload.get("change_reason"),
                    json.dumps({"source": "local_dashboard"}),
                ),
            )
    return json_ready(updated)


def delete_workflow_asset_custom(asset_custom_id: str) -> dict[str, Any]:
    with connect() as conn:
        with conn.transaction():
            old = conn.execute(
                """
                select
                  id::text as asset_custom_id,
                  asset_name, asset_alias, address, comment, destination_code, created_at
                from asset_custom
                where id = %s::bigint
                for update
                """,
                (asset_custom_id,),
            ).fetchone()
            if not old:
                raise DashboardError(404, "Asset custom row not found.")
            conn.execute("delete from asset_custom where id = %s::bigint", (asset_custom_id,))
            conn.execute(
                """
                insert into management_audit_events (changed_table, changed_key, change_type, old_value, new_value, reason, request_metadata)
                values ('asset_custom', %s, 'delete', %s, %s, %s, %s)
                """,
                (
                    asset_custom_id,
                    json.dumps(json_ready(old)),
                    json.dumps(json_ready({**old, "deleted": True})),
                    None,
                    json.dumps({"source": "local_dashboard"}),
                ),
            )
    return {"status": "deleted", "asset_custom_id": asset_custom_id}


def workflow_runtime_config() -> list[dict[str, Any]]:
    with connect() as conn:
        return json_ready(conn.execute("select * from runtime_config order by config_key").fetchall())


def management_audit_events(limit: int = 50) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "select * from management_audit_events order by changed_at desc limit %s",
            (limit,),
        ).fetchall()
    return json_ready(rows)


def workflow_assets() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            select
              a.id::text as id,
              a.asset_name,
              a.ownership,
              a.asset_type,
              a.asset_alias,
              a.market_name,
              a.market_area,
              a.tenants,
              a.address,
              a.created_at,
              o.destination
            from asset a
            left join ownership o on o.ownership = a.ownership
            order by a.asset_alias nulls last, a.asset_name, a.id
            """
        ).fetchall()
    return json_ready(rows)


def _asset_payload(payload: dict[str, Any]) -> dict[str, Any]:
    def text_value(key: str) -> str | None:
        value = payload.get(key)
        if value is None:
            return None
        return str(value).strip() or None

    asset_name = text_value("asset_name")
    if not asset_name:
        raise DashboardError(400, "asset_name is required.")
    return {
        "asset_name": asset_name,
        "ownership": text_value("ownership"),
        "asset_type": text_value("asset_type"),
        "asset_alias": text_value("asset_alias"),
        "market_name": text_value("market_name"),
        "market_area": text_value("market_area"),
        "tenants": text_value("tenants"),
        "address": text_value("address"),
    }


def create_workflow_asset(payload: dict[str, Any]) -> dict[str, Any]:
    record = _asset_payload(payload)
    with connect() as conn:
        with conn.transaction():
            created = conn.execute(
                """
                insert into asset (
                  asset_name, ownership, asset_type, asset_alias,
                  market_name, market_area, tenants, address
                )
                values (
                  %(asset_name)s, %(ownership)s, %(asset_type)s, %(asset_alias)s,
                  %(market_name)s, %(market_area)s, %(tenants)s, %(address)s
                )
                returning
                  id::text as id,
                  asset_name,
                  ownership,
                  asset_type,
                  asset_alias,
                  market_name,
                  market_area,
                  tenants,
                  address,
                  created_at
                """,
                record,
            ).fetchone()
            conn.execute(
                """
                insert into management_audit_events (changed_table, changed_key, change_type, old_value, new_value, reason, request_metadata)
                values ('asset', %s, 'insert', null, %s, %s, %s)
                """,
                (
                    created["id"],
                    json.dumps(json_ready(created)),
                    payload.get("change_reason"),
                    json.dumps({"source": "local_dashboard"}),
                ),
            )
    return json_ready(created)


def update_workflow_asset(asset_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    record = _asset_payload(payload)
    with connect() as conn:
        with conn.transaction():
            old = conn.execute(
                """
                select
                  id::text as id,
                  asset_name,
                  ownership,
                  asset_type,
                  asset_alias,
                  market_name,
                  market_area,
                  tenants,
                  address,
                  created_at
                from asset
                where id = %s
                for update
                """,
                (asset_id,),
            ).fetchone()
            if not old:
                raise DashboardError(404, "Asset not found.")
            updated = conn.execute(
                """
                update asset
                set asset_name = %(asset_name)s,
                    ownership = %(ownership)s,
                    asset_type = %(asset_type)s,
                    asset_alias = %(asset_alias)s,
                    market_name = %(market_name)s,
                    market_area = %(market_area)s,
                    tenants = %(tenants)s,
                    address = %(address)s
                where id = %(asset_id)s
                returning
                  id::text as id,
                  asset_name,
                  ownership,
                  asset_type,
                  asset_alias,
                  market_name,
                  market_area,
                  tenants,
                  address,
                  created_at
                """,
                {**record, "asset_id": asset_id},
            ).fetchone()
            conn.execute(
                """
                insert into management_audit_events (changed_table, changed_key, change_type, old_value, new_value, reason, request_metadata)
                values ('asset', %s, 'update', %s, %s, %s, %s)
                """,
                (
                    asset_id,
                    json.dumps(json_ready(old)),
                    json.dumps(json_ready(updated)),
                    payload.get("change_reason"),
                    json.dumps({"source": "local_dashboard"}),
                ),
            )
    return json_ready(updated)


def deactivate_workflow_asset(asset_id: str) -> dict[str, Any]:
    with connect() as conn:
        with conn.transaction():
            old = conn.execute(
                """
                select
                  id::text as id,
                  asset_name,
                  ownership,
                  asset_type,
                  asset_alias,
                  market_name,
                  market_area,
                  tenants,
                  address,
                  created_at
                from asset
                where id = %s
                for update
                """,
                (asset_id,),
            ).fetchone()
            if not old:
                raise DashboardError(404, "Asset not found.")
            conn.execute("delete from asset where id = %s", (asset_id,))
            conn.execute(
                """
                insert into management_audit_events (changed_table, changed_key, change_type, old_value, new_value, reason, request_metadata)
                values ('asset', %s, 'deactivate', %s, %s, %s, %s)
                """,
                (
                    asset_id,
                    json.dumps(json_ready(old)),
                    json.dumps(json_ready({**old, "deleted": True})),
                    None,
                    json.dumps({"source": "local_dashboard"}),
                ),
            )
    return {"status": "deactivated", "asset_id": asset_id}


def update_workflow_rule(rule_code: str, payload: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "rule_name",
        "priority",
        "enabled",
        "condition_type",
        "outcome",
        "destination_code",
        "reason_template",
        "effective_start",
        "effective_end",
    }
    updates = {key: value for key, value in payload.items() if key in allowed}
    if not updates:
        raise DashboardError(400, "No editable workflow rule fields were provided.")

    reason = payload.get("change_reason")
    with connect() as conn:
        with conn.transaction():
            old = conn.execute("select * from workflow_rules where rule_code = %s for update", (rule_code,)).fetchone()
            if not old:
                raise DashboardError(404, "Workflow rule not found.")
            if "outcome" in updates and updates["outcome"] not in {"AUTO", "ESCALATE", "FILE", "FLAG", "DISCARD"}:
                raise DashboardError(400, "Invalid decision outcome.")
            if "destination_code" in updates and updates["destination_code"]:
                destination = conn.execute(
                    "select active from routing_destinations where destination_code = %s",
                    (updates["destination_code"],),
                ).fetchone()
                if not destination:
                    raise DashboardError(400, "Destination does not exist.")
            new_version = int(old["version"]) + 1
            assignments = [f"{key} = %s" for key in updates]
            values = list(updates.values())
            values.extend([new_version, rule_code])
            conn.execute(
                f"update workflow_rules set {', '.join(assignments)}, version = %s, updated_at = now() where rule_code = %s",
                values,
            )
            new = conn.execute("select * from workflow_rules where rule_code = %s", (rule_code,)).fetchone()
            event_id = conn.execute(
                """
                insert into management_audit_events (
                  changed_table, changed_key, change_type, old_value, new_value, reason, request_metadata
                )
                values ('workflow_rules', %s, 'update', %s, %s, %s, %s)
                returning management_audit_event_id
                """,
                (rule_code, json.dumps(json_ready(old)), json.dumps(json_ready(new)), reason, json.dumps({"source": "local_dashboard"})),
            ).fetchone()["management_audit_event_id"]
            conditions = conn.execute(
                "select condition_key, condition_value from workflow_rule_conditions where rule_code = %s",
                (rule_code,),
            ).fetchall()
            condition_snapshot = {row["condition_key"]: row["condition_value"] for row in conditions}
            conn.execute(
                """
                insert into workflow_rule_versions (
                  rule_code, version, rule_name, priority, enabled, condition_type, condition_snapshot,
                  outcome, destination_code, reason_template, effective_start, effective_end, management_audit_event_id
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    rule_code,
                    new["version"],
                    new["rule_name"],
                    new["priority"],
                    new["enabled"],
                    new["condition_type"],
                    json.dumps(condition_snapshot),
                    new["outcome"],
                    new["destination_code"],
                    new["reason_template"],
                    new["effective_start"],
                    new["effective_end"],
                    event_id,
                ),
            )
    return {"status": "updated", "rule_code": rule_code, "version": new_version}


def update_runtime_config(config_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    if "config_value" not in payload:
        raise DashboardError(400, "config_value is required.")
    with connect() as conn:
        with conn.transaction():
            old = conn.execute("select * from runtime_config where config_key = %s for update", (config_key,)).fetchone()
            if not old:
                raise DashboardError(404, "Runtime config not found.")
            conn.execute(
                "update runtime_config set config_value = %s, updated_at = now() where config_key = %s",
                (json.dumps(payload["config_value"]), config_key),
            )
            new = conn.execute("select * from runtime_config where config_key = %s", (config_key,)).fetchone()
            conn.execute(
                """
                insert into management_audit_events (changed_table, changed_key, change_type, old_value, new_value, reason, request_metadata)
                values ('runtime_config', %s, 'update', %s, %s, %s, %s)
                """,
                (
                    config_key,
                    json.dumps(json_ready(old)),
                    json.dumps(json_ready(new)),
                    payload.get("change_reason"),
                    json.dumps({"source": "local_dashboard"}),
                ),
            )
    return {"status": "updated", "config_key": config_key}
