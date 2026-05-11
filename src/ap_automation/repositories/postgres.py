from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from ap_automation.models.decision import Decision, Destination, PropertyMatch, WorkflowRule
from ap_automation.models.extraction import ExtractionPayload


class PostgresRepository:
    """Postgres-backed policy and operational repository.

    This class keeps SQL at the repository boundary. Decision code receives
    typed policy rows and does not know table details.
    """

    def __init__(self, dsn: str) -> None:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("Install the postgres extra to use PostgresRepository: pip install -e .[postgres]") from exc

        self._psycopg = psycopg
        self._dict_row = dict_row
        self._dsn = dsn

    def get_runtime_config(self) -> dict[str, Any]:
        with self._connect() as conn:
            rows = conn.execute("select config_key, config_value from runtime_config").fetchall()
        return {row["config_key"]: row["config_value"] for row in rows}

    def get_active_workflow_rules(self) -> list[WorkflowRule]:
        sql = """
            select
              wr.rule_code,
              wr.rule_name,
              wr.priority,
              wr.condition_type,
              wr.outcome::text as outcome,
              wr.destination_code,
              wr.reason_template,
              wr.version,
              coalesce(jsonb_object_agg(wrc.condition_key, wrc.condition_value)
                filter (where wrc.condition_key is not null), '{}'::jsonb) as conditions
            from workflow_rules wr
            left join workflow_rule_conditions wrc on wrc.rule_code = wr.rule_code
            where wr.enabled = true
              and wr.effective_start <= current_date
              and (wr.effective_end is null or wr.effective_end >= current_date)
            group by wr.rule_code
            order by wr.priority
        """
        with self._connect() as conn:
            rows = conn.execute(sql).fetchall()
        return [
            WorkflowRule(
                rule_code=row["rule_code"],
                rule_name=row["rule_name"],
                priority=row["priority"],
                condition_type=row["condition_type"],
                outcome=row["outcome"],
                destination_code=row["destination_code"],
                reason_template=row["reason_template"],
                version=row["version"],
                conditions=row["conditions"],
            )
            for row in rows
        ]

    def get_destination(self, destination_code: str) -> Destination:
        sql = """
            select destination_code, destination_type, display_name, email_address,
                   folder_path, subject_instruction, active
            from routing_destinations
            where destination_code = %s
        """
        with self._connect() as conn:
            row = conn.execute(sql, (destination_code,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown routing destination: {destination_code}")
        return Destination(**row)

    def match_property(self, extraction: ExtractionPayload) -> PropertyMatch | None:
        candidates = [
            extraction.invoice.property_code,
            extraction.invoice.property_name,
            extraction.invoice.service_address,
            extraction.invoice.bill_to,
            *extraction.business_signals.possible_property_aliases,
        ]
        values = [value.strip() for value in candidates if value and value.strip()]
        if not values:
            return None

        sql = """
            select
              p.property_code,
              p.property_name,
              p.ownership_type,
              p.management_type,
              p.business_unit_code,
              p.default_destination_code,
              pr.destination_code as route_destination_code,
              p.is_sold,
              pa.alias_value as matched_alias
            from properties p
            left join property_aliases pa on pa.property_id = p.property_id
            left join property_routes pr on pr.property_id = p.property_id and pr.active = true
            where p.active = true
              and (
                lower(p.property_code) = any(%s)
                or lower(coalesce(p.property_name, '')) = any(%s)
                or lower(coalesce(pa.alias_value, '')) = any(%s)
              )
            order by case when lower(p.property_code) = any(%s) then 0 else 1 end, p.property_code
            limit 1
        """
        lowered = [value.lower() for value in values]
        with self._connect() as conn:
            row = conn.execute(sql, (lowered, lowered, lowered, lowered)).fetchone()
        return PropertyMatch(**row) if row else None

    def find_duplicate_status(self, extraction: ExtractionPayload, idempotency_key: str) -> str | None:
        invoice_number = extraction.invoice.invoice_number
        vendor_name = extraction.invoice.vendor_name
        if not invoice_number or not vendor_name:
            return None
        sql = """
            select 1
            from extractions e
            join emails em on em.email_id = e.email_id
            where em.idempotency_key <> %s
              and lower(e.parsed_output #>> '{invoice,invoice_number}') = lower(%s)
              and lower(e.parsed_output #>> '{invoice,vendor_name}') = lower(%s)
            limit 1
        """
        with self._connect() as conn:
            row = conn.execute(sql, (idempotency_key, invoice_number, vendor_name)).fetchone()
        return "candidate" if row else None

    def upsert_email(self, metadata: dict[str, Any]) -> str:
        sql = """
            insert into emails (
              source_system, source_message_id, idempotency_key, subject,
              sender_email, received_at, raw_storage_path, metadata
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (idempotency_key) do update
            set subject = excluded.subject,
                sender_email = excluded.sender_email,
                received_at = excluded.received_at,
                raw_storage_path = excluded.raw_storage_path,
                metadata = excluded.metadata
            returning email_id::text
        """
        params = (
            metadata["source_system"],
            metadata["source_message_id"],
            metadata["idempotency_key"],
            metadata.get("subject"),
            metadata.get("sender_email"),
            metadata.get("received_at"),
            metadata.get("raw_storage_path"),
            json.dumps(metadata.get("metadata", {})),
        )
        with self._connect() as conn:
            return conn.execute(sql, params).fetchone()["email_id"]

    def create_audit_run(self, email_id: str, metadata: dict[str, Any]) -> str:
        with self._connect() as conn:
            return conn.execute(
                "insert into audit_runs (email_id, status, metadata) values (%s, 'started', %s) returning run_id::text",
                (email_id, json.dumps(metadata)),
            ).fetchone()["run_id"]

    def add_audit_step(
        self,
        run_id: str,
        step_type: str,
        input_summary: dict[str, Any],
        output_summary: dict[str, Any],
        reason: str | None = None,
        confidence: float | None = None,
        decision: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        sql = """
            insert into audit_steps (
              run_id, sequence_number, step_type, input_summary, output_summary,
              decision, reason, confidence, error
            )
            values (
              %s,
              coalesce((select max(sequence_number) + 1 from audit_steps where run_id = %s), 1),
              %s, %s, %s, %s, %s, %s, %s
            )
        """
        with self._connect() as conn:
            conn.execute(
                sql,
                (
                    run_id,
                    run_id,
                    step_type,
                    json.dumps(input_summary),
                    json.dumps(output_summary),
                    json.dumps(decision) if decision is not None else None,
                    reason,
                    confidence,
                    error,
                ),
            )

    def save_extraction(
        self,
        email_id: str,
        extraction: ExtractionPayload | None,
        parsed_payload: dict[str, Any],
        validation_errors: list[str],
        raw_output: dict[str, Any] | None = None,
    ) -> None:
        if extraction is None:
            extractor_raw = parsed_payload.get("extractor", {}) if isinstance(parsed_payload.get("extractor"), dict) else {}
            extractor_type = str(extractor_raw.get("type") or (raw_output or {}).get("extractor_type") or "unknown")
            confidence = None
            validation_status = "invalid"
            parsed_output = parsed_payload
            model_name = (raw_output or {}).get("model") if isinstance((raw_output or {}).get("model"), str) else None
            prompt_version = (
                extractor_raw.get("prompt_version")
                if isinstance(extractor_raw.get("prompt_version"), str)
                else (raw_output or {}).get("prompt_version") if isinstance((raw_output or {}).get("prompt_version"), str) else None
            )
        else:
            extractor_type = extraction.extractor.type
            confidence = extraction.confidence.overall
            validation_status = "valid"
            parsed_output = extraction.raw
            model_name = extraction.extractor.model
            prompt_version = extraction.extractor.prompt_version

        raw_extractor_output = raw_output if raw_output is not None else parsed_payload

        with self._connect() as conn:
            conn.execute(
                """
                insert into extractions (
                  email_id, extractor_type, model_name, prompt_version, raw_output,
                  parsed_output, confidence, validation_status, validation_errors
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    email_id,
                    extractor_type,
                    model_name,
                    prompt_version,
                    json.dumps(raw_extractor_output),
                    json.dumps(parsed_output),
                    confidence,
                    validation_status,
                    json.dumps(validation_errors),
                ),
            )

    def save_attachments(self, email_id: str, attachments: list[dict[str, Any]]) -> None:
        sql = """
            insert into attachments (
              email_id, file_name, content_type, storage_path,
              file_size_bytes, sha256, metadata
            )
            values (%s, %s, %s, %s, %s, %s, %s)
            on conflict (email_id, storage_path, sha256) do update
            set file_name = excluded.file_name,
                content_type = excluded.content_type,
                file_size_bytes = excluded.file_size_bytes,
                metadata = excluded.metadata
        """
        with self._connect() as conn:
            for attachment in attachments:
                conn.execute(
                    sql,
                    (
                        email_id,
                        attachment["file_name"],
                        attachment.get("content_type"),
                        attachment["storage_path"],
                        attachment.get("file_size_bytes"),
                        attachment.get("sha256"),
                        json.dumps(attachment.get("metadata", {})),
                    ),
                )

    def save_decision(self, email_id: str, run_id: str, decision: Decision) -> str:
        with self._connect() as conn:
            return conn.execute(
                """
                insert into decisions (
                  email_id, run_id, outcome, destination_code, destination_email,
                  reason, confidence, matched_rule_code, matched_rule_version,
                  extracted_fields, routing_match, dry_run
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                returning decision_id::text
                """,
                (
                    email_id,
                    run_id,
                    decision.outcome,
                    decision.destination_code,
                    decision.destination_email,
                    decision.reason,
                    decision.confidence,
                    decision.matched_rule_code,
                    decision.matched_rule_version,
                    json.dumps(decision.extracted_fields),
                    json.dumps(decision.routing_match),
                    decision.dry_run,
                ),
            ).fetchone()["decision_id"]

    def save_action(self, email_id: str, decision_id: str, decision: Decision, manifest_path: str) -> None:
        action_type = {
            "AUTO": "forward_email",
            "REVIEW": "queue_review",
            "FLAG": "queue_review",
            "FILE": "file_email",
            "DISCARD": "no_action",
        }[decision.outcome]
        with self._connect() as conn:
            conn.execute(
                """
                insert into actions (
                  email_id, decision_id, action_type, destination_code, dry_run,
                  status, external_reference, reason, completed_at
                )
                values (%s, %s, %s, %s, %s, 'skipped_dry_run', %s, %s, now())
                """,
                (
                    email_id,
                    decision_id,
                    action_type,
                    decision.destination_code,
                    decision.dry_run,
                    manifest_path,
                    decision.reason,
                ),
            )

    def enqueue_review(self, email_id: str, decision_id: str, reason: str, priority: str = "normal") -> None:
        with self._connect() as conn:
            conn.execute(
                "insert into review_queue (email_id, decision_id, priority, reason) values (%s, %s, %s, %s)",
                (email_id, decision_id, priority, reason),
            )

    def finalize_audit_run(self, run_id: str, final_outcome: str, trace_artifact_path: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                update audit_runs
                set status = 'completed',
                    completed_at = now(),
                    final_outcome = %s,
                    trace_artifact_path = %s
                where run_id = %s
                """,
                (final_outcome, trace_artifact_path, run_id),
            )

    def fail_audit_run(self, run_id: str, error: str, trace_artifact_path: str | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                update audit_runs
                set status = 'failed',
                    completed_at = now(),
                    trace_artifact_path = coalesce(%s, trace_artifact_path),
                    metadata = metadata || jsonb_build_object('error', %s::text)
                where run_id = %s
                """,
                (trace_artifact_path, error, run_id),
            )

    def _connect(self):
        return self._psycopg.connect(self._dsn, row_factory=self._dict_row)


def new_local_id(prefix: str) -> str:
    return f"{prefix}_{uuid4()}"
