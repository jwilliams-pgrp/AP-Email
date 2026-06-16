from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from difflib import get_close_matches
from pathlib import Path
from typing import Any

from ap_automation.models.extraction import DOCUMENT_TYPES
from ap_automation.models.extraction import ExtractionTriageBatch
from ap_automation.services.msg_parser import ParsedMsg
from ap_automation.services.thread_context import latest_body_text


class AzureOpenAIExtractionError(RuntimeError):
    """Raised when Azure OpenAI extraction fails or returns invalid JSON."""

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
    deployment_name: str | None = None
    api_version: str | None = None
    request_parameters: dict[str, Any] | None = None
    raw_usage: dict[str, Any] | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cached_prompt_tokens: int | None = None
    reasoning_tokens: int | None = None
    latency_ms: int | None = None
    attempts: tuple[dict[str, Any], ...] = ()
    triage: dict[str, Any] | None = None

    def audit_payload(self) -> dict[str, Any]:
        return {
            "extractor_type": self.extractor_type,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "llm_input": self.prompt,
            "llm_output": self.raw_response,
            "parsed_output": self.parsed_payload,
            "llm_metadata": {
                "deployment_name": self.deployment_name,
                "api_version": self.api_version,
                "request_parameters": self.request_parameters or {},
                "raw_usage": self.raw_usage or {},
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "total_tokens": self.total_tokens,
                "cached_prompt_tokens": self.cached_prompt_tokens,
                "reasoning_tokens": self.reasoning_tokens,
                "latency_ms": self.latency_ms,
            },
            "attempts": list(self.attempts),
            "triage": self.triage,
        }


TYPE_CONTRACT_RULES = (
    "Type Contract Rules:\n"
    "- Do not coerce contract types into strings. Return JSON booleans, numbers, arrays, objects, or null exactly as required.\n"
    "- Scalar invoice fields are string or null unless marked as dates or numbers. invoice.property_code and invoice.property_name are string or null, never arrays.\n"
    "- Lookup fields are arrays of strings. property_lookup.property_code and property_lookup.property_name are arrays of strings, never scalar strings.\n"
    "- observed_facts values are booleans only, never \"true\" or \"false\" strings.\n"
    "- confidence values are numbers from 0.0 to 1.0 only, never quoted strings.\n"
    "- invoice.invoice_date and invoice.due_date are YYYY-MM-DD strings or null. email.received_at is an ISO datetime string or null.\n"
    "- evidence.source_pages is an array of integer page numbers only. For attachment page citations, use evidence.source_refs objects with attachment and page.\n"
    "- Wrong: {\"invoice\":{\"property_code\":[\"gw34\"]}} Correct: {\"invoice\":{\"property_code\":\"gw34\"}}\n"
    "- Wrong: {\"property_lookup\":{\"property_code\":\"gw34\"}} Correct: {\"property_lookup\":{\"property_code\":[\"gw34\"]}}\n"
    "- Wrong: {\"observed_facts\":{\"link_only\":\"false\"}} Correct: {\"observed_facts\":{\"mentions_payment_link_only\":false}}\n"
    "- Wrong: {\"confidence\":{\"overall\":\"0.92\"}} Correct: {\"confidence\":{\"overall\":0.92}}\n"
    "- Wrong: {\"evidence\":{\"source_pages\":[\"1\",\"invoice.pdf:page1\"]}} Correct: {\"evidence\":{\"source_pages\":[1],\"source_refs\":[{\"attachment\":\"invoice.pdf\",\"page\":1}]}}\n"
    "- Wrong: {\"evidence\":{\"source_refs\":[\"invoice.pdf:1\"]}} Correct: {\"evidence\":{\"source_refs\":[{\"attachment\":\"invoice.pdf\",\"page\":1}]}}\n"
)

COMPACT_EXTRACTION_BATCH_CONTRACT = (
    "Extraction Batch Contract Checklist:\n"
    "- Return exactly one extraction_batch.v1 JSON object with schema_version, excluded_attachments, and items.\n"
    "- Every items[].extraction must be a complete extraction.v1 object with all required sections present: extractor, email, document, invoice, property_lookup, business_signals, observed_facts, confidence, and evidence.\n"
    "- Required fields must be present even when the value is null, false, 0.0, or []. Do not omit required sections or return partial item extractions.\n"
    "- invoice scalar fields are string-or-null unless explicitly numeric/date: invoice_number, project_number, job_number, invoice_date, due_date, currency, vendor_name, vendor_email, bill_to, bill_to_name_line_1, bill_to_name_line_2, bill_to_street_address, bill_to_suite, bill_to_city, bill_to_state, bill_to_zip_code, property_code, property_name, service_address.\n"
    "- invoice.amount and all confidence fields are JSON numbers, never quoted strings. Confidence keys are overall, document_type, invoice_fields, property_identity, and business_unit.\n"
    "- All observed_facts fields are JSON booleans, never strings or omitted keys.\n"
    "- property_lookup.property_code, property_lookup.property_name, tenant, address, suite, city, state, zipcode, and address_candidates are arrays; use [] when absent.\n"
    "- Allowed document.document_type values are invoice, check_request, statement, account_summary, contract, pay_application, vendor_question, payment_inquiry, past_due_notice, ach_notice, auto_draft_notice, ben_e_keith_notice, lien_release, and unknown.\n"
    "- Allowed address candidate labels are deliver_to, ship_to, service_location, site, property, bill_to, and customer_account.\n"
    "- If any item cannot be represented as a complete valid extraction.v1 object, lower confidence and preserve explicit facts; do not produce a partial object.\n"
)


@dataclass(frozen=True)
class AzureOpenAIExtractor:
    project_root: Path
    endpoint: str | None = None
    api_key: str | None = None
    api_version: str | None = None
    deployment: str | None = None
    timeout_seconds: int = 120

    def extract_msg(
        self,
        parsed_msg: ParsedMsg,
        attachment_records: list[dict[str, Any]],
        asset_reference_rows: list[dict[str, Any]] | None = None,
    ) -> ExtractionAttempt:
        prompt = _prompt(parsed_msg, attachment_records, asset_reference_rows=asset_reference_rows)
        try:
            result = self.run_json_prompt(prompt)
            attempts: tuple[dict[str, Any], ...] = ()
        except AzureOpenAIExtractionError as exc:
            if not exc.raw_response:
                raise
            repair_prompt = contract_repair_prompt(
                original_prompt=prompt,
                invalid_response=exc.raw_response,
                errors=[str(exc)],
                contract_name="extraction_batch.v1",
            )
            result = self.run_json_prompt(repair_prompt)
            attempts = (
                {
                    "attempt": 1,
                    "status": "parse_error",
                    "error": str(exc),
                    "raw_response": exc.raw_response,
                },
                {
                    "attempt": 2,
                    "status": "retry_response",
                    "retry_reason": "json_parse_failed",
                    "raw_response": result.raw_output,
                    "parsed_output": result.parsed_payload,
                },
            )
        raw_output, parsed_payload = result
        extractor = parsed_payload.get("extractor", {})
        return ExtractionAttempt(
            parsed_payload=parsed_payload,
            prompt=prompt,
            raw_response=raw_output,
            extractor_type=str(extractor.get("type") or "azure_openai"),
            model=extractor.get("model") if isinstance(extractor.get("model"), str) else self._deployment(),
            prompt_version=extractor.get("prompt_version") if isinstance(extractor.get("prompt_version"), str) else "azure_msg_extraction.v1",
            deployment_name=result.deployment_name,
            api_version=result.api_version,
            request_parameters=result.request_parameters,
            raw_usage=result.raw_usage,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            total_tokens=result.total_tokens,
            cached_prompt_tokens=result.cached_prompt_tokens,
            reasoning_tokens=result.reasoning_tokens,
            latency_ms=result.latency_ms,
            attempts=attempts,
        )

    def triage_msg(
        self,
        parsed_msg: ParsedMsg,
        attachment_records: list[dict[str, Any]],
    ) -> ExtractionAttempt:
        prompt = _triage_prompt(parsed_msg, attachment_records)
        try:
            result = self.run_json_prompt(prompt)
            attempts: tuple[dict[str, Any], ...] = ()
        except AzureOpenAIExtractionError as exc:
            if not exc.raw_response:
                raise
            repair_prompt = contract_repair_prompt(
                original_prompt=prompt,
                invalid_response=exc.raw_response,
                errors=[str(exc)],
                contract_name="extraction_triage_batch.v1",
            )
            result = self.run_json_prompt(repair_prompt)
            attempts = (
                {
                    "attempt": 1,
                    "status": "parse_error",
                    "error": str(exc),
                    "raw_response": exc.raw_response,
                },
                {
                    "attempt": 2,
                    "status": "retry_response",
                    "retry_reason": "json_parse_failed",
                    "raw_response": result.raw_output,
                    "parsed_output": result.parsed_payload,
                },
            )
        raw_output, parsed_payload = result
        return ExtractionAttempt(
            parsed_payload=parsed_payload,
            prompt=prompt,
            raw_response=raw_output,
            extractor_type="azure_openai",
            model=self._deployment(),
            prompt_version="azure_msg_triage.v1",
            deployment_name=result.deployment_name,
            api_version=result.api_version,
            request_parameters=result.request_parameters,
            raw_usage=result.raw_usage,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            total_tokens=result.total_tokens,
            cached_prompt_tokens=result.cached_prompt_tokens,
            reasoning_tokens=result.reasoning_tokens,
            latency_ms=result.latency_ms,
            attempts=attempts,
        )

    def extract_msg_with_triage(
        self,
        parsed_msg: ParsedMsg,
        attachment_records: list[dict[str, Any]],
        triage_batch: ExtractionTriageBatch,
        asset_reference_rows: list[dict[str, Any]] | None = None,
    ) -> ExtractionAttempt:
        prompt = _prompt(
            parsed_msg,
            attachment_records,
            asset_reference_rows=asset_reference_rows,
            triage_payload=triage_batch.raw,
        )
        try:
            result = self.run_json_prompt(prompt)
            attempts: tuple[dict[str, Any], ...] = ()
        except AzureOpenAIExtractionError as exc:
            if not exc.raw_response:
                raise
            repair_prompt = contract_repair_prompt(
                original_prompt=prompt,
                invalid_response=exc.raw_response,
                errors=[str(exc)],
                contract_name="extraction_batch.v1",
            )
            result = self.run_json_prompt(repair_prompt)
            attempts = (
                {
                    "attempt": 1,
                    "status": "parse_error",
                    "error": str(exc),
                    "raw_response": exc.raw_response,
                },
                {
                    "attempt": 2,
                    "status": "retry_response",
                    "retry_reason": "json_parse_failed",
                    "raw_response": result.raw_output,
                    "parsed_output": result.parsed_payload,
                },
            )
        raw_output, parsed_payload = result
        extractor = parsed_payload.get("extractor", {})
        return ExtractionAttempt(
            parsed_payload=parsed_payload,
            prompt=prompt,
            raw_response=raw_output,
            extractor_type=str(extractor.get("type") or "azure_openai"),
            model=extractor.get("model") if isinstance(extractor.get("model"), str) else self._deployment(),
            prompt_version=extractor.get("prompt_version") if isinstance(extractor.get("prompt_version"), str) else "azure_msg_targeted_extraction.v1",
            deployment_name=result.deployment_name,
            api_version=result.api_version,
            request_parameters=result.request_parameters,
            raw_usage=result.raw_usage,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            total_tokens=result.total_tokens,
            cached_prompt_tokens=result.cached_prompt_tokens,
            reasoning_tokens=result.reasoning_tokens,
            latency_ms=result.latency_ms,
            attempts=attempts,
        )

    def run_json_prompt(self, prompt: str) -> "JsonPromptResult":
        endpoint = self._endpoint()
        api_version = self._api_version()
        deployment = self._deployment()
        headers = {
            "Content-Type": "application/json",
            **self._auth_headers(),
        }
        url = (
            f"{endpoint.rstrip('/')}/openai/deployments/{urllib.parse.quote(deployment, safe='')}/chat/completions"
            f"?api-version={urllib.parse.quote(api_version, safe='')}"
        )
        request_payload = {
            "messages": [
                {
                    "role": "system",
                    "content": "Return only valid JSON for AP Automation extraction and interpretation tasks.",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        request_parameters = {
            "temperature": request_payload["temperature"],
            "response_format": request_payload["response_format"],
        }
        body = json.dumps(request_payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers=headers,
        )
        print(
            f"[azure-openai-extractor] starting chat completion "
            f"(deployment={deployment}, api_version={api_version}, timeout={self.timeout_seconds}s)",
            flush=True,
        )
        started_at = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                response_body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise AzureOpenAIExtractionError(
                f"Azure OpenAI request failed with HTTP {exc.code}: {error_body}",
                prompt=prompt,
                raw_response=error_body,
            ) from exc
        except urllib.error.URLError as exc:
            raise AzureOpenAIExtractionError(f"Azure OpenAI request could not complete: {exc}", prompt=prompt, raw_response=None) from exc
        except TimeoutError as exc:
            elapsed = time.perf_counter() - started_at
            raise AzureOpenAIExtractionError(
                f"Azure OpenAI request timed out after {elapsed:.1f}s (limit={self.timeout_seconds}s).",
                prompt=prompt,
                raw_response=None,
            ) from exc

        elapsed = time.perf_counter() - started_at
        print(f"[azure-openai-extractor] chat completion completed (elapsed={elapsed:.1f}s)", flush=True)
        response_payload = _response_payload(response_body)
        raw_usage = response_payload.get("usage") if isinstance(response_payload.get("usage"), dict) else {}
        raw_output = _message_content(response_payload)
        try:
            parsed_payload = _parse_json_object(raw_output)
        except AzureOpenAIExtractionError as exc:
            raise AzureOpenAIExtractionError(str(exc), prompt=prompt, raw_response=raw_output) from exc
        return JsonPromptResult(
            raw_output=raw_output,
            parsed_payload=parsed_payload,
            deployment_name=deployment,
            api_version=api_version,
            request_parameters=request_parameters,
            raw_usage=raw_usage,
            prompt_tokens=_int_or_none(raw_usage.get("prompt_tokens")),
            completion_tokens=_int_or_none(raw_usage.get("completion_tokens")),
            total_tokens=_int_or_none(raw_usage.get("total_tokens")),
            cached_prompt_tokens=_nested_int_or_none(raw_usage, ("prompt_tokens_details", "cached_tokens")),
            reasoning_tokens=_nested_int_or_none(raw_usage, ("completion_tokens_details", "reasoning_tokens")),
            latency_ms=max(0, int(round(elapsed * 1000))),
        )

    def _endpoint(self) -> str:
        return _required_config(self.endpoint or os.environ.get("AZURE_OPENAI_ENDPOINT"), "AZURE_OPENAI_ENDPOINT")

    def _api_key(self) -> str:
        return _required_config(self.api_key or os.environ.get("AZURE_OPENAI_API_KEY"), "AZURE_OPENAI_API_KEY")

    def _api_version(self) -> str:
        return _required_config(self.api_version or os.environ.get("AZURE_OPENAI_API_VERSION"), "AZURE_OPENAI_API_VERSION")

    def _deployment(self) -> str:
        return _required_config(self.deployment or os.environ.get("AZURE_OPENAI_DEPLOYMENT"), "AZURE_OPENAI_DEPLOYMENT")

    def _auth_headers(self) -> dict[str, str]:
        auth_mode = os.environ.get("AZURE_OPENAI_AUTH_MODE", "identity").strip().lower()
        if auth_mode == "api_key":
            return {"api-key": self._api_key()}
        try:
            from azure.identity import DefaultAzureCredential
        except Exception as exc:
            if self.api_key or os.environ.get("AZURE_OPENAI_API_KEY"):
                return {"api-key": self._api_key()}
            raise AzureOpenAIExtractionError(
                "azure-identity is required for identity-based Azure OpenAI authentication. "
                "Set AZURE_OPENAI_AUTH_MODE=api_key only for local development fallback."
            ) from exc
        token = DefaultAzureCredential().get_token("https://cognitiveservices.azure.com/.default").token
        return {"Authorization": f"Bearer {token}"}


def _prompt(
    parsed_msg: ParsedMsg,
    attachment_records: list[dict[str, Any]],
    asset_reference_rows: list[dict[str, Any]] | None = None,
    triage_payload: dict[str, Any] | None = None,
) -> str:
    attachment_summary = [
        {
            "file_name": record["file_name"],
            "content_type": record.get("content_type"),
            "storage_path": record.get("storage_path"),
            "file_size_bytes": record.get("file_size_bytes"),
            "sha256": record.get("sha256"),
            "text_excerpt": record.get("text_excerpt"),
            "extractor_selection": (record.get("metadata") or {}).get("extractor_selection") if isinstance(record.get("metadata"), dict) else None,
            "pdf_evaluation": (record.get("metadata") or {}).get("pdf_evaluation") if isinstance(record.get("metadata"), dict) else None,
            "document_intelligence": (record.get("metadata") or {}).get("document_intelligence") if isinstance(record.get("metadata"), dict) else None,
        }
        for record in attachment_records
    ]
    input_payload = {
        "email": {
            "subject": parsed_msg.subject,
            "sender_email": parsed_msg.sender_email,
            "sender_name": parsed_msg.sender_name,
            "received_at": parsed_msg.received_at.isoformat() if parsed_msg.received_at else None,
            "latest_body_text": latest_body_text(parsed_msg.metadata, parsed_msg.body_text),
            "body_text": parsed_msg.body_text,
            "thread_context": (parsed_msg.metadata or {}).get("thread_context"),
            "transport_headers": parsed_msg.transport_headers,
        },
        "attachments": attachment_summary,
        "asset_reference": [
            {
                "asset_name": row.get("asset_name"),
                "asset_alias": row.get("asset_alias"),
                "asset_type": row.get("asset_type"),
                "address": row.get("address"),
            }
            for row in (asset_reference_rows or [])
            if isinstance(row, dict)
        ],
    }
    triage_section = ""
    if triage_payload:
        triage_section = (
            "Validated triage from the first LLM pass:\n"
            f"{json.dumps(triage_payload, indent=2, sort_keys=True)}\n"
            "Use this triage only to keep detailed extraction focused on the listed items and source attachments. "
            "If detailed evidence conflicts with triage, preserve the safer AP-risk facts in the extraction payload. "
            "Still return a complete extraction_batch.v1 object; do not return triage fields in the final extraction.\n\n"
            f"{_route_guidance(triage_payload)}\n"
        )
    return (
        "You are the local LLM extractor for the AP Automation system.\n"
        "Return only one JSON object. Do not include Markdown, prose, or code fences.\n"
        f"{COMPACT_EXTRACTION_BATCH_CONTRACT}\n"
        f"{TYPE_CONTRACT_RULES}\n"
        "Return an extraction_batch.v1 envelope with one item per AP workflow-relevant non-inline attachment and, only when the email body independently contains routing facts or exceptions not already represented by a selected readable attachment, one email-level item. Routine cover text that says an invoice, bill, statement, or document is attached is context for the attachment item only and must not create a separate email-level item. Generated invoice email summaries, including BuildOps-style bodies that repeat invoice number, due date, total, balance due, bill-to, or payment summary, are also context only when a selected readable invoice attachment already contains the same invoice. If validated triage includes an email-only item but detailed evidence shows the email body only duplicates a selected readable attachment, omit that email item from the final extraction_batch.v1. Bill-to-only facts in the email body must not create a second invoice item when the attachment has stronger service, property, site, or location evidence. Each item.extraction must be a complete extraction.v1 payload with required sections present. Put clearly irrelevant reviewed attachments in batch-level excluded_attachments, not in items. The deterministic rules engine will make final routing decisions.\n"
        "Thread-aware email body handling: email.latest_body_text is authoritative for the current email-level AP action. email.body_text is the full message body retained for context and audit. Quoted history may provide background only when latest_body_text explicitly reactivates or asks about that prior invoice or document. A forwarded message can reactivate quoted AP content when it is sent to the AP mailbox for handling, even when the current latest body is empty, signature-only, or contact-card-only. Do not create invoice, link-only, vendor-question, wrong-destination, property-routing, statement, or other current actionable facts from quoted history alone unless the current message context is a forward or other current submission that makes the quoted content the thing being sent for AP handling. Set observed_facts.latest_reply_indicates_no_ap_action=true only when email.latest_body_text itself contains actual non-action language: an acknowledgement, courtesy/social reply, FYI-only note, or confirmation that the recipient will handle/process the prior item. Empty latest body, signature-only latest body, contact-card-only latest body, or an internal sender alone is not social/no-action language. A forward or reply may still be social/no-action only when the latest-body words look like social/no-action language, such as \"Thanks, handled\", \"FYI only\", \"I already sent this\", or \"No action needed\". If the language or context is fuzzy, lean toward leaving latest_reply_indicates_no_ap_action=false and preserving AP-risk facts. Do not set latest_reply_indicates_no_ap_action=true when the latest body asks a question, reports a wrong destination, introduces a new invoice/payment/link action, cites a current attachment, or is empty/signature-only while quoted history contains invoice, payment-link, statement, vendor-question, or other AP workflow facts. The same no-action reply must also not introduce a current statement action. Treat an internal @hillwood.com sender as a positive indicator for this extraction fact only when actual no-action language is present, not as an outcome or destination. Examples that should set latest_reply_indicates_no_ap_action=true when no current AP evidence is attached or introduced include \"Thank you. I just sent it.\", \"Received, thank you.\", \"I resent it.\", and \"I will handle this.\" Quoted statement, invoice, or vendor-question history must not override a latest-body no-action acknowledgement.\n"
        "AP workflow-relevant attachments include invoices, statements, payment inquiries, lien releases or waivers, contracts, pay applications, check requests, account summaries, past-due notices, ACH or auto-draft notices, Ben E Keith notices, wrong-destination evidence, and documents with invoice, payment, property, vendor, amount, account, or routing facts. "
        "Omit clearly irrelevant attachments from items after reviewing selected attachment text, Document Intelligence text, filename, and email context. Clearly irrelevant means the attachment has no invoice, payable amount, vendor, invoice number, property, account, payment, hard-exception, or other AP workflow facts, such as a generic sign photo, legal notice image unrelated to the invoice, logo, or decorative/non-business image. "
        "Standalone payment-instruction support PDFs may be put in excluded_attachments with reason_code=\"payment_instruction_support\" only when at least one separate valid invoice item exists in the same batch and the standalone support PDF contains no invoice number, payable amount, bill-to/property/service address, statement balance, vendor question, dispute, missing-remittance question, unsupported invoice evidence, lien waiver, work ticket, backup packet, contract, pay application, or other hard-exception content. "
        "Examples of standalone payment-instruction support include wire instructions, ACH instructions, remittance instructions, and payment portal instructions. Do not emit those standalone support PDFs as actionable items when they meet this exclusion rule. "
        "Do not include standalone payment-instruction support PDFs in any invoice item's evidence.source_attachments or evidence.source_refs. Keep payment instructions embedded inside the invoice PDF as normal invoice evidence for that invoice item. "
        "Do not include irrelevant attachment filenames in a valid invoice item's evidence.source_attachments or evidence.source_refs. "
        "For each clearly irrelevant reviewed attachment, add excluded_attachments entry with file_name, reason_code=\"irrelevant_to_ap_workflow\", reason, and optional source of document_intelligence, pymupdf, filename, or email_context. "
        "For each standalone payment-instruction support PDF excluded under the support rule, add an excluded_attachments entry with file_name, reason_code=\"payment_instruction_support\", reason, and optional source of document_intelligence, pymupdf, filename, or email_context. "
        "Excluded attachments must not appear in any item's evidence.source_attachments or evidence.source_refs. If an unsupported, unreadable, or non-PDF attachment is the claimed invoice evidence, return it as an item so deterministic policy can escalate explicitly. "
        "If an unsupported, unreadable, image, Office, spreadsheet, or other non-PDF attachment contains AP workflow facts, return it as an item so deterministic policy can escalate explicitly.\n"
        "Do not merge invoice facts across attachments. A PDF that contains multiple invoices should be one attachment item with document.multi_invoice=true; separate invoice PDFs should be separate items with document.multi_invoice=false.\n"
        "Attachment extractor_selection.selected_extractor identifies the authoritative attachment text source for that attachment: pymupdf means use the top-level attachment text_excerpt from pdf_evaluation, document_intelligence means use successful prebuilt-layout text, and none means no attachment text source was selected. "
        "Attachment document_intelligence summaries are Azure Document Intelligence evidence from prebuilt-layout and, when invoice-like, prebuilt-invoice. Prefer successful document_intelligence text only when extractor_selection.selected_extractor is document_intelligence. PyMuPDF pdf_evaluation text is deterministic PDF text evidence only when extractor_selection.selected_extractor is pymupdf. Do not treat Document Intelligence as a routing decision source. Do not treat PyMuPDF or extractor selection as a routing decision source.\n"
        "Do not invent property codes, destinations, email recipients, or invoice facts. Use null for absent facts.\n"
        "The input asset_reference list is read-only normalization context for visible property, building, site, service, billing, shipping, delivery, or address text. "
        "It is not routing authority and must never be used to choose destinations, outcomes, recipients, workflow rules, ownership, or final routing. "
        "Use asset_reference only when the email or selected attachment text visibly matches a listed asset_name, asset_alias, asset_type, or address. "
        "Use asset_type only to normalize source-visible property text such as Retail, Multifamily, Ground Lease, Project, Job, Site, or Service Location; do not use asset_type to invent facts or make routing decisions. "
        "When visible source text exactly matches or is a clear semantic near-match to exactly one listed asset, populate canonical normalized lookup candidates such as property_lookup.property_name=[\"alliance gateway 34\"] and property_lookup.property_code=[\"gw34\"]. "
        "Clear semantic near-matches include missing common portfolio prefixes, suffix variants, number-format variants, and configured alias variants when only one asset_reference row fits the visible phrase, such as Gateway 15 -> Alliance Gateway 15 / GW15, Circle T Golf Course -> Circle T Golf / CTG, and Heritage Commons 2 -> Heritage Commons II / HC2. "
        "Do not normalize vague family names or ambiguous partial names such as Gateway, Circle T, Commons, or Westport into a property code or property_name when multiple configured assets could fit; keep those uncertain values in business_signals.possible_property_aliases or evidence.summary. "
        "Preserve visible asset-code families exactly. Codes such as WP9, GW9, HC2, HWC2, ACC 14, and ACN5 identify different configured alias families unless the source visibly provides an accepted alias variant. "
        "Do not convert a visible Westport/WP code into an Alliance Gateway/GW code, or a visible Gateway/GW code into a Westport/WP code. "
        "Negative example: source text \"Service at: WP9 400 Intermodal Pkwy\" with asset_reference containing WP9 / Alliance Westport 9 and GW9 / Alliance Gateway 9 must not return gw9 or alliance gateway 9; it should return property_lookup.property_code=[\"wp9\"] and property_lookup.property_name=[\"alliance westport 9\"] when supported, or omit the property_name if not confidently supported. "
        "If a visible code and a proposed canonical property name conflict, keep the visible code, remove the unsupported canonical name, lower confidence.property_identity, and set observed_facts.has_conflicting_signals=true. "
        "Labeled identity fields such as Project, Job, Site, Location, Service Location, Property, Building, Facility, Work Site, Ship To, Deliver To, Sold To, Customer, Account, Attention, and similar labels are source-visible property identity evidence when their values contain property, building, site, tenant, address, or asset-code facts. "
        "Do not limit property evidence capture to Bill To or Ship To blocks; vendors may use nonstandard labels or unlabeled adjacent customer/site/address blocks. "
        "When a labeled or adjacent name/address block contains both a generic legal customer name and a more specific visible asset/property/site name, preserve the specific name separately in property_lookup.property_name when it matches asset_reference, or in business_signals.possible_property_aliases when uncertain. "
        "For example, text like \"Hillwood Alliance Group, LP Circle T Golf 2451 Westlake Pkwy\" must preserve \"Circle T Golf\" as source-visible property identity evidence instead of flattening the whole block into only a bill_to/customer name and address. "
        "When the value following one of these labels visibly matches or clearly near-matches an asset_reference asset_name or asset_alias, populate property_lookup.property_name with the normalized asset name and property_lookup.property_code with the normalized asset_alias when available. "
        "Do not drop or demote a visible labeled Project, Job, Site, Location, or Property value solely because address evidence is incomplete, city-mismatched, ZIP-missing, or shared by multiple assets. "
        "If a visible labeled property/project name identifies one asset and a bill-to or address-only signal matches multiple assets, use the visible labeled name/code as the distinguishing lookup signal while preserving the address evidence. "
        "For example, source text \"Bill To 2451 Westlake Parkway Westlake, Tx ... Project Circle T Ranch\" with asset_reference Circle T Ranch / CTR must return property_lookup.property_name=[\"circle t ranch\"] and property_lookup.property_code=[\"ctr\"], with the 2451 Westlake Parkway address retained as address evidence. "
        "If a Project or Job value is a generic work description or does not visibly match a known asset name or alias, do not invent property_lookup.property_name or property_lookup.property_code from it; keep it in business_signals.possible_property_aliases or evidence when useful. "
        "Use listed addresses only to clean up, normalize, or confirm lookup candidates from visible source text; do not use addresses to invent invoice facts or unseen service locations. "
        "Preserve the original visible source text in audit-facing fields, address_candidates.evidence_text, evidence.summary, source_attachments, and source_refs where applicable. "
        "Preserve explicit visible asset or property names in audit-facing fields such as invoice.property_name, address_candidates.evidence_text, and evidence.summary; do not rewrite a visible asset name into a different canonical asset family because nearby address, bill-to, customer-account, or code evidence resembles another asset. "
        "Canonical property codes and names may be populated only when visibly supported by email body, subject, selected attachment text, or attachment metadata; asset_reference can normalize visible source text but is not source evidence. "
        "Visible addresses should become address candidates first; do not invent property codes or property names from address resemblance alone. "
        "Do not put account, customer, or tenant names into invoice.property_code, invoice.property_name, property_lookup.property_code, or property_lookup.property_name unless the source visibly labels them as property or asset identity. "
        "Do not copy invoice number into invoice.project_number or invoice.job_number unless the source explicitly labels that value as project, project number, job, or job number. "
        "When a visible property name and address disagree, prefer the visible property name for canonical asset normalization unless the source explicitly labels the address as the invoice service, site, delivery, shipping, or property address. "
        "If asset_reference contains Hillwood Commons II / HWC2 and the source visibly says Hillwood Commons II, normalize to property_lookup.property_name=[\"hillwood commons ii\"] and property_lookup.property_code=[\"hwc2\"]. "
        "Do not convert visible Hillwood Commons II to Heritage Commons II / HC2 unless the source visibly says Heritage Commons II or HC2. "
        "When visible source text contains Alliance Gateway shorthand such as AG31, AG 31, or AG-31, and asset_reference contains the corresponding Alliance Gateway building, treat that shorthand as visible evidence for Alliance Gateway 31 and normalize to the listed asset_reference asset_name and configured asset_alias. "
        "AG shorthand is an explicit exception for Alliance Gateway only; do not treat WP, GW, HC, HWC, ACC, ACN, or other configured alias prefixes as interchangeable. "
        "Final source-support check before returning JSON: verify every invoice.property_code, invoice.property_name, property_lookup.property_code, and property_lookup.property_name value against email subject, email body, selected attachment text, or attachment metadata. "
        "The asset_reference list is not source evidence for this check. "
        "If a property code or property name is only inferred from asset_reference, remove it from property_lookup arrays and set invoice.property_code or invoice.property_name to null. "
        "Tenant-only source text such as Pei Wei/Chipotle belongs in property_lookup.tenant or business_signals.possible_property_aliases, not invoice.property_code, invoice.property_name, property_lookup.property_code, or property_lookup.property_name. "
        "Negative example: if source text says Pei Wei/Chipotle 2901 Heritage Trace Pkwy and asset_reference contains GW31 / Alliance Gateway 31, the correct output must not include gw31 or alliance gateway 31; keep visible tenant and address evidence only. "
        "Positive example: if source text visibly says GW31, GW 31, AG31, or Alliance Gateway 31 and asset_reference contains the corresponding Alliance Gateway building, the correct output may normalize to property_lookup.property_code=[\"gw31\"] and property_lookup.property_name=[\"alliance gateway 31\"]. "
        "If visible name, address, or code signals conflict and you cannot confidently resolve the asset, lower confidence.property_identity and set observed_facts.has_conflicting_signals=true. "
        "If visible source signals conflict during the final source-support check, lower confidence.property_identity and set observed_facts.has_conflicting_signals=true. "
        "Return only the final corrected JSON; do not include the self-check. "
        "If visible source text does not match asset_reference, do not create asset names, aliases, codes, or addresses from the reference list.\n"
        "Property identity evidence hierarchy: service/site/location/deliver-to/ship-to evidence is stronger than project/job/property name or code evidence, which is stronger than bill-to/customer-account evidence, which is stronger than remit-to/vendor/signature evidence. "
        "Bill-to and customer-account facts may populate invoice.bill_to fields and lower-ranked bill_to or customer_account address candidates, but must not override serviced-property identity when service/site/location/deliver-to/ship-to evidence exists. "
        "A bill-to address that maps to a different asset is not a conflict when clear service/site/location/deliver-to/ship-to evidence identifies the serviced property. "
        "Set observed_facts.has_conflicting_signals=true only for material conflicts among same-strength property signals, or when stronger serviced-property evidence points to a different configured asset.\n"
        "Property identity example: if source text says Bill To: Hillwood Alliance Group (CFW), 9800 Hillwood Pkwy #300 and also says Attn: Hillwood Alliance Airport Tower, 2300 Alliance Blvd, Fort Worth, TX 76177, the correct output uses property_lookup.property_name=[\"hillwood alliance airport tower\"], property_lookup.property_code=[\"tower\"] when visibly supported by asset_reference normalization, address_candidates[0].label=\"service_location\" for 2300 alliance boulevard, address_candidates[1].label=\"bill_to\" for 9800 hillwood pkwy, and observed_facts.has_conflicting_signals=false. "
        "Incorrect output for that example includes property_lookup.property_name=[\"hillwood commons i\"], property_lookup.property_code=[\"hwc1\"], or treating the bill-to address as a serviced-property conflict.\n"
        "For property_lookup, return normalized structured lookup fields only: property_code, property_name, tenant, address, suite, city, state, zipcode, and optional address_candidates. "
        "Each property_lookup field must be an array of normalized strings; use an empty array when no value is present. "
        "Postgres property lookup will use this output directly, so normalize property_lookup values before returning them. "
        "Return every visible plausible asset address as property_lookup.address_candidates with rank, label, street, city, state, zipcode, normalized_address, source, confidence, and evidence_text. "
        "Only include address_candidates entries when at least one address component is visible: street, normalized_address, city, state, or zipcode. "
        "Never put project names, property names, asset names, building aliases, tenant names, account names, or job descriptions in address_candidates unless an address component is visible in the same candidate. "
        "If a visible signal is only a project, property, asset, building, tenant, account, or job name, put it in property_lookup.property_name, property_lookup.property_code, property_lookup.tenant, or business_signals.possible_property_aliases instead, and leave address_candidates empty unless an actual address is visible. "
        "Allowed address candidate labels are deliver_to, ship_to, service_location, site, property, bill_to, and customer_account. "
        "Rank strongest site signals first: DELIVER TO, SHIP TO, SERVICE LOCATION, SITE, JOB, and LOCATION. "
        "Prefer Project, Job, Site, Service Location, Location, Deliver To, Ship To, or Property fields over Bill To when identifying the serviced property. "
        "Rank customer/account address or bill-to as medium only when no stronger site signal exists; rank bill-to weaker when a stronger site/shipping/delivery address also exists. "
        "Also return explicit property, tenant, site, service, shipping, billing, and bill-to address signals you can identify in the legacy flat property_lookup arrays, in the same candidate rank order. "
        "Treat labels such as Building, Property, Site, Job, Location, Facility, Work Site, Ship To, Deliver To, Sold To, Customer, Account, Attention, Service Location, and the email subject as property identity sources when the visible value contains property, building, site, tenant, address, or asset-code facts. "
        "Before returning JSON, scan selected attachment text for labels Project, Job, Site, Location, Service Location, Property, Building, Facility, Work Site, Ship To, Deliver To, Sold To, Customer, Account, and Attention, plus adjacent repeated customer/address blocks; if the labeled or adjacent value visibly matches or clearly near-matches an asset_reference asset_name or asset_alias, ensure property_lookup.property_name or property_lookup.property_code includes that normalized candidate. "
        "Asset codes are short building aliases like GW31, GW 31, HCX, HC-2, ACC 14, or WP9; put normalized compact versions in property_lookup.property_code, for example GW 31 -> gw31 and HC-2 -> hc2. "
        "Asset names are building names like Alliance Gateway 31 or Heritage Commons X; put normalized names in property_lookup.property_name when visible. "
        "Tenant or occupant names shown beside a building alias, such as GW 31 / US Conec, should go in property_lookup.tenant as normalized text. "
        "When a service/property/site/shipping address is visible with city, state, or ZIP, include both the normalized street-only address and the complete normalized address in property_lookup.address, with street-only first; for example 5201 Alliance Gateway Freeway, Fort Worth, TX 76177 becomes address values \"5201 alliance gateway freeway\" and \"5201 alliance gateway freeway fort worth tx 76177\", plus city \"fort worth\", state \"tx\", and zipcode \"76177\". "
        "Order service/property/site/shipping address candidates before billing or bill-to address candidates because SQL treats earlier property_lookup.address values as stronger. "
        "Do not include sender, vendor, remit-to, or email signature addresses in property_lookup or address_candidates unless the same address is explicitly labeled as the invoice's service, property, site, shipping, delivery, or bill-to address. "
        "Do not use remit-to, sender, vendor, or signature addresses as serviced-property evidence. "
        "Do not invent missing lookup values. "
        "Normalize lookup values by lowercasing, trimming whitespace, replacing punctuation and special characters with spaces, and collapsing multiple spaces; "
        "expand st to street, rd to road, dr to drive, pkwy or pwky to parkway, fwy to freeway, blvd to boulevard, ln to lane, ct to court, ave to avenue, "
        "normalize ft worth to fort worth and n/s/e/w to north/south/east/west, remove suite prefixes such as ste, suite, unit, and #, keep state as a lowercase 2-letter abbreviation, "
        "keep zipcode as a 5-digit numeric value only, and compact property-code formatting such as HC-2 or HC 2 to hc2.\n"
        "Set confidence below 0.90 when required invoice, property, or business-unit facts are incomplete or uncertain.\n"
        "Return observed facts only. Do not return document.document_flags, document.requires_merge, routing outcomes, "
        "destinations, workflow decisions, or high-risk labels. Python derives those after validation.\n"
        "Return separate AP-relevant supporting documents as their own batch items when they contain workflow facts tied to an invoice. "
        "For invoice items, set observed_facts.mentions_separate_backup_document=true only when there is a distinct supporting-document item tied to the invoice; Python will clear the signal when no separate supporting item exists. "
        "Related supporting documents include lien waivers, lien releases, work orders, field tickets, service tickets, delivery tickets, time-entry detail reports, hourly detail reports, shift reports, actual hours worked, hours worked, staffing hours, timesheets, time sheets, labor/detail backup, labor/material breakdowns, job completion records, signed tickets, or similar support. "
        "The supporting document does not need to say \"invoice\". "
        "Infer relation from shared vendor, project/job, customer, location, invoice number, work order number, service date, technician, amount, line items, or work description. "
        "Do not set mentions_separate_backup_document=true for embedded invoice pages, invoice line-item detail, work descriptions or same-invoice work detail, photo filenames or image references inside the invoice PDF, inline images, logos, decorative images, or attachments excluded as irrelevant. "
        "This is not a duplicate-invoice scenario, and it does not require explicit merge or combine instructions.\n"
        "For service appointment reminders, maintenance reminders, inspection notices, access notices, and similar non-payable property communications, set document_type to \"unknown\", keep document.link_only=false, and populate property_lookup from visible property, site, service, account, or address facts. "
        "Set observed_facts.indicates_informational_appointment_notice=true only for confirmations, reminders, follow-ups, upcoming appointments, scheduled service visits, technician visits, reschedule notices, or similar informational appointment/service-visit messages when the current email does not identify a current invoice, bill, payment request, statement, vendor question, link-only invoice, or other AP action. "
        "Do not classify these informational property notices as invoices, statements, payment inquiries, or link-only invoices unless the message explicitly identifies a current bill or invoice requiring payment, viewing, retrieval, or download.\n"
        "Filename, attachment title, and subject are weak metadata for document type and must not override extracted document content. "
        "Classify invoice-like content as invoice even when the filename or title says Receipt, including Receipt.pdf. "
        "Invoice-positive signals include invoice number, INV:, Invoice #, invoice date, due date, vendor, terms, amount, subtotal, tax, total, balance due, "
        "current amount due, current charges, service period, bill-to, sold-to, ship-to, service/site/property address, line items, quantities, unit prices, delivery charges, service charges, payment instructions, and remittance instructions. "
        "Classify a utility, telecom, or service bill as document_type=\"invoice\" when a single current payable bill or invoice is present with invoice number, due date, total/current amount due, service charges or service period, and bill-to, service address, property, or payment/remittance details. "
        "Do not classify as statement or account_summary solely because labels say Statement Date, Summary of Charges, Previous Balance, or Balance Forward. "
        "Use \"statement\" or \"account_summary\" only for documents dominated by non-payable receipt-only documents, customer statements, aging summaries, balance recaps, payment confirmations, transaction histories, or multiple open-item summaries. "
        "Do not use account_summary solely because a filename or title says Receipt. "
        "Classify a completed-payment document as document_type=\"account_summary\" and set observed_facts.indicates_statement_or_account_summary=true only when the source explicitly confirms payment already completed, such as payment confirmation, paid receipt, receipt of payment, payment received, paid by, paid on, a confirmation number, reference number, check number, or balance after payment 0.00. "
        "Use the completed-payment account_summary rule only when there is no current request to pay, view, download, or retrieve a bill or invoice and no current amount due. "
        "Do not use this rule for generic Receipt.pdf filenames or receipt-labeled documents that contain payable invoice structure. "
        "Do not use this rule for current bills or invoices, link-only bill notices, statements, transaction histories, aging summaries, or balance recaps unless they independently meet existing account-summary or statement rules. "
        "If completed-payment evidence and current payable-bill evidence both appear, keep document_type=\"invoice\" or document_type=\"invoice\" with document.link_only=true per payable-bill rules, and set observed_facts.has_conflicting_signals=true when the conflict is material. "
        "If invoice-positive signals and account-summary or statement labels both appear, classify as invoice unless explicit account-summary or statement structure dominates. "
        "Set observed_facts.indicates_statement_or_account_summary=true only when statement/account-summary structure dominates over payable invoice structure. "
        "If both are present but a single payable invoice is complete, keep document_type=\"invoice\" and mention the conflicting statement labels in evidence.summary. "
        "If unclear, keep document_type=\"invoice\", lower confidence.document_type, and set observed_facts.has_conflicting_signals=true.\n"
        "Disambiguate invoices from pay applications by the dominant document evidence, not by isolated progress-billing terminology. "
        "Progress-billing columns or labels such as Contract Amount, Percent Complete, Total Billed, Prior Billed, and Current Billed do not by themselves make a document a pay_application. "
        "Classify Westwood-style and other professional-services progress billing documents as document_type=\"invoice\" when they present as invoices, including visible INVOICE title, Invoice No, invoice date, Total This Invoice, payable amount, remittance copy, remit/payment instructions, or ordinary invoice payment terms. "
        "For those invoice-presenting progress billing documents, set observed_facts.indicates_contract_or_pay_application=false. "
        "Do not infer pay_application from project billing, progress billing, percent complete, contract amount, prior billed, or current billed terminology alone. "
        "Reserve document_type=\"pay_application\" for explicit pay-application or draw-request evidence such as Application for Payment, Pay Application, AIA-style payment applications, draw requests, or documents where payment-application structure dominates the invoice evidence.\n"
        "For no-attachment vendor payment or account questions, set document_type to \"vendor_question\" or \"payment_inquiry\". "
        "Set observed_facts.indicates_vendor_question_or_payment_inquiry=true when the sender asks AP to answer, confirm, research, reconcile, or explain invoice, payment, or account facts. "
        "Positive examples include duplicate payment confirmation, \"can you please confirm\", \"please advise\", missing remittance, which invoice an ACH paid, payment-to-invoice matching, multiple possible open invoices for one payment, account reconciliation, dispute, credit, and missing backup/support questions. "
        "Routine AP processing or collection language is not a vendor question when a complete invoice is present. Negative examples include \"please process\", \"please submit payment\", \"please remit\", \"include invoice number\", \"reference invoice number\", \"contact us with questions\", payment options, and remittance instructions. "
        "A vendor question requires AP to answer, confirm, research, reconcile, or explain invoice, payment, or account facts. "
        "Example: a vendor says duplicate payments were received, lists invoice numbers, and asks \"Can you please Confirm?\"; classify as payment_inquiry or vendor_question and set indicates_vendor_question_or_payment_inquiry=true.\n"
        "Keep invoice.bill_to as one compressed display line when present, while preserving bill_to_name_line_1, bill_to_name_line_2, bill_to_street_address, bill_to_suite, bill_to_city, bill_to_state, and bill_to_zip_code as structured components. "
        "Routing relies on property_lookup and Postgres matching, not only invoice.bill_to.\n"
        "For evidence, source_attachments and source_refs must be scoped to the current item being extracted. For evidence, source_pages must contain page numbers as integers only, never filenames or strings. "
        "When citing pages from attachments, put structured objects in evidence.source_refs with attachment and page fields, for example {\"attachment\":\"invoice.pdf\",\"page\":1}. "
        "Use evidence.source_attachments for filenames and evidence.source_refs for attachment-specific page citations.\n"
        "Set observed_facts.indicates_wrong_destination=true only when the message explicitly says a prior routing recipient was the wrong person, "
        "should not have received the email, or asks AP to escalate because the destination was wrong.\n"
        "Set observed_facts.current_invoice_is_past_due=true only when the current email subject or body explicitly calls the payable invoice past due, overdue, in collection, or a true past-due notice. "
        "Do not use attachment-only labels, invoice due dates, payment due dates, remit-by dates, or invoice date comparisons to set current-invoice past-due facts. "
        "Do not set observed_facts.current_invoice_is_past_due=true merely because an invoice says payable upon receipt, because invoice.due_date is before email.received_at.date(), or because invoice_date was copied into invoice.due_date. "
        "Extract invoice.due_date only when the source text explicitly labels a concrete calendar date as the due date, payment due date, remit-by date, or equivalent payment deadline. "
        "Do not populate invoice.due_date for due-on-receipt, due upon receipt, payable upon receipt, net due upon receipt, or similar receipt-based payment terms. "
        "Do not infer invoice.due_date from invoice date, service date, activity date, posting date, email received date, receipt-based terms, or due-on-receipt language. "
        "Do not copy invoice_date into invoice.due_date unless the document explicitly presents that date as the payment due date. "
        "Payment-status follow-up emails for the current invoice may set current_invoice_is_past_due=true only when the current email subject or body explicitly asks for overdue, past-due, or collection handling. "
        "Statements and account summaries with aging tables, open items, or account-level past-due balances remain statement/account-summary filing candidates and must not be classified as past-due notices unless the document is explicitly a past-due or collection notice. "
        "Set it false when the current invoice amount or balance due is in the Current aging bucket, even if other past-due aging buckets are nonzero. "
        "Set observed_facts.account_has_past_due_aging_balance=true when a separate account-level aging table or footer shows nonzero past-due balances outside the current invoice. "
        "Set observed_facts.contains_aging_summary=true when an aging table, aging footer, or account aging summary is visible. "
        "Extract these as facts only; never choose outcomes, destinations, or routing rules.\n"
        "For Ben E Keith related invoice/payment notice emails, set document_type to \"ben_e_keith_notice\" and "
        "observed_facts.indicates_ben_e_keith=true when the sender, subject, body, or attachment name clearly identifies Ben E Keith, "
        "including variants such as \"Ben E. Keith\", \"Ben E Keith\", \"BEK\", \"ChefTec\", "
        "\"benekeith@cleointegration.cloud\", or wording like \"Ben E. Keith invoice attached\". "
        "Do this even when the message also uses invoice wording; Ben E Keith notices are filed by deterministic policy and must not be classified as ordinary invoices.\n"
        "Allowed document_type values: invoice, check_request, statement, account_summary, contract, pay_application, vendor_question, "
        "payment_inquiry, past_due_notice, ach_notice, auto_draft_notice, ben_e_keith_notice, lien_release, unknown.\n"
        "Set extractor.type to \"azure_openai\", extractor.name to \"azure_openai_foundry\", extractor.model to the Azure OpenAI deployment name when known, "
        "and extractor.prompt_version to \"azure_msg_extraction.v1\".\n\n"
        "Final silent self-check before returning JSON: verify every populated field is visibly source-supported; verify stronger service/site/location evidence was not overridden by bill-to or administrative evidence; verify asset_reference was used only for normalization; verify routine payment or remittance language did not become vendor inquiry; verify no workflow outcomes, destinations, document.document_flags, or other derived workflow labels were returned. "
        "Do not include this self-check in the JSON output.\n\n"
        "Return this exact batch JSON shape and field names:\n"
        "{\n"
        "  \"schema_version\": \"extraction_batch.v1\",\n"
        "  \"excluded_attachments\": [{\"file_name\": \"logo.jpg\", \"reason_code\": \"irrelevant_to_ap_workflow\", \"reason\": \"Decorative logo with no AP workflow facts.\", \"source\": \"filename\"}],\n"
        "  \"items\": [\n"
        "    {\"item_kind\": \"attachment\", \"item_key\": \"attachment:{sha256 or filename}\", \"display_name\": \"invoice.pdf\", \"attachment_id\": null, \"metadata\": {}, \"extraction\": {\n"
        "  \"schema_version\": \"extraction.v1\",\n"
        "  \"extractor\": {\"type\": \"azure_openai\", \"name\": \"azure_openai_foundry\", \"model\": null, \"prompt_version\": \"azure_msg_extraction.v1\"},\n"
        "  \"email\": {\"subject\": \"string or null\", \"sender_email\": \"string or null\", \"received_at\": \"ISO-8601 string or null\"},\n"
        "  \"document\": {\n"
        "    \"document_type\": \"invoice|check_request|statement|account_summary|contract|pay_application|vendor_question|payment_inquiry|past_due_notice|ach_notice|auto_draft_notice|ben_e_keith_notice|lien_release|unknown\",\n"
        "    \"requires_attachment\": true,\n"
        "    \"has_invoice_attachment\": true,\n"
        "    \"link_only\": false,\n"
        "    \"multi_invoice\": false\n"
        "  },\n"
        "  \"invoice\": {\n"
        "    \"invoice_number\": \"string or null\",\n"
        "    \"project_number\": \"string or null\",\n"
        "    \"job_number\": \"string or null\",\n"
        "    \"invoice_date\": \"YYYY-MM-DD or null\",\n"
        "    \"due_date\": \"YYYY-MM-DD or null\",\n"
        "    \"amount\": 0.0,\n"
        "    \"currency\": \"USD or null\",\n"
        "    \"vendor_name\": \"string or null\",\n"
        "    \"vendor_email\": \"string or null\",\n"
        "    \"bill_to\": \"string or null\",\n"
        "    \"bill_to_name_line_1\": \"string or null\",\n"
        "    \"bill_to_name_line_2\": \"string or null\",\n"
        "    \"bill_to_street_address\": \"string or null\",\n"
        "    \"bill_to_suite\": \"string or null\",\n"
        "    \"bill_to_city\": \"string or null\",\n"
        "    \"bill_to_state\": \"2-letter state code string or null\",\n"
        "    \"bill_to_zip_code\": \"ZIP string or null\",\n"
        "    \"property_code\": \"string or null\",\n"
        "    \"property_name\": \"string or null\",\n"
        "    \"service_address\": \"string or null\"\n"
        "  },\n"
        "  \"property_lookup\": {\"property_code\": [\"normalized compact string\"], \"property_name\": [\"normalized string\"], \"tenant\": [\"normalized string\"], \"address\": [\"normalized street address\"], \"suite\": [\"normalized suite string\"], \"city\": [\"normalized city\"], \"state\": [\"2-letter lowercase state code\"], \"zipcode\": [\"5-digit ZIP string\"], \"address_candidates\": []},\n"
        "  \"business_signals\": {\"business_unit_code\": \"string or null\", \"possible_property_aliases\": [], \"subject_instruction_hint\": \"string or null\"},\n"
        "  \"observed_facts\": {\n"
        "    \"current_invoice_is_past_due\": false,\n"
        "    \"account_has_past_due_aging_balance\": false,\n"
        "    \"contains_aging_summary\": false,\n"
        "    \"mentions_separate_backup_document\": false,\n"
        "    \"mentions_merge_or_combine_required\": false,\n"
        "    \"mentions_lien_waiver_or_release\": false,\n"
        "    \"mentions_payment_link_only\": false,\n"
        "    \"mentions_missing_invoice_attachment\": false,\n"
        "    \"indicates_multiple_invoices\": false,\n"
        "    \"indicates_statement_or_account_summary\": false,\n"
        "    \"indicates_contract_or_pay_application\": false,\n"
        "    \"indicates_vendor_question_or_payment_inquiry\": false,\n"
    "    \"indicates_wrong_destination\": false,\n"
    "    \"latest_reply_indicates_no_ap_action\": false,\n"
    "    \"indicates_informational_appointment_notice\": false,\n"
    "    \"indicates_ach_or_auto_draft\": false,\n"
        "    \"indicates_ben_e_keith\": false,\n"
        "    \"has_conflicting_signals\": false,\n"
        "    \"has_low_text_quality\": false\n"
        "  },\n"
        "  \"confidence\": {\"overall\": 0.0, \"document_type\": 0.0, \"invoice_fields\": 0.0, \"property_identity\": 0.0, \"business_unit\": 0.0},\n"
        "  \"evidence\": {\"summary\": \"short explanation of extracted facts\", \"source_attachments\": [], \"source_pages\": [], \"source_refs\": [{\"attachment\": \"invoice.pdf\", \"page\": 1}]}\n"
        "    }}\n"
        "  ]\n"
        "}\n"
        "Required fields must be present even when values are null, false, 0.0, or empty arrays. "
        "Use invoice.amount, not amount_due. Use confidence.invoice_fields and confidence.property_identity. "
        "Extract explicit PROJECT NO or PROJECT NUMBER values into invoice.project_number. "
        "Extract explicit JOB NO or JOB NUMBER values into invoice.job_number, never invoice.project_number. "
        "Use observed_facts for source-observable conditions, not document flags. "
        "Use evidence.summary, not an evidence array or arbitrary evidence map. "
        "Use address_candidates: [] as the default. When actual address components are visible, address candidate objects must use this shape: "
        "{\"rank\": 1, \"label\": \"deliver_to|ship_to|service_location|site|property|bill_to|customer_account\", \"street\": \"normalized street or null\", \"city\": \"normalized city or null\", \"state\": \"2-letter lowercase state code or null\", \"zipcode\": \"5-digit ZIP string or null\", \"normalized_address\": \"complete normalized address or null\", \"source\": \"email|attachment:file.pdf:page or null\", \"confidence\": 0.0, \"evidence_text\": \"short visible source text or null\"}. "
        "Project, property, tenant, account, alias, or job names without address components must not be address candidates. "
        "Filename, attachment title, and subject are weak metadata for document type. Receipt.pdf with invoice number, invoice date, terms, line items, tax, and total must be document_type=\"invoice\", not account_summary. "
        "A FiberFirst-style utility/service bill with Statement Date, Summary of Charges, Previous Balance, or Balance Forward labels is still document_type=\"invoice\" when it contains one current payable bill with invoice number, due date, current amount due, service charges, and bill-to, service address, property, or payment/remittance facts. "
        "True account summaries and statements are limited to non-payable receipts, customer statements, aging summaries, balance recaps, payment confirmations, transaction histories, or multiple open-item summaries where that structure dominates over a single payable invoice. "
        "Completed-payment documents with explicit payment confirmation, paid receipt, receipt of payment, payment received, paid by, paid on, confirmation number, reference number, check number, or balance after payment 0.00 evidence should be document_type=\"account_summary\" with observed_facts.indicates_statement_or_account_summary=true only when there is no current request to pay, view, download, or retrieve a bill or invoice and no current amount due. "
        "Do not apply the completed-payment rule to generic Receipt.pdf filenames, receipt-labeled payable invoices, current bills or invoices, link-only bill notices, statements, transaction histories, aging summaries, or balance recaps unless existing payable-bill or account-summary rules independently support that classification. "
        "When completed-payment evidence conflicts with current payable-bill evidence, keep the payable classification as document_type=\"invoice\" or document.link_only=true and set observed_facts.has_conflicting_signals=true if needed. "
        "Treat payment-link call-to-action detection as first-pass extraction work: if the email asks the recipient to view, retrieve, download, or pay a current bill or invoice through a URL and no invoice attachment is present, set "
        "document.link_only=true and observed_facts.mentions_payment_link_only=true. "
        "Do not set document.link_only or observed_facts.mentions_payment_link_only when the email body already contains usable payable invoice facts, such as invoice number, vendor, amount, due date, bill-to or service-location details, and line items or work description. "
        "QuickBooks-style messages with visible invoice details plus print, save, view, or pay links are body-embedded invoices, not link-only invoices. "
        "Continue setting link-only for payment portals, utility bill notices, service bill notices, or view/pay/download-invoice emails where the body lacks enough payable invoice detail and the invoice or bill must be retrieved through the link. "
        "Set document.multi_invoice and observed_facts.indicates_multiple_invoices to true only when a single attached PDF contains multiple invoices; "
        "do not set them just because multiple invoices are discussed in email text or because there are multiple separate attachments. "
        "Set document.link_only and observed_facts.mentions_payment_link_only to true only when an invoice or bill is available only by link and no invoice attachment is present; "
        "do not set them for generic customer portals, account portals, auto-pay enrollment, service appointment reminders, maintenance reminders, inspection notices, access notices, or informational payment portal notices unless current bill or invoice action language is present.\n\n"
        "Do not set indicates_vendor_question_or_payment_inquiry for routine invoice-payment collection language when a valid invoice is attached, including "
        "\"attached invoice is due\", \"please review for payment\", \"when can we expect payment\", payment options, remittance instructions, or invoice dispute contact text. "
        "Set indicates_vendor_question_or_payment_inquiry only when the sender is asking AP to answer, confirm, research, reconcile, or explain a payment/account question, such as missing remittance, "
        "which invoice an ACH paid, payment-to-invoice matching, multiple possible open invoices for one payment, account reconciliation, dispute, credit, duplicate payment confirmation, or missing backup/support questions.\n\n"
        "Wrong-destination replies are distinct from vendor questions: set indicates_wrong_destination only for explicit wrong-recipient escalation messages.\n\n"
        "Input email and attachment metadata:\n"
        f"{triage_section}"
        f"{json.dumps(input_payload, indent=2, sort_keys=True)}\n"
    )


def _route_guidance(triage_payload: dict[str, Any]) -> str:
    routes = {
        item.get("extraction_route")
        for item in triage_payload.get("items", [])
        if isinstance(item, dict) and isinstance(item.get("extraction_route"), str)
    }
    guidance = ["Route-specific detail guidance:"]
    if "invoice_detail" in routes:
        guidance.append("- For invoice_detail items, focus on payable invoice fields, property lookup signals, amount/date/vendor facts, and AP exception flags.")
    if "statement_detail" in routes:
        guidance.append("- For statement_detail items, focus on statement/account-summary/notice facts, filing-relevant observed facts, and avoid inventing payable invoice fields.")
    if "exception_detail" in routes:
        guidance.append("- For exception_detail items, focus on source-visible risk facts such as link-only, vendor inquiry, wrong destination, past due, contracts, pay applications, unsupported files, and conflicting signals.")
    if "notice_detail" in routes:
        guidance.append("- For notice_detail items, focus on ACH, auto-draft, Ben E Keith, appointment, informational, and non-payable notice facts.")
    if "email_only_detail" in routes:
        guidance.append("- For email_only_detail items, use the current email body as evidence and do not create facts from unrelated attachments.")
    if "no_detail" in routes:
        guidance.append("- For no_detail items, return only a safe non-payable email-level extraction when current source text clearly supports no AP action.")
    return "\n".join(guidance)


def _triage_prompt(parsed_msg: ParsedMsg, attachment_records: list[dict[str, Any]]) -> str:
    attachment_summary = [
        {
            "file_name": record["file_name"],
            "content_type": record.get("content_type"),
            "storage_path": record.get("storage_path"),
            "file_size_bytes": record.get("file_size_bytes"),
            "sha256": record.get("sha256"),
            "text_excerpt": record.get("text_excerpt"),
            "extractor_selection": (record.get("metadata") or {}).get("extractor_selection") if isinstance(record.get("metadata"), dict) else None,
            "pdf_evaluation": (record.get("metadata") or {}).get("pdf_evaluation") if isinstance(record.get("metadata"), dict) else None,
            "document_intelligence": (record.get("metadata") or {}).get("document_intelligence") if isinstance(record.get("metadata"), dict) else None,
        }
        for record in attachment_records
    ]
    input_payload = {
        "email": {
            "subject": parsed_msg.subject,
            "sender_email": parsed_msg.sender_email,
            "sender_name": parsed_msg.sender_name,
            "received_at": parsed_msg.received_at.isoformat() if parsed_msg.received_at else None,
            "latest_body_text": latest_body_text(parsed_msg.metadata, parsed_msg.body_text),
            "body_text": parsed_msg.body_text,
            "thread_context": (parsed_msg.metadata or {}).get("thread_context"),
            "transport_headers": parsed_msg.transport_headers,
        },
        "attachments": attachment_summary,
    }
    document_types = ", ".join(sorted(DOCUMENT_TYPES))
    return (
        "You are the first-pass triage classifier for the AP Automation system.\n"
        "Return only one JSON object. Do not include Markdown, prose, or code fences.\n"
        "Return exactly one extraction_triage_batch.v1 object with schema_version, excluded_attachments, and items.\n"
        "Triage is for itemization, document classification, AP relevance, extraction-route selection, and source-visible risk flags only.\n"
        "Do not return workflow rules, outcomes, destinations, destination emails, recipients, property candidates, or final routing decisions.\n"
        "Every AP workflow-relevant attachment or email body source must appear as an item. Omit only clearly irrelevant attachments through excluded_attachments.\n"
        "Use one item per distinct AP workflow-relevant document. Do not merge invoice facts across attachments.\n"
        "Allowed item_kind values: attachment, email.\n"
        f"Allowed document_type values: {document_types}.\n"
        "Allowed extraction_route values: invoice_detail, statement_detail, exception_detail, notice_detail, email_only_detail, no_detail.\n"
        "Allowed risk_flags values: multi_invoice, separate_supporting_document, link_only, contract_or_pay_application, vendor_question_or_payment_inquiry, wrong_destination, past_due, unsupported_attachment, low_text_quality, conflicting_signals.\n"
        "Use the multi_invoice risk_flag only when one attached PDF visibly contains multiple distinct payable invoices, such as multiple invoice numbers, repeated invoice headers, or multiple complete payable invoice sections inside the same PDF. "
        "Do not use the multi_invoice risk_flag for a single invoice with line items, subtotal/tax/total rows, credits/payments rows, balance due, one invoice number, one total, one balance due, or an aging table with Current, 1-30, 31-60, 61-90, or 90+ buckets. "
        "Do not use the multi_invoice risk_flag merely because the email mentions an invoice number, the filename contains an invoice number, or there are multiple separate invoice attachments; separate invoice PDFs are separate items.\n"
        "Use the past_due risk_flag only when the current email subject or body explicitly calls the payable invoice past due, overdue, in collection, or a true past-due notice. "
        "Do not use the past_due risk_flag merely because an invoice contains an aging table or nonzero 1-30, 31-60, 61-90, or 90+ past-due buckets. "
        "When the document shows account-level aging balances but the current invoice is not explicitly past due, mention the aging table in reason and do not include past_due in risk_flags.\n"
        "Use invoice_detail for payable invoices, check requests, and invoice-like bills requiring field extraction.\n"
        "Use statement_detail for statements, account summaries, receipts, ACH notices, auto-draft notices, and Ben E Keith notices.\n"
        "Use exception_detail for contracts, pay applications, lien releases, multi-invoice PDFs, link-only invoices, vendor questions, payment inquiries, wrong-destination replies, past-due notices, unsupported files with AP facts, and ambiguous high-risk content.\n"
        "Use email_only_detail only when the current email body independently contains AP workflow facts that selected readable attachments do not already hold as item evidence.\n"
        "Do not create an email item for routine cover text that only says an invoice, bill, statement, or document is attached when a selected readable attachment already contains that item evidence. Also do not create an email item for generated invoice summary text, including BuildOps-style bodies, that repeats invoice number, due date, total, balance due, bill-to, or payment summary for a readable attached invoice. In those cases, the email body is context for the attachment item only.\n"
        "Use no_detail only for safely non-payable no-action email items; do not use no_detail for any invoice, statement, exception, notice, unsupported attachment, or ambiguous AP-relevant source.\n"
        "For thread replies and forwards, email.latest_body_text is authoritative for the current email-level action, and quoted history is background unless the current message context makes the quoted content the submitted AP item. "
        "A forwarded message can reactivate quoted AP content when it is sent to the AP mailbox for handling. Empty latest body, signature-only latest body, contact-card-only latest body, or an internal sender alone is not social/no-action language. "
        "A short internal @hillwood.com reply such as \"Thank you. I just sent it.\", \"Received, thank you.\", or \"I resent it.\" may be a safe no_detail item only when the latest-body words look like social/no-action language and it does not ask a question, report a wrong destination, cite a current attachment as AP evidence, or introduce new invoice, payment, statement, link, vendor-question, or property-routing facts. "
        "If the language or context is fuzzy, lean away from no_detail and preserve AP-risk facts for detail extraction. Do not classify a current reply as statement_detail only because quoted history or the subject mentions a statement.\n"
        "Set requires_detail_extraction=true for every AP-relevant item except safe no_detail items.\n"
        "Return this exact shape:\n"
        "{\n"
        "  \"schema_version\":\"extraction_triage_batch.v1\",\n"
        "  \"excluded_attachments\":[{\"file_name\":\"name.ext\",\"reason_code\":\"irrelevant_to_ap_workflow|payment_instruction_support\",\"reason\":\"short reason\",\"source\":\"document_intelligence|pymupdf|filename|email_context\"}],\n"
        "  \"items\":[{\"item_kind\":\"attachment|email\",\"item_key\":\"stable unique key\",\"display_name\":\"string or null\",\"source_attachments\":[\"name.ext\"],\"document_type\":\"invoice\",\"requires_detail_extraction\":true,\"extraction_route\":\"invoice_detail\",\"risk_flags\":[],\"confidence\":0.0,\"reason\":\"short source-visible reason\"}]\n"
        "}\n"
        "Input email and attachment metadata:\n"
        f"{json.dumps(input_payload, indent=2, sort_keys=True)}\n"
    )


def _parse_json_object(raw_output: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw_output)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw_output, re.DOTALL)
        if not match:
            raise AzureOpenAIExtractionError("Azure OpenAI did not return a JSON object")
        parsed = json.loads(match.group(0))

    if not isinstance(parsed, dict):
        raise AzureOpenAIExtractionError("Azure OpenAI returned JSON, but not an object")
    return parsed


def contract_repair_prompt(
    *,
    original_prompt: str,
    invalid_response: str | None,
    errors: list[str],
    contract_name: str,
    lint_findings: dict[str, Any] | None = None,
) -> str:
    lint_section = ""
    if lint_findings:
        lint_section = (
            "Advisory contract lint findings:\n"
            f"{json.dumps(lint_findings, indent=2, sort_keys=True)}\n"
            "These lint findings are repair guidance only. The schema validation errors above remain the hard contract gate.\n\n"
        )
    return (
        "The previous response did not satisfy the required AP Automation JSON contract.\n"
        f"Contract: {contract_name}\n"
        "Return one complete corrected JSON object for the original task. Do not return a patch, diff, Markdown, or prose.\n"
        "Do not invent facts, destinations, asset IDs, workflow rules, or routing outcomes. Use null, false, 0.0, or empty arrays for absent facts when the contract requires a field.\n\n"
        f"{_compact_contract_skeleton(contract_name)}\n\n"
        f"{COMPACT_EXTRACTION_BATCH_CONTRACT}\n"
        f"{_canonical_contract_checklist(contract_name)}\n\n"
        f"{TYPE_CONTRACT_RULES}\n"
        "Validation errors:\n"
        f"{json.dumps(errors, indent=2, sort_keys=True)}\n\n"
        f"{lint_section}"
        "Repair guidance:\n"
        "- property_lookup.address_candidates may contain only address-like candidates with at least one visible street, normalized_address, city, state, or zipcode value.\n"
        "- If an address_candidates object contains only a project name, property name, building alias, tenant name, account name, job description, source, evidence_text, rank, label, or confidence, remove that address_candidates object.\n"
        "- Move visible non-address identity text to property_lookup.property_name, property_lookup.property_code, property_lookup.tenant, or business_signals.possible_property_aliases as appropriate.\n"
        "- Do not invent address components to make an address_candidates object valid.\n\n"
        "Invalid response:\n"
        f"{invalid_response or ''}\n\n"
        "Original task prompt:\n"
        f"{original_prompt}\n"
    )


def lint_extraction_contract(payload: dict[str, Any]) -> dict[str, Any]:
    """Return advisory extraction contract findings without changing validation behavior."""
    findings = {"missing_required_keys": [], "unknown_keys": [], "close_key_matches": []}
    if not isinstance(payload, dict):
        return findings

    if payload.get("schema_version") == "extraction_batch.v1":
        _lint_object(payload, "batch", _BATCH_KEYS, _BATCH_REQUIRED_KEYS, findings)
        for index, raw_item in enumerate(payload.get("items") or []):
            if not isinstance(raw_item, dict):
                continue
            item_path = f"items[{index}]"
            _lint_object(raw_item, item_path, _BATCH_ITEM_KEYS, _BATCH_ITEM_REQUIRED_KEYS, findings)
            extraction = raw_item.get("extraction")
            if isinstance(extraction, dict):
                _lint_extraction_payload(extraction, f"{item_path}.extraction", findings)
        for index, excluded in enumerate(payload.get("excluded_attachments") or []):
            if isinstance(excluded, dict):
                _lint_object(
                    excluded,
                    f"excluded_attachments[{index}]",
                    _EXCLUDED_ATTACHMENT_KEYS,
                    _EXCLUDED_ATTACHMENT_REQUIRED_KEYS,
                    findings,
                )
    else:
        _lint_extraction_payload(payload, "extraction", findings)

    return {key: value for key, value in findings.items() if value}


def _lint_extraction_payload(payload: dict[str, Any], path: str, findings: dict[str, list[Any]]) -> None:
    _lint_object(payload, path, _EXTRACTION_KEYS, _EXTRACTION_REQUIRED_KEYS, findings)
    for key, allowed, required in (
        ("extractor", _EXTRACTOR_KEYS, _EXTRACTOR_REQUIRED_KEYS),
        ("email", _EMAIL_KEYS, _EMAIL_REQUIRED_KEYS),
        ("document", _DOCUMENT_KEYS, _DOCUMENT_REQUIRED_KEYS),
        ("invoice", _INVOICE_KEYS, _INVOICE_REQUIRED_KEYS),
        ("property_lookup", _PROPERTY_LOOKUP_KEYS, _PROPERTY_LOOKUP_REQUIRED_KEYS),
        ("business_signals", _BUSINESS_SIGNALS_KEYS, _BUSINESS_SIGNALS_REQUIRED_KEYS),
        ("observed_facts", _OBSERVED_FACTS_KEYS, _OBSERVED_FACTS_REQUIRED_KEYS),
        ("confidence", _CONFIDENCE_KEYS, _CONFIDENCE_REQUIRED_KEYS),
        ("evidence", _EVIDENCE_KEYS, _EVIDENCE_REQUIRED_KEYS),
    ):
        value = payload.get(key)
        if isinstance(value, dict):
            _lint_object(value, f"{path}.{key}", allowed, required, findings)
    address_candidates = (payload.get("property_lookup") or {}).get("address_candidates") if isinstance(payload.get("property_lookup"), dict) else None
    for index, candidate in enumerate(address_candidates or []):
        if isinstance(candidate, dict):
            _lint_object(candidate, f"{path}.property_lookup.address_candidates[{index}]", _ADDRESS_CANDIDATE_KEYS, _ADDRESS_CANDIDATE_REQUIRED_KEYS, findings)
    refs = (payload.get("evidence") or {}).get("source_refs") if isinstance(payload.get("evidence"), dict) else None
    for index, ref in enumerate(refs or []):
        if isinstance(ref, dict):
            _lint_object(ref, f"{path}.evidence.source_refs[{index}]", _SOURCE_REF_KEYS, _SOURCE_REF_REQUIRED_KEYS, findings)


def _lint_object(
    obj: dict[str, Any],
    path: str,
    allowed_keys: set[str],
    required_keys: set[str],
    findings: dict[str, list[Any]],
) -> None:
    for key in sorted(required_keys - set(obj)):
        findings["missing_required_keys"].append(f"{path}.{key}")
    for key in sorted(set(obj) - allowed_keys):
        key_path = f"{path}.{key}"
        findings["unknown_keys"].append(key_path)
        matches = get_close_matches(key, allowed_keys, n=1, cutoff=0.78)
        if matches:
            findings["close_key_matches"].append({"path": key_path, "did_you_mean": matches[0]})


def _canonical_contract_checklist(contract_name: str) -> str:
    document_types = ", ".join(sorted(DOCUMENT_TYPES))
    return (
        "Canonical field checklist:\n"
        "- Batch envelope keys: schema_version, excluded_attachments, items.\n"
        "- Batch item keys: item_kind, item_key, display_name, attachment_id, metadata, extraction.\n"
        "- extraction.v1 top-level keys: schema_version, extractor, email, document, invoice, property_lookup, business_signals, observed_facts, confidence, evidence.\n"
        "- invoice keys: invoice_number, project_number, job_number, invoice_date, due_date, amount, currency, vendor_name, vendor_email, bill_to, bill_to_name_line_1, bill_to_name_line_2, bill_to_street_address, bill_to_suite, bill_to_city, bill_to_state, bill_to_zip_code, property_code, property_name, service_address.\n"
        "- observed_facts keys: current_invoice_is_past_due, account_has_past_due_aging_balance, contains_aging_summary, mentions_separate_backup_document, mentions_merge_or_combine_required, mentions_lien_waiver_or_release, mentions_payment_link_only, mentions_missing_invoice_attachment, indicates_multiple_invoices, indicates_statement_or_account_summary, indicates_contract_or_pay_application, indicates_vendor_question_or_payment_inquiry, indicates_wrong_destination, latest_reply_indicates_no_ap_action, indicates_informational_appointment_notice, indicates_ach_or_auto_draft, indicates_ben_e_keith, has_conflicting_signals, has_low_text_quality.\n"
        "- confidence keys: overall, document_type, invoice_fields, property_identity, business_unit.\n"
        f"- Allowed document.document_type values: {document_types}."
    )


def _compact_contract_skeleton(contract_name: str) -> str:
    if contract_name != "extraction_batch.v1":
        return "Return the complete JSON object required by the named contract."
    return (
        "Compact extraction_batch.v1 skeleton:\n"
        "{\n"
        "  \"schema_version\": \"extraction_batch.v1\",\n"
        "  \"excluded_attachments\": [],\n"
        "  \"items\": [\n"
        "    {\n"
        "      \"item_kind\": \"attachment|email\",\n"
        "      \"item_key\": \"stable unique item key\",\n"
        "      \"display_name\": \"string or null\",\n"
        "      \"attachment_id\": null,\n"
        "      \"metadata\": {},\n"
        "      \"extraction\": {\"schema_version\": \"extraction.v1\", \"extractor\": {}, \"email\": {}, \"document\": {}, \"invoice\": {}, \"property_lookup\": {}, \"business_signals\": {}, \"observed_facts\": {}, \"confidence\": {}, \"evidence\": {}}\n"
        "    }\n"
        "  ]\n"
        "}\n"
        "The extraction object in each item must be expanded to the complete extraction.v1 contract, not left as empty section objects."
    )


_BATCH_KEYS = {"schema_version", "excluded_attachments", "items"}
_BATCH_REQUIRED_KEYS = {"schema_version", "items"}
_BATCH_ITEM_KEYS = {"item_kind", "item_key", "display_name", "attachment_id", "metadata", "extraction"}
_BATCH_ITEM_REQUIRED_KEYS = {"item_kind", "item_key", "extraction"}
_EXCLUDED_ATTACHMENT_KEYS = {"file_name", "reason_code", "reason", "source"}
_EXCLUDED_ATTACHMENT_REQUIRED_KEYS = {"file_name", "reason_code", "reason"}
_EXTRACTION_KEYS = {"schema_version", "extractor", "email", "document", "invoice", "property_lookup", "business_signals", "observed_facts", "confidence", "evidence"}
_EXTRACTION_REQUIRED_KEYS = _EXTRACTION_KEYS
_EXTRACTOR_KEYS = {"type", "name", "model", "prompt_version"}
_EXTRACTOR_REQUIRED_KEYS = {"type"}
_EMAIL_KEYS = {"subject", "sender_email", "received_at"}
_EMAIL_REQUIRED_KEYS = set(_EMAIL_KEYS)
_DOCUMENT_KEYS = {"document_type", "requires_attachment", "has_invoice_attachment", "link_only", "multi_invoice"}
_DOCUMENT_REQUIRED_KEYS = {"document_type", "link_only", "multi_invoice"}
_INVOICE_KEYS = {"invoice_number", "project_number", "job_number", "invoice_date", "due_date", "amount", "currency", "vendor_name", "vendor_email", "bill_to", "bill_to_name_line_1", "bill_to_name_line_2", "bill_to_street_address", "bill_to_suite", "bill_to_city", "bill_to_state", "bill_to_zip_code", "property_code", "property_name", "service_address"}
_INVOICE_REQUIRED_KEYS = set(_INVOICE_KEYS)
_PROPERTY_LOOKUP_KEYS = {"property_code", "property_name", "tenant", "address", "suite", "city", "state", "zipcode", "address_candidates"}
_PROPERTY_LOOKUP_REQUIRED_KEYS = set()
_BUSINESS_SIGNALS_KEYS = {"business_unit_code", "possible_property_aliases", "subject_instruction_hint"}
_BUSINESS_SIGNALS_REQUIRED_KEYS = set(_BUSINESS_SIGNALS_KEYS)
_OBSERVED_FACTS_KEYS = {"current_invoice_is_past_due", "account_has_past_due_aging_balance", "contains_aging_summary", "mentions_separate_backup_document", "mentions_merge_or_combine_required", "mentions_lien_waiver_or_release", "mentions_payment_link_only", "mentions_missing_invoice_attachment", "indicates_multiple_invoices", "indicates_statement_or_account_summary", "indicates_contract_or_pay_application", "indicates_vendor_question_or_payment_inquiry", "indicates_wrong_destination", "latest_reply_indicates_no_ap_action", "indicates_informational_appointment_notice", "indicates_ach_or_auto_draft", "indicates_ben_e_keith", "has_conflicting_signals", "has_low_text_quality"}
_OBSERVED_FACTS_REQUIRED_KEYS = set(_OBSERVED_FACTS_KEYS)
_CONFIDENCE_KEYS = {"overall", "document_type", "invoice_fields", "property_identity", "business_unit"}
_CONFIDENCE_REQUIRED_KEYS = set(_CONFIDENCE_KEYS)
_EVIDENCE_KEYS = {"summary", "source_attachments", "source_pages", "source_refs"}
_EVIDENCE_REQUIRED_KEYS = {"summary"}
_ADDRESS_CANDIDATE_KEYS = {"rank", "label", "street", "city", "state", "zipcode", "normalized_address", "source", "confidence", "evidence_text"}
_ADDRESS_CANDIDATE_REQUIRED_KEYS = {"rank", "label"}
_SOURCE_REF_KEYS = {"attachment", "page"}
_SOURCE_REF_REQUIRED_KEYS = {"attachment"}


@dataclass(frozen=True)
class JsonPromptResult:
    raw_output: str
    parsed_payload: dict[str, Any]
    deployment_name: str
    api_version: str
    request_parameters: dict[str, Any]
    raw_usage: dict[str, Any]
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    cached_prompt_tokens: int | None
    reasoning_tokens: int | None
    latency_ms: int

    def __iter__(self):
        yield self.raw_output
        yield self.parsed_payload


def _response_payload(response_body: str) -> dict[str, Any]:
    try:
        response_payload = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise AzureOpenAIExtractionError("Azure OpenAI response was not JSON", raw_response=response_body) from exc
    if not isinstance(response_payload, dict):
        raise AzureOpenAIExtractionError("Azure OpenAI response was JSON, but not an object", raw_response=response_body)
    return response_payload


def _message_content(response_payload: dict[str, Any]) -> str:
    choices = response_payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise AzureOpenAIExtractionError("Azure OpenAI response did not include choices", raw_response=json.dumps(response_payload))
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise AzureOpenAIExtractionError("Azure OpenAI response did not include message content", raw_response=json.dumps(response_payload))
    return content.strip()


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _nested_int_or_none(payload: dict[str, Any], path: tuple[str, str]) -> int | None:
    parent = payload.get(path[0])
    if not isinstance(parent, dict):
        return None
    return _int_or_none(parent.get(path[1]))


def _required_config(value: str | None, name: str) -> str:
    if value and value.strip():
        return value.strip()
    raise AzureOpenAIExtractionError(f"{name} is required for Azure OpenAI extraction.")
