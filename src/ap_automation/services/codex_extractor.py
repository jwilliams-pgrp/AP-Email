from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ap_automation.services.msg_parser import ParsedMsg


class CodexExtractionError(RuntimeError):
    """Raised when Codex CLI extraction fails or returns invalid JSON."""

    def __init__(self, message: str, prompt: str | None = None, raw_response: str | None = None) -> None:
        super().__init__(message)
        self.prompt = prompt
        self.raw_response = raw_response


@dataclass(frozen=True)
class ExtractionAttempt:
    parsed_payload: dict[str, Any]
    prompt: str | None
    raw_response: str | None
    extractor_type: str
    model: str | None
    prompt_version: str | None

    def audit_payload(self) -> dict[str, Any]:
        return {
            "extractor_type": self.extractor_type,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "llm_input": self.prompt,
            "llm_output": self.raw_response,
            "parsed_output": self.parsed_payload,
        }


@dataclass(frozen=True)
class CodexCliExtractor:
    project_root: Path
    command: str = "codex"
    model: str | None = None
    timeout_seconds: int = 300
    skip_git_repo_check: bool = False

    def extract_msg(self, parsed_msg: ParsedMsg, attachment_records: list[dict[str, Any]]) -> ExtractionAttempt:
        prompt = _prompt(parsed_msg, attachment_records)
        with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False, encoding="utf-8") as output_file:
            output_path = Path(output_file.name)

        try:
            command_prefix = shlex.split(self.command, posix=os.name != "nt")
            command = [
                *command_prefix,
                "exec",
                "--cd",
                str(self.project_root),
                "--sandbox",
                "read-only",
                "--output-last-message",
                str(output_path),
                "-",
            ]
            if self.skip_git_repo_check:
                command.insert(len(command_prefix) + 1, "--skip-git-repo-check")
            if self.model:
                command.extend(["--model", self.model])

            try:
                completed = subprocess.run(
                    command,
                    input=prompt,
                    text=True,
                    capture_output=True,
                    timeout=self.timeout_seconds,
                    check=False,
                )
            except OSError as exc:
                raise CodexExtractionError(f"codex exec could not start: {exc}", prompt=prompt, raw_response=None) from exc
            raw_output = output_path.read_text(encoding="utf-8").strip()
            if completed.returncode != 0:
                raise CodexExtractionError(
                    f"codex exec failed with exit code {completed.returncode}: {completed.stderr.strip()}",
                    prompt=prompt,
                    raw_response=raw_output,
                )

            try:
                parsed_payload = _parse_json_object(raw_output)
            except CodexExtractionError as exc:
                raise CodexExtractionError(str(exc), prompt=prompt, raw_response=raw_output) from exc
            extractor = parsed_payload.get("extractor", {})
            return ExtractionAttempt(
                parsed_payload=parsed_payload,
                prompt=prompt,
                raw_response=raw_output,
                extractor_type=str(extractor.get("type") or "codex_cli"),
                model=extractor.get("model") if isinstance(extractor.get("model"), str) else self.model,
                prompt_version=extractor.get("prompt_version") if isinstance(extractor.get("prompt_version"), str) else "local_msg_extraction.v1",
            )
        finally:
            output_path.unlink(missing_ok=True)


def _prompt(parsed_msg: ParsedMsg, attachment_records: list[dict[str, Any]]) -> str:
    attachment_summary = [
        {
            "file_name": record["file_name"],
            "content_type": record.get("content_type"),
            "storage_path": record.get("storage_path"),
            "file_size_bytes": record.get("file_size_bytes"),
            "sha256": record.get("sha256"),
        }
        for record in attachment_records
    ]
    input_payload = {
        "email": {
            "subject": parsed_msg.subject,
            "sender_email": parsed_msg.sender_email,
            "sender_name": parsed_msg.sender_name,
            "received_at": parsed_msg.received_at.isoformat() if parsed_msg.received_at else None,
            "body_text": parsed_msg.body_text,
            "transport_headers": parsed_msg.transport_headers,
        },
        "attachments": attachment_summary,
    }
    return (
        "You are the local LLM extractor for the AP Automation system.\n"
        "Return only one JSON object. Do not include Markdown, prose, or code fences.\n"
        "Extract facts for the extraction.v1 schema below. The deterministic rules engine will make final routing decisions.\n"
        "Do not invent property codes, destinations, email recipients, or invoice facts. Use null for absent facts.\n"
        "Set confidence below 0.90 when required invoice, property, or business-unit facts are incomplete or uncertain.\n"
        "Return observed facts only. Do not return document.document_flags, document.requires_merge, routing outcomes, "
        "destinations, workflow decisions, or high-risk labels. Python derives those after validation.\n"
        "Allowed document_type values: invoice, statement, account_summary, contract, pay_application, vendor_question, "
        "payment_inquiry, past_due_notice, ach_notice, auto_draft_notice, ben_e_keith_notice, lien_release, unknown.\n"
        "Set extractor.type to \"codex_cli\", extractor.name to \"codex_exec\", extractor.model to null unless known, "
        "and extractor.prompt_version to \"local_msg_extraction.v1\".\n\n"
        "Return this exact JSON shape and field names:\n"
        "{\n"
        "  \"schema_version\": \"extraction.v1\",\n"
        "  \"extractor\": {\"type\": \"codex_cli\", \"name\": \"codex_exec\", \"model\": null, \"prompt_version\": \"local_msg_extraction.v1\"},\n"
        "  \"email\": {\"subject\": \"string or null\", \"sender_email\": \"string or null\", \"received_at\": \"ISO-8601 string or null\"},\n"
        "  \"document\": {\n"
        "    \"document_type\": \"invoice|statement|account_summary|contract|pay_application|vendor_question|payment_inquiry|past_due_notice|ach_notice|auto_draft_notice|ben_e_keith_notice|lien_release|unknown\",\n"
        "    \"requires_attachment\": true,\n"
        "    \"has_invoice_attachment\": true,\n"
        "    \"link_only\": false,\n"
        "    \"multi_invoice\": false\n"
        "  },\n"
        "  \"invoice\": {\n"
        "    \"invoice_number\": \"string or null\",\n"
        "    \"invoice_date\": \"YYYY-MM-DD or null\",\n"
        "    \"due_date\": \"YYYY-MM-DD or null\",\n"
        "    \"amount\": 0.0,\n"
        "    \"currency\": \"USD or null\",\n"
        "    \"vendor_name\": \"string or null\",\n"
        "    \"vendor_email\": \"string or null\",\n"
        "    \"bill_to\": \"string or null\",\n"
        "    \"property_code\": \"string or null\",\n"
        "    \"property_name\": \"string or null\",\n"
        "    \"service_address\": \"string or null\"\n"
        "  },\n"
        "  \"business_signals\": {\"business_unit_code\": \"string or null\", \"possible_property_aliases\": [], \"subject_instruction_hint\": \"string or null\"},\n"
        "  \"observed_facts\": {\n"
        "    \"mentions_past_due\": false,\n"
        "    \"mentions_separate_backup_document\": false,\n"
        "    \"mentions_merge_or_combine_required\": false,\n"
        "    \"mentions_lien_waiver_or_release\": false,\n"
        "    \"mentions_payment_link_only\": false,\n"
        "    \"mentions_missing_invoice_attachment\": false,\n"
        "    \"indicates_multiple_invoices\": false,\n"
        "    \"indicates_statement_or_account_summary\": false,\n"
        "    \"indicates_contract_or_pay_application\": false,\n"
        "    \"indicates_vendor_question_or_payment_inquiry\": false,\n"
        "    \"indicates_ach_or_auto_draft\": false,\n"
        "    \"indicates_ben_e_keith\": false,\n"
        "    \"indicates_sold_property\": false,\n"
        "    \"has_conflicting_signals\": false,\n"
        "    \"has_low_text_quality\": false\n"
        "  },\n"
        "  \"confidence\": {\"overall\": 0.0, \"document_type\": 0.0, \"invoice_fields\": 0.0, \"property_identity\": 0.0, \"business_unit\": 0.0},\n"
        "  \"evidence\": {\"summary\": \"short explanation of extracted facts\", \"source_attachments\": [], \"source_pages\": []}\n"
        "}\n"
        "Required fields must be present even when values are null, false, 0.0, or empty arrays. "
        "Use invoice.amount, not amount_due. Use confidence.invoice_fields and confidence.property_identity. "
        "Use observed_facts for source-observable conditions, not document flags. "
        "Use evidence.summary, not an evidence array or arbitrary evidence map.\n\n"
        "Input email and attachment metadata:\n"
        f"{json.dumps(input_payload, indent=2, sort_keys=True)}\n"
    )


def _parse_json_object(raw_output: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw_output)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw_output, re.DOTALL)
        if not match:
            raise CodexExtractionError("codex exec did not return a JSON object")
        parsed = json.loads(match.group(0))

    if not isinstance(parsed, dict):
        raise CodexExtractionError("codex exec returned JSON, but not an object")
    return parsed
