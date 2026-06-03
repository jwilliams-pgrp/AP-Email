from __future__ import annotations

from typing import Any, Protocol

from ap_automation.models.decision import (
    Decision,
    Destination,
    NoActionEmailPattern,
    PropertyMatch,
    PropertyMatchEvaluation,
    WorkflowRule,
)
from ap_automation.models.extraction import ExtractionPayload


class PolicyRepository(Protocol):
    """Read-only workflow policy boundary.

    Implementations may read from Postgres, test fixtures, or local seed exports.
    Business-maintained values must come through this boundary.
    """

    def get_runtime_config(self) -> dict[str, Any]:
        ...

    def get_active_workflow_rules(self) -> list[WorkflowRule]:
        ...

    def get_destination(self, destination_code: str) -> Destination:
        ...

    def evaluate_property_match(self, extraction: ExtractionPayload) -> PropertyMatchEvaluation:
        ...

    def get_property_match_by_asset_id(self, asset_id: str, matched_alias: str | None = None) -> PropertyMatch | None:
        ...

    def get_asset_reference_rows(self) -> list[dict[str, Any]]:
        ...

    def find_duplicate_status(self, extraction: ExtractionPayload, idempotency_key: str) -> str | None:
        ...

    def get_active_no_action_email_patterns(self) -> list[NoActionEmailPattern]:
        ...


class OperationalRepository(Protocol):
    """Operational persistence boundary for local processing and audit records."""

    def upsert_email(self, metadata: dict[str, Any]) -> str:
        ...

    def create_audit_run(self, email_id: str, metadata: dict[str, Any]) -> str:
        ...

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
        ...

    def save_extraction(
        self,
        email_id: str,
        extraction: ExtractionPayload | None,
        parsed_payload: dict[str, Any],
        validation_errors: list[str],
        raw_output: dict[str, Any] | None = None,
        document_item_id: str | None = None,
    ) -> dict[str, Any]:
        ...

    def save_llm_interaction(
        self,
        email_id: str,
        run_id: str,
        interaction: dict[str, Any],
    ) -> None:
        ...

    def save_invoice_fact(self, email_id: str, extraction: ExtractionPayload, document_item_id: str | None = None) -> None:
        ...

    def save_attachments(self, email_id: str, attachments: list[dict[str, Any]]) -> None:
        ...

    def save_document_item(
        self,
        email_id: str,
        item_kind: str,
        item_key: str,
        display_name: str | None,
        metadata: dict[str, Any],
        attachment_id: str | None = None,
    ) -> str:
        ...

    def update_email_html_storage_path(self, email_id: str, html_storage_path: str) -> None:
        ...

    def update_email_office_web_link(self, email_id: str, office_web_link: str) -> None:
        ...

    def save_decision(self, email_id: str, run_id: str, decision: Decision, document_item_id: str | None = None) -> str:
        ...

    def save_action(self, email_id: str, decision_id: str, decision: Decision, manifest_path: str, document_item_id: str | None = None) -> None:
        ...

    def enqueue_escalate(self, email_id: str, decision_id: str, reason: str, priority: str = "normal", document_item_id: str | None = None) -> None:
        ...

    def reload_escalate_folder_items(self, items: list[dict[str, Any]]) -> None:
        ...

    def finalize_audit_run(self, run_id: str, final_outcome: str, trace_artifact_path: str) -> None:
        ...

    def fail_audit_run(self, run_id: str, error: str, trace_artifact_path: str | None = None) -> None:
        ...
