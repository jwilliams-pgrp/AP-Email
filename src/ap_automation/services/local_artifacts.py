from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ap_automation.models.decision import Decision
from ap_automation.services.msg_parser import ParsedAttachment


LOCAL_DIRECTORIES = (
    "local/ingest",
    "local/processed",
    "local/attachments",
    "local/audit/traces",
    "local/audit/prompts",
    "local/audit/extractions",
    "local/outbound/dry-run",
    "local/outbound/review",
    "local/outbound/review-statements",
    "local/outbound/ach",
    "local/outbound/ben-e-keith",
    "local/outbound/lien-release",
)


class LocalArtifactStore:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root

    def ensure_directories(self) -> None:
        for relative_path in LOCAL_DIRECTORIES:
            (self.project_root / relative_path).mkdir(parents=True, exist_ok=True)

    def write_extraction_snapshot(self, run_id: str, payload: dict[str, Any]) -> str:
        relative_path = Path("local/audit/extractions") / f"{run_id}.json"
        self._write_json(relative_path, payload)
        return relative_path.as_posix()

    def write_prompt_snapshot(self, run_id: str, prompt: str) -> str:
        relative_path = Path("local/audit/prompts") / f"{run_id}.txt"
        full_path = self.project_root / relative_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(prompt, encoding="utf-8")
        return relative_path.as_posix()

    def write_attachments(self, email_id: str, attachments: tuple[ParsedAttachment, ...]) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for index, attachment in enumerate(attachments, start=1):
            file_name = _safe_file_name(attachment.file_name, index)
            relative_path = Path("local/attachments") / email_id / file_name
            full_path = self.project_root / relative_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_bytes(attachment.content)
            records.append(
                {
                    "file_name": attachment.file_name,
                    "content_type": attachment.content_type,
                    "storage_path": relative_path.as_posix(),
                    "file_size_bytes": len(attachment.content),
                    "metadata": {**attachment.metadata, "stored_file_name": file_name},
                }
            )
        return records

    def write_dry_run_manifest(self, run_id: str, decision: Decision) -> str:
        relative_path = Path("local/outbound/dry-run") / f"{run_id}.json"
        manifest = {
            "run_id": run_id,
            "dry_run": decision.dry_run,
            "outcome": decision.outcome,
            "destination_code": decision.destination_code,
            "destination_email": decision.destination_email,
            "reason": decision.reason,
            "matched_rule_code": decision.matched_rule_code,
            "matched_rule_version": decision.matched_rule_version,
        }
        self._write_json(relative_path, manifest)
        return relative_path.as_posix()

    def write_trace(self, run_id: str, decision: Decision) -> str:
        relative_path = Path("local/audit/traces") / f"{run_id}.mmd"
        destination = _mermaid_label(decision.destination_code or "No routing destination")
        decision_reason = _mermaid_label(decision.reason)
        lines = [
            "flowchart TD",
            f'  start["Audit Run<br/>{run_id}"] -->|"Processing started"| ingestion["Email Received"]',
            '  ingestion -->|"Email details were recorded for audit review"| attachments["Attachments Saved"]',
            '  attachments -->|"Invoice files were preserved for review and routing"| extraction["Invoice Details Read"]',
            '  extraction -->|"Extractor returned structured invoice information"| validation["Required Fields Checked"]',
            '  validation -->|"Required invoice data passed schema checks"| duplicate["Duplicate History Checked"]',
            '  duplicate -->|"No blocking duplicate stopped processing"| routing["Property Routing Checked"]',
            '  routing -->|"Property and routing signals were matched against setup tables"| rules["Workflow Policy Applied"]',
            f'  rules -->|"{decision_reason}"| decision["Business Decision<br/>{decision.outcome}"]',
            f'  decision -->|"Decision recorded with confidence {decision.confidence:.2f}"| action["Dry-Run Action Logged<br/>{destination}"]',
            '  action -->|"No external system was changed in local dry-run mode"| finalize["Audit Complete"]',
            "  classDef success fill:#d1fae5,stroke:#15803d,color:#064e3b;",
            "  classDef failure fill:#fee2e2,stroke:#b91c1c,color:#7f1d1d;",
            "  class start,ingestion,attachments,extraction,validation,duplicate,routing,rules,decision,action,finalize success;",
        ]
        full_path = self.project_root / relative_path
        full_path.write_text("\n".join(lines), encoding="utf-8")
        return relative_path.as_posix()

    def write_failure_trace(self, run_id: str, failed_step: str, error: str) -> str:
        relative_path = Path("local/audit/traces") / f"{run_id}.mmd"
        safe_error = _mermaid_label(error)
        steps = [
            ("ingestion", "Email Received", "Processing started"),
            ("attachments", "Attachments Saved", "Email details were recorded for audit review"),
            ("extraction", "Invoice Details Read", "Invoice files were sent for extraction"),
            ("validation", "Required Fields Checked", "Extractor output was checked against required fields"),
        ]
        failed_node = _step_node_id(failed_step)
        lines = [
            "flowchart TD",
            f'  start["Audit Run<br/>{run_id}"]',
        ]
        successful_nodes = ["start"]
        previous_node = "start"
        for node_id, label, reason in steps:
            if node_id == failed_node:
                lines.append(f'  {previous_node} -->|"{reason}"| {node_id}["Failed Step<br/>{label}"]')
                lines.append(f'  {node_id} -->|"Processing stopped for business review"| error["Failure Reason<br/>{safe_error}"]')
                break
            lines.append(f'  {previous_node} -->|"{reason}"| {node_id}["{label}"]')
            successful_nodes.append(node_id)
            previous_node = node_id
        else:
            lines.append(f'  {previous_node} -->|"Unexpected processing issue"| failed["Failed Step<br/>{_mermaid_label(failed_step)}"]')
            lines.append(f'  failed -->|"Processing stopped for business review"| error["Failure Reason<br/>{safe_error}"]')
            failed_node = "failed"

        lines.extend(
            [
                "  classDef success fill:#d1fae5,stroke:#15803d,color:#064e3b;",
                "  classDef failure fill:#fee2e2,stroke:#b91c1c,color:#7f1d1d;",
                f"  class {','.join(successful_nodes)} success;",
                f"  class {failed_node},error failure;",
            ]
        )
        full_path = self.project_root / relative_path
        full_path.write_text("\n".join(lines), encoding="utf-8")
        return relative_path.as_posix()

    def _write_json(self, relative_path: Path, payload: dict[str, Any]) -> None:
        full_path = self.project_root / relative_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _safe_file_name(file_name: str, index: int) -> str:
    path_name = Path(file_name).name
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "_", path_name).strip(" .")
    cleaned = cleaned or "attachment.bin"
    return f"{index:03d}-{cleaned}"


def _step_node_id(step_type: str) -> str:
    return {
        "INGESTION": "ingestion",
        "ATTACHMENT_PROCESSING": "attachments",
        "LLM_EXTRACTION": "extraction",
        "VALIDATION": "validation",
    }.get(step_type, step_type.lower())


def _mermaid_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', "'").replace("\r", " ").replace("\n", " ")
