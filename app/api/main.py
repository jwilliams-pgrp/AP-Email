from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import psycopg
from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from psycopg.rows import dict_row


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCAL_ROOT = PROJECT_ROOT / "local"
DSN = os.getenv("AP_DASHBOARD_DSN", "host=localhost dbname=apautomation user=postgres password=llamas")

app = FastAPI(title="AP Automation Local Dashboard API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def connect():
    return psycopg.connect(DSN, row_factory=dict_row)


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


def require_local_artifact(path_value: str | None) -> Path:
    if not path_value:
        raise HTTPException(status_code=404, detail="Artifact path is missing.")
    path = (PROJECT_ROOT / path_value).resolve()
    try:
        path.relative_to(LOCAL_ROOT.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Artifact path is outside the approved local artifact root.") from exc
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail=f"Artifact does not exist: {path_value}")
    return path


@app.get("/api/health")
def health() -> dict[str, Any]:
    with connect() as conn:
        db_time = conn.execute("select now() as db_time").fetchone()["db_time"]
    return {"status": "ok", "db_time": json_ready(db_time), "mode": "LOCAL"}


@app.get("/api/monitor/summary")
def monitor_summary(days: int = Query(7, ge=1, le=365)) -> dict[str, Any]:
    start = cutoff(days)
    with connect() as conn:
        outcome_rows = conn.execute(
            """
            select d.outcome::text as outcome, count(*)::int as count
            from decisions d
            where d.created_at >= %s
            group by d.outcome
            """,
            (start,),
        ).fetchall()
        run_rows = conn.execute(
            """
            select status, count(*)::int as count
            from audit_runs
            where started_at >= %s
            group by status
            """,
            (start,),
        ).fetchall()
        open_review = conn.execute(
            "select count(*)::int as count from review_queue where status in ('open', 'in_progress')"
        ).fetchone()["count"]
        avg_seconds = conn.execute(
            """
            select avg(extract(epoch from completed_at - started_at)) as seconds
            from audit_runs
            where started_at >= %s and completed_at is not null
            """,
            (start,),
        ).fetchone()["seconds"]
        confidence = conn.execute(
            """
            select
              count(*) filter (where confidence >= 0.95)::int as high,
              count(*) filter (where confidence >= 0.90 and confidence < 0.95)::int as medium,
              count(*) filter (where confidence < 0.90)::int as low
            from decisions
            where created_at >= %s
            """,
            (start,),
        ).fetchone()

    outcomes = {row["outcome"]: row["count"] for row in outcome_rows}
    runs = {row["status"]: row["count"] for row in run_rows}
    total = sum(outcomes.values())

    def rate(name: str) -> float:
        return round((outcomes.get(name, 0) / total) * 100, 1) if total else 0.0

    return json_ready(
        {
            "days": days,
            "total_processed": total,
            "outcomes": outcomes,
            "runs": runs,
            "rates": {
                "AUTO": rate("AUTO"),
                "REVIEW": rate("REVIEW"),
                "FILE": rate("FILE"),
                "FLAG": rate("FLAG"),
                "DISCARD": rate("DISCARD"),
            },
            "failed_run_rate": round((runs.get("failed", 0) / max(sum(runs.values()), 1)) * 100, 1),
            "open_review_count": open_review,
            "avg_processing_seconds": round(float(avg_seconds), 2) if avg_seconds is not None else None,
            "confidence": confidence,
        }
    )


@app.get("/api/monitor/throughput")
def monitor_throughput(days: int = Query(7, ge=1, le=365)) -> list[dict[str, Any]]:
    start = cutoff(days)
    with connect() as conn:
        decision_rows = conn.execute(
            """
            select date_trunc('day', d.created_at)::date as day, d.outcome::text as outcome, count(*)::int as count
            from decisions d
            where d.created_at >= %s and d.outcome in ('AUTO', 'REVIEW', 'FILE')
            group by 1, 2
            order by 1
            """,
            (start,),
        ).fetchall()
        failed_rows = conn.execute(
            """
            select date_trunc('day', started_at)::date as day, count(*)::int as count
            from audit_runs
            where started_at >= %s and status = 'failed'
            group by 1
            order by 1
            """,
            (start,),
        ).fetchall()
    by_day: dict[str, dict[str, Any]] = {}
    outcome_to_category = {"AUTO": "automated", "REVIEW": "review", "FILE": "filed"}
    for row in decision_rows:
        key = row["day"].isoformat()
        by_day.setdefault(key, {"day": key, "automated": 0, "review": 0, "failed": 0, "filed": 0})
        by_day[key][outcome_to_category[row["outcome"]]] = row["count"]
    for row in failed_rows:
        key = row["day"].isoformat()
        by_day.setdefault(key, {"day": key, "automated": 0, "review": 0, "failed": 0, "filed": 0})
        by_day[key]["failed"] = row["count"]
    return [by_day[key] for key in sorted(by_day)]


@app.get("/api/monitor/review-reasons")
def monitor_review_reasons(days: int = Query(7, ge=1, le=365)) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            select coalesce(rq.reason, d.reason) as reason, count(*)::int as count
            from decisions d
            left join review_queue rq on rq.decision_id = d.decision_id
            where d.created_at >= %s and d.outcome in ('REVIEW', 'FLAG')
            group by 1
            order by count(*) desc, reason
            limit 10
            """,
            (cutoff(days),),
        ).fetchall()
    return rows


@app.get("/api/monitor/destinations")
def monitor_destinations(days: int = Query(7, ge=1, le=365)) -> list[dict[str, Any]]:
    with connect() as conn:
        return conn.execute(
            """
            select coalesce(d.destination_code, 'NONE') as destination_code,
                   coalesce(rd.display_name, 'No destination') as display_name,
                   count(*)::int as count
            from decisions d
            left join routing_destinations rd on rd.destination_code = d.destination_code
            where d.created_at >= %s
            group by 1, 2
            order by count(*) desc, display_name
            limit 10
            """,
            (cutoff(days),),
        ).fetchall()


@app.get("/api/monitor/review-emails")
def monitor_review_emails(limit: int = Query(25, ge=1, le=100)) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            select rq.review_id::text, rq.email_id::text, rq.status, rq.priority, rq.reason,
                   rq.assigned_to, rq.created_at, e.subject, e.sender_email,
                   e.metadata ->> 'sender_name' as sender_name,
                   d.outcome::text as outcome, d.confidence
            from review_queue rq
            join emails e on e.email_id = rq.email_id
            left join decisions d on d.decision_id = rq.decision_id
            where rq.status in ('open', 'in_progress')
            order by
              case rq.priority when 'high' then 0 when 'normal' then 1 else 2 end,
              rq.created_at
            limit %s
            """,
            (limit,),
        ).fetchall()
    return json_ready(rows)


@app.get("/api/monitor/recent-runs")
def monitor_recent_runs(limit: int = Query(25, ge=1, le=100)) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            select ar.run_id::text, ar.email_id::text, ar.status, ar.started_at, ar.completed_at,
                   ar.final_outcome::text as final_outcome, e.subject, e.sender_email, d.reason
            from audit_runs ar
            left join emails e on e.email_id = ar.email_id
            left join decisions d on d.run_id = ar.run_id
            order by ar.started_at desc
            limit %s
            """,
            (limit,),
        ).fetchall()
    return json_ready(rows)


@app.get("/api/emails/search")
def email_search(
    q: str = "",
    outcome: str | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> list[dict[str, Any]]:
    pattern = f"%{q.lower()}%"
    params: list[Any] = [pattern, pattern, pattern, pattern, pattern, pattern]
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
                   x.parsed_output #>> '{{invoice,vendor_name}}' as vendor_name,
                   x.parsed_output #>> '{{invoice,invoice_number}}' as invoice_number,
                   x.parsed_output #>> '{{invoice,property_code}}' as property_code,
                   x.parsed_output #>> '{{invoice,property_name}}' as property_name
            from emails e
            left join lateral (
              select * from decisions d2 where d2.email_id = e.email_id order by d2.created_at desc limit 1
            ) d on true
            left join lateral (
              select * from extractions x2 where x2.email_id = e.email_id order by x2.created_at desc limit 1
            ) x on true
            where (
              %s = '%%' or lower(coalesce(e.subject, '')) like %s
              or lower(coalesce(e.sender_email, '')) like %s
              or lower(e.email_id::text) like %s
              or lower(coalesce(d.reason, '')) like %s
              or lower(coalesce(x.parsed_output::text, '')) like %s
            )
            {outcome_filter}
            order by coalesce(d.created_at, e.created_at) desc
            limit %s
            """,
            params,
        ).fetchall()
    return json_ready(rows)


@app.get("/api/emails/{email_id}")
def email_detail(email_id: str) -> dict[str, Any]:
    with connect() as conn:
        email = conn.execute("select *, email_id::text as email_id from emails where email_id = %s", (email_id,)).fetchone()
        if not email:
            raise HTTPException(status_code=404, detail="Email not found.")
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
        reviews = conn.execute(
            "select *, review_id::text as review_id, decision_id::text as decision_id from review_queue where email_id = %s order by created_at desc",
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
            "review_queue": reviews,
            "audit_runs": runs,
            "html_available": bool(email.get("html_storage_path")),
        }
    )


@app.get("/api/attachments/{attachment_id}/download")
def attachment_download(attachment_id: str) -> Response:
    with connect() as conn:
        row = conn.execute(
            "select file_name, content_type, storage_path from attachments where attachment_id = %s",
            (attachment_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Attachment not found.")
    path = require_local_artifact(row["storage_path"])
    filename = row["file_name"] or path.name
    return Response(
        path.read_bytes(),
        media_type=row["content_type"] or "application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@app.get("/api/emails/{email_id}/html")
def email_html(email_id: str) -> Response:
    with connect() as conn:
        row = conn.execute("select html_storage_path from emails where email_id = %s", (email_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Email not found.")
    path = require_local_artifact(row["html_storage_path"])
    return Response(path.read_text(encoding="utf-8"), media_type="text/html")


@app.get("/api/audit-runs/{run_id}")
def audit_run(run_id: str) -> dict[str, Any]:
    with connect() as conn:
        run = conn.execute("select *, run_id::text as run_id, email_id::text as email_id from audit_runs where run_id = %s", (run_id,)).fetchone()
        if not run:
            raise HTTPException(status_code=404, detail="Audit run not found.")
        steps = conn.execute(
            "select *, step_id::text as step_id, run_id::text as run_id from audit_steps where run_id = %s order by sequence_number",
            (run_id,),
        ).fetchall()
    return json_ready({"run": run, "steps": steps})


@app.get("/api/audit-runs/{run_id}/steps")
def audit_steps(run_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "select *, step_id::text as step_id, run_id::text as run_id from audit_steps where run_id = %s order by sequence_number",
            (run_id,),
        ).fetchall()
    return json_ready(rows)


@app.get("/api/artifacts")
def artifact(path: str) -> Response:
    file_path = require_local_artifact(path)
    return Response(file_path.read_bytes(), media_type="application/octet-stream")


@app.patch("/api/review-queue/{review_id}/complete")
def complete_review_queue_item(review_id: str) -> dict[str, Any]:
    with connect() as conn:
        with conn.transaction():
            row = conn.execute(
                "select status from review_queue where review_id = %s for update",
                (review_id,),
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Review item not found.")
            if row["status"] in {"resolved", "closed"}:
                return {"status": row["status"], "review_id": review_id}
            conn.execute(
                "update review_queue set status = 'resolved', resolved_at = now() where review_id = %s",
                (review_id,),
            )
    return {"status": "resolved", "review_id": review_id}


@app.get("/api/workflow/rules")
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


@app.get("/api/workflow/destinations")
def workflow_destinations() -> list[dict[str, Any]]:
    with connect() as conn:
        return json_ready(conn.execute("select * from routing_destinations order by destination_code").fetchall())


@app.get("/api/workflow/runtime-config")
def workflow_runtime_config() -> list[dict[str, Any]]:
    with connect() as conn:
        return json_ready(conn.execute("select * from runtime_config order by config_key").fetchall())


@app.get("/api/workflow/audit-events")
def management_audit_events(limit: int = Query(50, ge=1, le=200)) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "select * from management_audit_events order by changed_at desc limit %s",
            (limit,),
        ).fetchall()
    return json_ready(rows)


@app.patch("/api/workflow/rules/{rule_code}")
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
        raise HTTPException(status_code=400, detail="No editable workflow rule fields were provided.")

    reason = payload.get("change_reason")
    with connect() as conn:
        with conn.transaction():
            old = conn.execute("select * from workflow_rules where rule_code = %s for update", (rule_code,)).fetchone()
            if not old:
                raise HTTPException(status_code=404, detail="Workflow rule not found.")
            if "outcome" in updates and updates["outcome"] not in {"AUTO", "REVIEW", "FILE", "FLAG", "DISCARD"}:
                raise HTTPException(status_code=400, detail="Invalid decision outcome.")
            if "destination_code" in updates and updates["destination_code"]:
                destination = conn.execute(
                    "select active from routing_destinations where destination_code = %s",
                    (updates["destination_code"],),
                ).fetchone()
                if not destination:
                    raise HTTPException(status_code=400, detail="Destination does not exist.")
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


@app.patch("/api/workflow/runtime-config/{config_key}")
def update_runtime_config(config_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    if "config_value" not in payload:
        raise HTTPException(status_code=400, detail="config_value is required.")
    with connect() as conn:
        with conn.transaction():
            old = conn.execute("select * from runtime_config where config_key = %s for update", (config_key,)).fetchone()
            if not old:
                raise HTTPException(status_code=404, detail="Runtime config not found.")
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
