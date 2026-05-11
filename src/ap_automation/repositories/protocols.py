from __future__ import annotations

from typing import Any, Protocol

from ap_automation.models.decision import Decision, Destination, PropertyMatch, WorkflowRule
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

    def match_property(self, extraction: ExtractionPayload) -> PropertyMatch | None:
        ...

    def find_duplicate_status(self, extraction: ExtractionPayload, idempotency_key: str) -> str | None:
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
    ) -> None:
        ...

    def save_attachments(self, email_id: str, attachments: list[dict[str, Any]]) -> None:
        ...

    def save_decision(self, email_id: str, run_id: str, decision: Decision) -> str:
        ...

    def save_action(self, email_id: str, decision_id: str, decision: Decision, manifest_path: str) -> None:
        ...

    def enqueue_review(self, email_id: str, decision_id: str, reason: str, priority: str = "normal") -> None:
        ...

    def finalize_audit_run(self, run_id: str, final_outcome: str, trace_artifact_path: str) -> None:
        ...

    def fail_audit_run(self, run_id: str, error: str, trace_artifact_path: str | None = None) -> None:
        ...
