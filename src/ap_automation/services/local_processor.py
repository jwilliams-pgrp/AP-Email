from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Callable

from ap_automation.agents.property_match_assistant import CachedPropertyMatchReviewer, PropertyMatchAssistant
from ap_automation.models.decision import Decision, Destination, WorkflowRule
from ap_automation.models.extraction import DocumentItem, ExcludedAttachment, ExtractionValidationError, validate_extraction_batch, validate_extraction_triage_batch
from ap_automation.repositories.protocols import OperationalRepository, PolicyRepository
from ap_automation.services.azure_openai_extractor import (
    AzureOpenAIExtractionError,
    AzureOpenAIExtractor,
    ExtractionAttempt,
    contract_repair_prompt,
    lint_extraction_contract,
)
from ap_automation.services.decision_engine import DecisionContext, DecisionEngine
from ap_automation.services.document_extractor_selector import DocumentExtractorSelector, summarize_extractor_selection
from ap_automation.services.document_intelligence_attachment_analyzer import DocumentIntelligenceAttachmentAnalyzer, summarize_document_intelligence
from ap_automation.services.graph_mailbox import GraphMailboxClient, GraphMessageEnvelope
from ap_automation.services.local_artifacts import LocalArtifactStore, artifact_store_from_env
from ap_automation.services.msg_parser import ParsedMsg, parse_msg
from ap_automation.services.pdf_attachment_evaluator import PdfAttachmentEvaluator
from ap_automation.services.thread_context import latest_body_text
from ap_automation.services.teams_notifier import TeamsNotifier, TeamsReviewNotification


class _FixturePropertyMatchSuggestion:
    def __init__(self, asset_id: str) -> None:
        self.candidate_asset_ids = (asset_id,)
        self.confidence = 1.0
        self.reason = "Fixture property review selected the top deterministic candidate."


class _FixturePropertyMatchReviewer:
    """Test-fixture reviewer used only when an explicit extraction fixture is supplied."""

    def suggest(self, extraction, alias_mappings: list[dict[str, Any]]):
        if not alias_mappings:
            return None
        asset_id = alias_mappings[0].get("asset_id")
        return _FixturePropertyMatchSuggestion(str(asset_id)) if asset_id else None


class LocalProcessor:
    """Runs the LOCAL processing pipeline using Azure OpenAI or fixture extraction."""

    retry_delay_seconds = 30.0

    def __init__(
        self,
        project_root: Path,
        policy_repository: PolicyRepository,
        operational_repository: OperationalRepository,
        llm_extractor: AzureOpenAIExtractor | None = None,
        graph_mailbox: GraphMailboxClient | None = None,
        teams_notifier: TeamsNotifier | None = None,
        document_intelligence_analyzer: DocumentIntelligenceAttachmentAnalyzer | None = None,
        artifact_store: LocalArtifactStore | None = None,
    ) -> None:
        self._project_root = project_root
        self._artifacts = artifact_store or artifact_store_from_env(project_root)
        self._policy_repository = policy_repository
        self._operational_repository = operational_repository
        self._llm_extractor = llm_extractor or AzureOpenAIExtractor(project_root)
        self._graph_mailbox = graph_mailbox
        self._teams_notifier = teams_notifier
        self._pdf_evaluator = PdfAttachmentEvaluator(project_root)
        self._extractor_selector = DocumentExtractorSelector()
        self._document_intelligence_analyzer = document_intelligence_analyzer or DocumentIntelligenceAttachmentAnalyzer(project_root, artifact_store=self._artifacts)
        self._current_run_id: str | None = None
        self._current_run_marked_failed = False

    def process_fixture(self, source_email_path: Path, extraction_fixture_path: Path) -> str:
        return self._process_with_retry(
            lambda: self._process(source_email_path, extraction_fixture_path),
            {
                "source_system": "local_file",
                "source_path": _relative(source_email_path),
                "fixture_path": _relative(extraction_fixture_path),
            },
        )

    def process_email(self, source_email_path: Path) -> str:
        return self._process_with_retry(
            lambda: self._process(source_email_path, None),
            {"source_system": "local_file", "source_path": _relative(source_email_path)},
        )

    def process_graph_email(self, envelope: GraphMessageEnvelope, extraction_fixture_path: Path | None = None) -> str:
        return self._process_with_retry(
            lambda: self._process(
                source_email_path=None,
                extraction_fixture_path=extraction_fixture_path,
                parsed_msg_override=envelope.parsed_msg,
                source_system_override="graph_mailbox",
                source_message_id_override=envelope.message_id,
                graph_categories=envelope.categories,
                internet_message_id=envelope.internet_message_id,
                office_web_link=envelope.web_link,
            ),
            {
                "source_system": "graph_mailbox",
                "source_message_id": envelope.message_id,
                "internet_message_id": envelope.internet_message_id,
                "fixture_path": _relative(extraction_fixture_path),
            },
        )

    def _process_with_retry(self, operation: Callable[[], str], context: dict[str, Any]) -> str:
        last_exc: Exception | None = None
        for attempt in (1, 2):
            self._current_run_id = None
            self._current_run_marked_failed = False
            try:
                return operation()
            except Exception as exc:
                last_exc = exc
                self._log_processing_failure_attempt(attempt, exc, context)
                if attempt == 1:
                    time.sleep(self.retry_delay_seconds)
                    continue
                self._mark_current_run_failed("PROCESSING", str(exc))
                raise
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("Processing retry loop exited without a result or exception.")

    def _log_processing_failure_attempt(self, attempt: int, exc: Exception, context: dict[str, Any]) -> None:
        payload = {
            "event": "processing_attempt_failed",
            "attempt": attempt,
            "max_attempts": 2,
            "retry_delay_seconds": self.retry_delay_seconds if attempt == 1 else None,
            "exception_type": exc.__class__.__name__,
            "error": str(exc),
            **{key: value for key, value in context.items() if value is not None},
        }
        print(json.dumps(payload, sort_keys=True), flush=True)

    def _mark_current_run_failed(self, failed_step: str, error: str) -> None:
        if not self._current_run_id or self._current_run_marked_failed:
            return
        try:
            trace_path = self._fail_run(self._current_run_id, failed_step, error)
            self._operational_repository.add_audit_step(
                self._current_run_id,
                "FINALIZE",
                {"run_id": self._current_run_id},
                {"trace_artifact_path": trace_path, "final_outcome": None, "status": "failed"},
                reason="Processing failed after retry.",
                error=error,
            )
        except Exception as finalize_exc:
            print(
                json.dumps(
                    {
                        "event": "processing_failure_finalize_failed",
                        "run_id": self._current_run_id,
                        "exception_type": finalize_exc.__class__.__name__,
                        "error": str(finalize_exc),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )

    def _extraction_attempt(
        self,
        source_email_path: Path,
        extraction_fixture_path: Path | None,
        fixture_payload: dict[str, Any],
        parsed_msg: ParsedMsg | None,
        attachment_records: list[dict[str, Any]],
        asset_reference_rows: list[dict[str, Any]] | None = None,
    ) -> ExtractionAttempt:
        if extraction_fixture_path:
            extractor = fixture_payload.get("extractor", {})
            return ExtractionAttempt(
                parsed_payload=fixture_payload,
                prompt=None,
                raw_response=json.dumps(fixture_payload, sort_keys=True),
                extractor_type=str(extractor.get("type") or "fixture"),
                model=extractor.get("model") if isinstance(extractor.get("model"), str) else None,
                prompt_version=extractor.get("prompt_version") if isinstance(extractor.get("prompt_version"), str) else None,
            )
        if parsed_msg is None:
            raise ValueError("Azure OpenAI extraction without a fixture currently requires a .msg source email.")
        triage_attempt = self._triage_attempt(parsed_msg, attachment_records)
        try:
            triage_batch = validate_extraction_triage_batch(triage_attempt.parsed_payload)
        except ExtractionValidationError as exc:
            if triage_attempt.attempts:
                raise
            repair_prompt = contract_repair_prompt(
                original_prompt=triage_attempt.prompt or "",
                invalid_response=triage_attempt.raw_response,
                errors=exc.errors,
                contract_name="extraction_triage_batch.v1",
            )
            result = self._llm_extractor.run_json_prompt(repair_prompt)
            raw_output, parsed_payload = result
            retry_attempt = ExtractionAttempt(
                parsed_payload=parsed_payload,
                prompt=triage_attempt.prompt,
                raw_response=raw_output,
                extractor_type=triage_attempt.extractor_type,
                model=triage_attempt.model,
                prompt_version=triage_attempt.prompt_version,
                deployment_name=getattr(result, "deployment_name", triage_attempt.deployment_name),
                api_version=getattr(result, "api_version", triage_attempt.api_version),
                request_parameters=getattr(result, "request_parameters", triage_attempt.request_parameters),
                raw_usage=getattr(result, "raw_usage", triage_attempt.raw_usage),
                prompt_tokens=getattr(result, "prompt_tokens", triage_attempt.prompt_tokens),
                completion_tokens=getattr(result, "completion_tokens", triage_attempt.completion_tokens),
                total_tokens=getattr(result, "total_tokens", triage_attempt.total_tokens),
                cached_prompt_tokens=getattr(result, "cached_prompt_tokens", triage_attempt.cached_prompt_tokens),
                reasoning_tokens=getattr(result, "reasoning_tokens", triage_attempt.reasoning_tokens),
                latency_ms=getattr(result, "latency_ms", triage_attempt.latency_ms),
                attempts=(
                    {
                        "attempt": 1,
                        "status": "validation_failed",
                        "validation_errors": exc.errors,
                        "raw_response": triage_attempt.raw_response,
                        "parsed_output": triage_attempt.parsed_payload,
                    },
                    {
                        "attempt": 2,
                        "status": "retry_response",
                        "retry_reason": "triage_schema_validation_failed",
                        "raw_response": raw_output,
                        "parsed_output": parsed_payload,
                    },
                ),
            )
            triage_attempt = retry_attempt
            triage_batch = validate_extraction_triage_batch(triage_attempt.parsed_payload)
        detail_attempt = self._llm_extractor.extract_msg_with_triage(
            parsed_msg,
            attachment_records,
            triage_batch,
            asset_reference_rows=asset_reference_rows,
        )
        return _with_triage_audit(detail_attempt, triage_attempt)

    def _triage_attempt(
        self,
        parsed_msg: ParsedMsg,
        attachment_records: list[dict[str, Any]],
    ) -> ExtractionAttempt:
        return self._llm_extractor.triage_msg(parsed_msg, attachment_records)

    def _retry_extraction_contract(
        self,
        extraction_attempt: ExtractionAttempt,
        errors: list[str],
        lint_findings: dict[str, Any] | None = None,
    ) -> ExtractionAttempt:
        if extraction_attempt.extractor_type != "azure_openai" or not extraction_attempt.prompt:
            return extraction_attempt
        repair_prompt = contract_repair_prompt(
            original_prompt=extraction_attempt.prompt,
            invalid_response=extraction_attempt.raw_response,
            errors=errors,
            contract_name="extraction_batch.v1",
            lint_findings=lint_findings,
        )
        result = self._llm_extractor.run_json_prompt(repair_prompt)
        raw_output, parsed_payload = result
        retry_changed_payload = not _json_equivalent(extraction_attempt.parsed_payload, parsed_payload)
        attempts = extraction_attempt.attempts
        if not attempts:
            attempts = (
                {
                    "attempt": 1,
                    "status": "validation_failed",
                    "validation_errors": errors,
                    "contract_lint": lint_findings or {},
                    "raw_response": extraction_attempt.raw_response,
                    "parsed_output": extraction_attempt.parsed_payload,
                },
            )
        attempts = attempts + (
            {
                "attempt": len(extraction_attempt.attempts) + 2,
                "status": "retry_response",
                "retry_reason": "schema_validation_failed",
                "validation_errors": errors,
                "contract_lint": lint_findings or {},
                "changed_payload": retry_changed_payload,
                "raw_response": raw_output,
                "parsed_output": parsed_payload,
            },
        )
        return ExtractionAttempt(
            parsed_payload=parsed_payload,
            prompt=extraction_attempt.prompt,
            raw_response=raw_output,
            extractor_type=extraction_attempt.extractor_type,
            model=extraction_attempt.model,
            prompt_version=extraction_attempt.prompt_version,
            deployment_name=extraction_attempt.deployment_name,
            api_version=extraction_attempt.api_version,
            request_parameters=getattr(result, "request_parameters", extraction_attempt.request_parameters),
            raw_usage=getattr(result, "raw_usage", extraction_attempt.raw_usage),
            prompt_tokens=getattr(result, "prompt_tokens", extraction_attempt.prompt_tokens),
            completion_tokens=getattr(result, "completion_tokens", extraction_attempt.completion_tokens),
            total_tokens=getattr(result, "total_tokens", extraction_attempt.total_tokens),
            cached_prompt_tokens=getattr(result, "cached_prompt_tokens", extraction_attempt.cached_prompt_tokens),
            reasoning_tokens=getattr(result, "reasoning_tokens", extraction_attempt.reasoning_tokens),
            latency_ms=getattr(result, "latency_ms", extraction_attempt.latency_ms),
            attempts=attempts,
            triage=extraction_attempt.triage,
        )

    def _process(
        self,
        source_email_path: Path | None,
        extraction_fixture_path: Path | None,
        parsed_msg_override: ParsedMsg | None = None,
        source_system_override: str | None = None,
        source_message_id_override: str | None = None,
        graph_categories: tuple[str, ...] = (),
        internet_message_id: str | None = None,
        office_web_link: str | None = None,
    ) -> str:
        self._artifacts.ensure_directories()
        print(
            json.dumps(
                {
                    "event": "pdf_evaluator_status",
                    "evaluation_version": self._pdf_evaluator.evaluation_version,
                    "dependency_available": self._pdf_evaluator.dependency_status.available,
                    "dependency_detail": self._pdf_evaluator.dependency_status.detail,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        source_hash = _sha256_file(source_email_path) if source_email_path else (hashlib.sha256((internet_message_id or source_message_id_override or "").encode("utf-8")).hexdigest() if (internet_message_id or source_message_id_override) else None)
        graph_stable_key = internet_message_id or source_message_id_override
        idempotency_key = (
            f"{source_system_override}:{graph_stable_key}"
            if source_system_override and graph_stable_key
            else f"local_file:{source_hash}"
        )

        parsed_msg = parsed_msg_override or (_parse_source_email(source_email_path) if source_email_path else None)
        fixture_payload = _fixture_payload(extraction_fixture_path)
        email_metadata = _email_metadata(
            source_email_path,
            idempotency_key,
            source_hash,
            fixture_payload,
            parsed_msg,
            source_system_override=source_system_override,
            source_message_id_override=source_message_id_override,
            office_web_link=office_web_link,
        )
        email_id = self._operational_repository.upsert_email(
            email_metadata
        )
        run_id = self._operational_repository.create_audit_run(email_id, {"mode": "LOCAL"})
        self._current_run_id = run_id
        self._operational_repository.add_audit_step(
            run_id,
            "INGESTION",
            {"source_path": _relative(source_email_path) if source_email_path else None, "source_sha256": source_hash},
            {
                "email_id": email_id,
                "idempotency_key": idempotency_key,
                "subject": email_metadata.get("subject"),
                "sender_email": email_metadata.get("sender_email"),
                "received_at": email_metadata.get("received_at"),
                "parser": email_metadata.get("metadata", {}).get("parser"),
            },
        )

        attachment_records = []
        html_storage_path: str | None = None
        if parsed_msg:
            attachment_records = self._artifacts.write_attachments(email_id, parsed_msg.attachments)
            for record in attachment_records:
                record["sha256"] = _sha256_bytes(self._artifacts.read_bytes(record["storage_path"]))
            self._evaluate_attachment_records(attachment_records)
            html_storage_path = self._artifacts.write_email_html_preview(
                email_id=email_id,
                subject=parsed_msg.subject,
                sender_name=parsed_msg.sender_name,
                sender_email=parsed_msg.sender_email,
                received_at=parsed_msg.received_at.isoformat() if parsed_msg.received_at else None,
                body_text=parsed_msg.body_text,
                body_html=parsed_msg.body_html,
                attachments=parsed_msg.attachments,
            )
            self._operational_repository.update_email_html_storage_path(email_id, html_storage_path)
        self._operational_repository.add_audit_step(
            run_id,
            "ATTACHMENT_PROCESSING",
            {"source_path": _relative(source_email_path) if source_email_path else None},
            {
                "mode": "local_msg" if parsed_msg else "no_msg_parser",
                "attachments_extracted": len(attachment_records),
                "business_attachments_extracted": len(_business_attachment_records(attachment_records)),
                "html_storage_path": html_storage_path,
                "pdf_evaluation_summary": _pdf_evaluation_summary(attachment_records),
                "pdf_evaluation_version": self._pdf_evaluator.evaluation_version,
                "pdf_dependency": {
                    "available": self._pdf_evaluator.dependency_status.available,
                    "detail": self._pdf_evaluator.dependency_status.detail,
                },
                "attachments": [
                    {
                        "file_name": record["file_name"],
                        "storage_path": record["storage_path"],
                        "sha256": record["sha256"],
                        "file_size_bytes": record["file_size_bytes"],
                        "is_inline": _is_inline_attachment_record(record),
                        "pdf_evaluation": record.get("metadata", {}).get("pdf_evaluation"),
                    }
                    for record in attachment_records
                ],
            },
            reason="Local MSG attachments extracted to filesystem artifacts for downstream processing."
            if parsed_msg
            else "Source was not an MSG file; no local attachment extraction was attempted.",
        )

        if parsed_msg:
            self._select_attachment_extractors(attachment_records)
        self._operational_repository.add_audit_step(
            run_id,
            "DOCUMENT_EXTRACTION_SELECTION",
            {"source_path": _relative(source_email_path) if source_email_path else None},
            {
                **summarize_extractor_selection(attachment_records),
                "attachments": [
                    {
                        "file_name": record["file_name"],
                        "storage_path": record["storage_path"],
                        "is_inline": _is_inline_attachment_record(record),
                        "extractor_selection": record.get("metadata", {}).get("extractor_selection"),
                    }
                    for record in attachment_records
                ],
            },
            reason="Attachment extractors selected before LLM extraction."
            if parsed_msg
            else "Source was not parsed as an MSG; no attachment extractor selection was attempted.",
        )

        try:
            if parsed_msg:
                self._analyze_attachment_records_with_document_intelligence(
                    _document_intelligence_selected_records(attachment_records),
                    run_id=run_id,
                    require_config=extraction_fixture_path is None,
                )
                self._operational_repository.save_attachments(email_id, attachment_records)
            self._operational_repository.add_audit_step(
                run_id,
                "DOCUMENT_INTELLIGENCE",
                {"source_path": _relative(source_email_path) if source_email_path else None},
                {
                    **summarize_document_intelligence(attachment_records),
                    "analysis_version": self._document_intelligence_analyzer.analysis_version,
                    "dependency": {
                        "available": self._document_intelligence_analyzer.dependency_status.available,
                        "detail": self._document_intelligence_analyzer.dependency_status.detail,
                    },
                },
                reason="Azure Document Intelligence attachment analysis completed before LLM extraction."
                if parsed_msg
                else "Source was not parsed as an MSG; no attachment analysis was attempted.",
            )
        except Exception as exc:
            self._operational_repository.add_audit_step(
                run_id,
                "DOCUMENT_INTELLIGENCE",
                {"source_path": _relative(source_email_path) if source_email_path else None},
                {
                    **summarize_document_intelligence(attachment_records),
                    "analysis_version": self._document_intelligence_analyzer.analysis_version,
                    "dependency": {
                        "available": self._document_intelligence_analyzer.dependency_status.available,
                        "detail": self._document_intelligence_analyzer.dependency_status.detail,
                    },
                },
                reason="Azure Document Intelligence attachment analysis failed before LLM extraction.",
                error=str(exc),
            )
            trace_path = self._fail_run(run_id, "DOCUMENT_INTELLIGENCE", str(exc))
            self._operational_repository.add_audit_step(
                run_id,
                "FINALIZE",
                {"run_id": run_id},
                {"trace_artifact_path": trace_path, "final_outcome": None, "status": "failed"},
                reason="Local processing failed during Document Intelligence attachment analysis.",
                error=str(exc),
            )
            raise

        extraction_input = (
            {"fixture_path": _relative(extraction_fixture_path)}
            if extraction_fixture_path
            else {"source_path": _relative(source_email_path) if source_email_path else None}
        )
        try:
            asset_reference_rows = None if extraction_fixture_path else self._policy_repository.get_asset_reference_rows()
            extraction_attempt = self._extraction_attempt(
                source_email_path or Path("graph://intake"),
                extraction_fixture_path,
                fixture_payload,
                parsed_msg,
                _business_attachment_records(attachment_records),
                asset_reference_rows=asset_reference_rows,
            )
        except AzureOpenAIExtractionError as exc:
            audit_payload = {
                "extractor_type": "azure_openai",
                "prompt_version": "azure_msg_extraction.v1",
                "llm_input": exc.prompt,
                "llm_output": exc.raw_response,
                "error": str(exc),
            }
            prompt_path = self._artifacts.write_prompt_snapshot(run_id, exc.prompt) if exc.prompt else None
            persisted_audit_payload = self._operational_repository.save_extraction(email_id, None, audit_payload, [str(exc)], audit_payload)
            extraction_snapshot_path = self._artifacts.write_extraction_snapshot(run_id, persisted_audit_payload)
            self._operational_repository.add_audit_step(
                run_id,
                "LLM_EXTRACTION",
                {**extraction_input, "prompt_artifact_path": prompt_path, "rendered_prompt": exc.prompt},
                {
                    "artifact_path": extraction_snapshot_path,
                    "extractor_type": "azure_openai",
                    "raw_response": exc.raw_response,
                },
                reason="Azure OpenAI extraction failed before schema validation.",
                error=str(exc),
            )
            self._save_azure_openai_interaction(
                email_id=email_id,
                run_id=run_id,
                prompt_path=prompt_path,
                response_path=extraction_snapshot_path,
                model=None,
                deployment_name=None,
                api_version=None,
                prompt_version="azure_msg_extraction.v1",
                request_parameters={"response_format": {"type": "json_object"}},
                raw_usage={},
                prompt_tokens=None,
                completion_tokens=None,
                total_tokens=None,
                cached_prompt_tokens=None,
                reasoning_tokens=None,
                latency_ms=None,
                status="error",
                error=str(exc),
            )
            trace_path = self._fail_run(run_id, "LLM_EXTRACTION", str(exc))
            self._operational_repository.add_audit_step(
                run_id,
                "FINALIZE",
                {"run_id": run_id},
                {"trace_artifact_path": trace_path, "final_outcome": None, "status": "failed"},
                reason="Local processing failed during LLM extraction.",
                error=str(exc),
            )
            raise

        business_attachment_records = _business_attachment_records(attachment_records)
        prompt_path = self._artifacts.write_prompt_snapshot(run_id, extraction_attempt.prompt) if extraction_attempt.prompt else None
        self._operational_repository.add_audit_step(
            run_id,
            "LLM_EXTRACTION",
            {**extraction_input, "prompt_artifact_path": prompt_path, "rendered_prompt": extraction_attempt.prompt},
            {
                "artifact_path": str(Path("local/audit/extractions") / f"{run_id}.json").replace("\\", "/"),
                "extractor_type": extraction_attempt.extractor_type,
                "model": extraction_attempt.model,
                "prompt_version": extraction_attempt.prompt_version,
                "raw_response": extraction_attempt.raw_response,
            },
            reason="Fixture extraction used for local development."
            if extraction_fixture_path
            else "Azure OpenAI extraction used for local development.",
        )

        raw_payload = extraction_attempt.parsed_payload
        _remove_inline_source_attachments(raw_payload, attachment_records)
        self._apply_deterministic_observed_fact_overrides(raw_payload, parsed_msg, business_attachment_records)
        _apply_triage_risk_overrides(raw_payload, extraction_attempt.triage)
        excluded_attachment_normalization = _restore_cited_excluded_attachments(raw_payload)
        normalization = _normalize_azure_extraction_payload(raw_payload) if extraction_attempt.extractor_type == "azure_openai" else {}
        if excluded_attachment_normalization:
            normalization = {**normalization, "excluded_attachment_conflicts": excluded_attachment_normalization}
        initial_validation_errors: list[str] = []
        repair_validation_errors: list[str] = []
        contract_lint = lint_extraction_contract(raw_payload) if extraction_attempt.extractor_type == "azure_openai" else {}
        validation_status = "valid"
        try:
            batch = validate_extraction_batch(raw_payload)
            if normalization:
                validation_status = "valid_after_normalization"
        except ExtractionValidationError as exc:
            initial_validation_errors = list(exc.errors)
            if extraction_attempt.extractor_type == "azure_openai" and not extraction_attempt.attempts:
                try:
                    retry_attempt = self._retry_extraction_contract(extraction_attempt, exc.errors, contract_lint)
                    retry_payload = retry_attempt.parsed_payload
                    _remove_inline_source_attachments(retry_payload, attachment_records)
                    self._apply_deterministic_observed_fact_overrides(retry_payload, parsed_msg, business_attachment_records)
                    _apply_triage_risk_overrides(retry_payload, retry_attempt.triage)
                    retry_excluded_attachment_normalization = _restore_cited_excluded_attachments(retry_payload)
                    retry_normalization = _normalize_azure_extraction_payload(retry_payload)
                    if retry_excluded_attachment_normalization:
                        retry_normalization = {
                            **retry_normalization,
                            "excluded_attachment_conflicts": retry_excluded_attachment_normalization,
                        }
                    batch = validate_extraction_batch(retry_payload)
                    extraction_attempt = retry_attempt
                    raw_payload = retry_payload
                    normalization = retry_normalization
                    validation_status = "valid_after_retry_and_normalization" if normalization else "valid_after_retry"
                except ExtractionValidationError as retry_exc:
                    repair_validation_errors = list(retry_exc.errors)
                    extraction_attempt = retry_attempt
                    raw_payload = retry_attempt.parsed_payload
                    exc = retry_exc
                except AzureOpenAIExtractionError as retry_exc:
                    repair_validation_errors = [str(retry_exc)]
                    exc = ExtractionValidationError([str(retry_exc)])
            if "batch" not in locals():
                audit_payload = extraction_attempt.audit_payload()
                audit_payload["initial_validation_errors"] = initial_validation_errors
                audit_payload["repair_validation_errors"] = repair_validation_errors
                audit_payload["contract_lint"] = contract_lint
                persisted_audit_payload = self._operational_repository.save_extraction(email_id, None, raw_payload, exc.errors, audit_payload)
                extraction_snapshot_path = self._artifacts.write_extraction_snapshot(run_id, persisted_audit_payload)
                self._save_azure_openai_interaction(
                    email_id=email_id,
                    run_id=run_id,
                    prompt_path=prompt_path,
                    response_path=extraction_snapshot_path,
                    model=extraction_attempt.model,
                    deployment_name=extraction_attempt.deployment_name,
                    api_version=extraction_attempt.api_version,
                    prompt_version=extraction_attempt.prompt_version,
                    request_parameters=extraction_attempt.request_parameters,
                    raw_usage=extraction_attempt.raw_usage,
                    prompt_tokens=extraction_attempt.prompt_tokens,
                    completion_tokens=extraction_attempt.completion_tokens,
                    total_tokens=extraction_attempt.total_tokens,
                    cached_prompt_tokens=extraction_attempt.cached_prompt_tokens,
                    reasoning_tokens=extraction_attempt.reasoning_tokens,
                    latency_ms=extraction_attempt.latency_ms,
                    status="error",
                    error=str(exc),
                    enabled=extraction_attempt.extractor_type == "azure_openai",
                )
                self._operational_repository.add_audit_step(
                    run_id,
                    "VALIDATION",
                    {"artifact_path": extraction_snapshot_path},
                    {
                        "validation_status": "invalid",
                        "errors": exc.errors,
                        "initial_validation_errors": initial_validation_errors,
                        "repair_validation_errors": repair_validation_errors,
                        "unknown_keys": contract_lint.get("unknown_keys", []),
                        "retry_count": len(extraction_attempt.attempts),
                    },
                    reason="Invalid extraction payload -> ESCALATE",
                    error=str(exc),
                )
                trace_path = self._fail_run(run_id, "VALIDATION", str(exc))
                self._operational_repository.add_audit_step(
                    run_id,
                    "FINALIZE",
                    {"run_id": run_id},
                    {"trace_artifact_path": trace_path, "final_outcome": None, "status": "failed"},
                    reason="Local processing failed during extraction validation.",
                    error=str(exc),
                )
                raise

        audit_payload = extraction_attempt.audit_payload()
        if normalization:
            audit_payload["normalization"] = normalization
        if contract_lint:
            audit_payload["contract_lint"] = contract_lint
        if initial_validation_errors:
            audit_payload["initial_validation_errors"] = initial_validation_errors
        not_selected_attachments = _not_selected_business_attachments(
            business_attachment_records,
            batch.items,
            batch.excluded_attachments,
        )
        persisted_audit_payload = dict(audit_payload)
        persisted_audit_payload["validated_items"] = len(batch.items)
        persisted_audit_payload["not_selected_attachments"] = not_selected_attachments
        extraction_snapshot_path = self._artifacts.write_extraction_snapshot(run_id, persisted_audit_payload)
        self._operational_repository.add_audit_step(
            run_id,
            "VALIDATION",
            {"artifact_path": extraction_snapshot_path},
            {
                "validation_status": validation_status,
                "schema_version": batch.schema_version,
                "item_count": len(batch.items),
                "not_selected_attachments": not_selected_attachments,
                "retry_count": max(0, len(extraction_attempt.attempts) - 1),
                "normalization": normalization,
                "initial_validation_errors": initial_validation_errors,
                "repair_validation_errors": repair_validation_errors,
                "unknown_keys": contract_lint.get("unknown_keys", []),
            },
            confidence=min(item.extraction.confidence.overall for item in batch.items),
        )
        property_evaluations = {
            item.item_key: self._policy_repository.evaluate_property_match(item.extraction)
            for item in batch.items
        }
        if extraction_fixture_path:
            property_match_reviewer = _FixturePropertyMatchReviewer()
        else:
            property_match_assistant = PropertyMatchAssistant(self._llm_extractor)
            property_match_suggestions = property_match_assistant.suggest_batch(
                [
                    (
                        item.item_key,
                        item.extraction,
                        [candidate.to_audit_dict() for candidate in property_evaluations[item.item_key].candidates],
                    )
                    for item in batch.items
                ]
            )
            property_match_reviewer = CachedPropertyMatchReviewer(
                suggestions_by_item_key=property_match_suggestions,
                item_key_by_extraction_id={id(item.extraction): item.item_key for item in batch.items},
            )
        engine = DecisionEngine(self._policy_repository, property_match_reviewer=property_match_reviewer)
        item_results: list[dict[str, Any]] = []
        decision_context = _decision_context_from_parsed_msg(parsed_msg)
        for item in batch.items:
            document_item_id = self._operational_repository.save_document_item(
                email_id,
                item.item_kind,
                item.item_key,
                item.display_name,
                item.metadata,
                item.attachment_id,
            )
            persisted_item_payload = self._operational_repository.save_extraction(
                email_id,
                item.extraction,
                item.extraction.raw,
                [],
                audit_payload,
                document_item_id=document_item_id,
            )
            self._operational_repository.save_invoice_fact(email_id, item.extraction, document_item_id=document_item_id)
            result = engine.decide(
                item.extraction,
                idempotency_key,
                _pre_decision_facts(business_attachment_records, item.extraction.raw),
                property_match_evaluation=property_evaluations[item.item_key],
                decision_context=decision_context,
            )
            item_decision_id = self._operational_repository.save_decision(email_id, run_id, result.decision, document_item_id=document_item_id)
            item_results.append(
                {
                    "item": item,
                    "document_item_id": document_item_id,
                    "decision_id": item_decision_id,
                    "decision": result.decision,
                    "evaluations": result.evaluations,
                    "persisted_payload": persisted_item_payload,
                }
            )
        final_decision = _aggregate_item_decisions(
            [entry["decision"] for entry in item_results],
            self._policy_repository.get_active_workflow_rules(),
            self._policy_repository.get_runtime_config(),
            self._policy_repository.get_destination,
            [
                {
                    "item_kind": entry["item"].item_kind,
                    "item_key": entry["item"].item_key,
                    "display_name": entry["item"].display_name,
                }
                for entry in item_results
            ],
        )
        persisted_audit_payload["items"] = [
            {
                "document_item_id": entry["document_item_id"],
                "item_key": entry["item"].item_key,
                "property_lookup": entry["decision"].routing_match.get("property_lookup"),
                "decision": entry["decision"].__dict__,
            }
            for entry in item_results
        ]
        if len(item_results) == 1:
            persisted_audit_payload["property_lookup"] = item_results[0]["decision"].routing_match.get("property_lookup")
        extraction_snapshot_path = self._artifacts.write_extraction_snapshot(run_id, persisted_audit_payload)
        self._save_azure_openai_interaction(
            email_id=email_id,
            run_id=run_id,
            prompt_path=prompt_path,
            response_path=extraction_snapshot_path,
            model=extraction_attempt.model,
            deployment_name=extraction_attempt.deployment_name,
            api_version=extraction_attempt.api_version,
            prompt_version=extraction_attempt.prompt_version,
            request_parameters=extraction_attempt.request_parameters,
            raw_usage=extraction_attempt.raw_usage,
            prompt_tokens=extraction_attempt.prompt_tokens,
            completion_tokens=extraction_attempt.completion_tokens,
            total_tokens=extraction_attempt.total_tokens,
            cached_prompt_tokens=extraction_attempt.cached_prompt_tokens,
            reasoning_tokens=extraction_attempt.reasoning_tokens,
            latency_ms=extraction_attempt.latency_ms,
            status="completed",
            error=None,
            enabled=extraction_attempt.extractor_type == "azure_openai",
        )
        self._operational_repository.add_audit_step(
            run_id,
            "DUPLICATE_CHECK",
            {"item_count": len(item_results)},
            {"duplicate_statuses": [entry["decision"].routing_match.get("duplicate_status") for entry in item_results]},
        )
        self._operational_repository.add_audit_step(
            run_id,
            "ROUTING_MATCH",
            {"item_count": len(item_results)},
            final_decision.routing_match,
        )
        self._operational_repository.add_audit_step(
            run_id,
            "RULE_EVALUATION",
            {"item_count": len(item_results)},
            {
                "evaluations": [
                    {
                        "item_key": entry["item"].item_key,
                        "evaluations": [evaluation.__dict__ for evaluation in entry["evaluations"]],
                    }
                    for entry in item_results
                ]
            },
            reason=final_decision.reason,
        )
        decision_id = self._operational_repository.save_decision(email_id, run_id, final_decision)
        self._operational_repository.add_audit_step(
            run_id,
            "DECISION",
            final_decision.extracted_fields,
            {"outcome": final_decision.outcome, "destination_code": final_decision.destination_code},
            decision=final_decision.__dict__,
            reason=final_decision.reason,
            confidence=final_decision.confidence,
        )
        destination = self._policy_repository.get_destination(final_decision.destination_code) if final_decision.destination_code else None
        action_path = self._artifacts.write_action_plan(
            run_id,
            final_decision,
            destination,
            source_message_id_override,
        )
        self._operational_repository.save_action(email_id, decision_id, final_decision, action_path)
        if final_decision.outcome in {"ESCALATE", "FLAG"}:
            self._operational_repository.enqueue_escalate(email_id, decision_id, final_decision.reason)
        action_output: dict[str, Any] = {"action_plan_path": action_path}
        final_office_web_link = office_web_link
        if (
            self._graph_mailbox is not None
            and source_system_override == "graph_mailbox"
            and source_message_id_override
            and destination is not None
            and destination.parent_folder
        ):
            graph_result = self._graph_mailbox.route_message(
                message_id=source_message_id_override,
                existing_categories=graph_categories,
                parent_folder=destination.parent_folder,
                label=destination.label,
                destination_display_name=destination.display_name,
                destination_folder_path=None,
            )
            action_output["graph_result"] = graph_result
            routed_office_web_link = graph_result.get("office_web_link")
            if isinstance(routed_office_web_link, str) and routed_office_web_link:
                final_office_web_link = routed_office_web_link
                self._operational_repository.update_email_office_web_link(email_id, final_office_web_link)
        if destination is not None and destination.send_teams_message:
            teams_notifier = self._teams_notifier or TeamsNotifier.from_env()
            teams_result = teams_notifier.send_review_notification(
                TeamsReviewNotification(
                    email_subject=email_metadata.get("subject"),
                    routing_path=_routing_path(destination),
                    office_web_link=final_office_web_link,
                )
            )
            action_output["teams_notification"] = {"sent": True, **teams_result}
        self._operational_repository.add_audit_step(
            run_id,
            "ACTION",
            {"decision_id": decision_id},
            action_output,
            reason="Action plan created. Graph mailbox route executed when Graph config is enabled and destination has a parent_folder.",
        )
        trace_path = self._artifacts.write_trace(run_id, final_decision)
        self._operational_repository.finalize_audit_run(run_id, final_decision.outcome, trace_path)
        self._operational_repository.add_audit_step(
            run_id,
            "FINALIZE",
            {"run_id": run_id},
            {"trace_artifact_path": trace_path, "final_outcome": final_decision.outcome},
        )
        return run_id

    def _fail_run(self, run_id: str, failed_step: str, error: str) -> str:
        trace_path = self._artifacts.write_failure_trace(run_id, failed_step, error)
        self._operational_repository.fail_audit_run(run_id, error, trace_path)
        if run_id == self._current_run_id:
            self._current_run_marked_failed = True
        return trace_path

    def _save_azure_openai_interaction(
        self,
        *,
        email_id: str,
        run_id: str,
        prompt_path: str | None,
        response_path: str | None,
        model: str | None,
        deployment_name: str | None,
        api_version: str | None,
        prompt_version: str | None,
        request_parameters: dict[str, Any] | None,
        raw_usage: dict[str, Any] | None,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        total_tokens: int | None,
        cached_prompt_tokens: int | None,
        reasoning_tokens: int | None,
        latency_ms: int | None,
        status: str,
        error: str | None,
        enabled: bool = True,
    ) -> None:
        if not enabled:
            return
        self._operational_repository.save_llm_interaction(
            email_id,
            run_id,
            {
                "interaction_type": "extraction",
                "provider": "azure_openai",
                "model_name": model,
                "deployment_name": deployment_name or model,
                "api_version": api_version,
                "prompt_template_name": "azure_msg_extraction",
                "prompt_version": prompt_version,
                "prompt_artifact_path": prompt_path,
                "response_artifact_path": response_path,
                "request_parameters": request_parameters or {"response_format": {"type": "json_object"}},
                "raw_usage": raw_usage or {},
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "cached_prompt_tokens": cached_prompt_tokens,
                "reasoning_tokens": reasoning_tokens,
                "latency_ms": latency_ms,
                "status": status,
                "error": error,
            },
        )

    def _evaluate_attachment_records(self, attachment_records: list[dict[str, Any]]) -> None:
        evaluations = self._pdf_evaluator.evaluate_attachments(attachment_records)
        for record, evaluation in zip(attachment_records, evaluations):
            metadata = record.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
                record["metadata"] = metadata
            metadata["pdf_evaluation"] = evaluation
            if "text_excerpt" in record:
                del record["text_excerpt"]

    def _select_attachment_extractors(self, attachment_records: list[dict[str, Any]]) -> None:
        selections = self._extractor_selector.select_attachments(attachment_records)
        for record, selection in zip(attachment_records, selections):
            metadata = record.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
                record["metadata"] = metadata
            metadata["extractor_selection"] = selection
            if selection.get("selected_extractor") == "pymupdf":
                pdf_evaluation = metadata.get("pdf_evaluation")
                if isinstance(pdf_evaluation, dict) and isinstance(pdf_evaluation.get("text_excerpt"), str):
                    record["text_excerpt"] = pdf_evaluation["text_excerpt"]
                    continue
            if "text_excerpt" in record:
                del record["text_excerpt"]

    def _analyze_attachment_records_with_document_intelligence(
        self,
        attachment_records: list[dict[str, Any]],
        *,
        run_id: str,
        require_config: bool,
    ) -> None:
        analyses = self._document_intelligence_analyzer.analyze_attachments(
            attachment_records,
            run_id=run_id,
            require_config=require_config,
        )
        for record, analysis in zip(attachment_records, analyses):
            metadata = record.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
                record["metadata"] = metadata
            metadata["document_intelligence"] = analysis
            if analysis.get("status") == "success" and isinstance(analysis.get("text_excerpt"), str):
                record["text_excerpt"] = analysis["text_excerpt"]
            elif "text_excerpt" in record:
                del record["text_excerpt"]

    def _apply_deterministic_observed_fact_overrides(
        self,
        raw_payload: dict[str, Any],
        parsed_msg: ParsedMsg | None,
        attachment_records: list[dict[str, Any]],
    ) -> None:
        if raw_payload.get("schema_version") == "extraction_batch.v1" and isinstance(raw_payload.get("items"), list):
            for item in raw_payload["items"]:
                if isinstance(item, dict) and isinstance(item.get("extraction"), dict):
                    self._apply_deterministic_observed_fact_overrides(item["extraction"], parsed_msg, attachment_records)
            return
        document = raw_payload.get("document")
        observed = raw_payload.get("observed_facts")
        if not isinstance(document, dict) or not isinstance(observed, dict):
            return
        if _has_unreadable_pdf(attachment_records, raw_payload):
            observed["has_low_text_quality"] = True
        if parsed_msg is None:
            return
        if not bool(observed.get("indicates_vendor_question_or_payment_inquiry")) and _looks_like_vendor_question_or_payment_inquiry(
            parsed_msg.subject,
            latest_body_text(parsed_msg.metadata, parsed_msg.body_text),
        ):
            observed["indicates_vendor_question_or_payment_inquiry"] = True
        has_invoice_attachment = document.get("has_invoice_attachment")
        if has_invoice_attachment is not False:
            return
        if bool(observed.get("mentions_payment_link_only")):
            return
        if bool(document.get("link_only")):
            return
        if document.get("document_type") in {"payment_inquiry", "vendor_question"}:
            return
        if bool(observed.get("indicates_vendor_question_or_payment_inquiry")):
            return
        if _looks_like_payment_link_only_email(latest_body_text(parsed_msg.metadata, parsed_msg.body_text)):
            observed["mentions_payment_link_only"] = True
            document["link_only"] = True


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _looks_like_payment_link_only_email(body_text: str | None) -> bool:
    if not body_text:
        return False
    normalized = body_text.lower()
    has_link = ("http://" in normalized) or ("https://" in normalized)
    if not has_link:
        return False
    explicit_invoice_or_bill_actions = (
        "pay bill",
        "pay your bill",
        "pay invoice",
        "pay your invoice",
        "bill is due",
        "invoice is due",
        "bill is available",
        "invoice is available",
        "your bill is available",
        "your invoice is available",
        "electric service bill is available",
        "view bill",
        "view your bill",
        "view invoice",
        "view your invoice",
        "retrieve bill",
        "retrieve invoice",
        "download bill",
        "download invoice",
        "make a payment",
        "pay now",
        "amount due",
        "balance due",
    )
    if any(signal in normalized for signal in explicit_invoice_or_bill_actions):
        return True

    bill_detail_signals = (
        "bill",
        "invoice",
        "amount:",
        "due date:",
        "account:",
        "service location:",
        "balance due",
    )
    bill_detail_count = sum(1 for signal in bill_detail_signals if signal in normalized)
    has_portal_action = any(
        signal in normalized
        for signal in (
            "click here",
            "view",
            "pay",
            "payment",
            "portal",
            "sign in",
            "login",
            "log in",
        )
    )
    has_current_bill_context = any(signal in normalized for signal in ("bill", "invoice", "amount:", "due date:", "balance due"))
    return bill_detail_count >= 2 and has_portal_action and has_current_bill_context


def _looks_like_vendor_question_or_payment_inquiry(subject: str | None, body_text: str | None) -> bool:
    text = _normalize_message_text(" ".join(value for value in (subject, body_text) if value))
    if not text:
        return False

    has_context = any(
        signal in text
        for signal in (
            "invoice",
            "payment",
            "paid",
            "pay ",
            "ach",
            "remittance",
            "account",
            "balance",
            "credit",
            "dispute",
            "backup",
            "support",
            "open invoice",
            "duplicate",
        )
    ) or re.search(r"\binv(?:oice)?[\s#:.-]*[a-z0-9-]+\b", text) is not None
    if not has_context:
        return False

    response_or_research_patterns = (
        r"\bcan you (?:please )?confirm\b",
        r"\bcould you (?:please )?confirm\b",
        r"\bplease confirm\b",
        r"\bplease advise\b",
        r"\bconfirm (?:which|what|whether|if|that)\b",
        r"\bwhich invoice\b",
        r"\bwhat invoice\b",
        r"\bmissing remittance\b",
        r"\bno remittance\b",
        r"\bwithout remittance\b",
        r"\bremittance (?:detail|details|advice|support)\b",
        r"\bmatch(?:ed|ing)? (?:this )?payment\b",
        r"\bpayment (?:match|matches|matched|for invoice|to invoice)\b",
        r"\bopen invoices?\b",
        r"\breconcil(?:e|iation|ing)\b",
        r"\bresearch\b",
        r"\bexplain\b",
        r"\bdispute[ds]?\b",
        r"\bcredit(?: memo)?\b",
        r"\bmissing (?:backup|support)\b",
        r"\bduplicate payments?\b",
        r"\bpaid twice\b",
        r"\bdouble paid\b",
    )
    return any(re.search(pattern, text) is not None for pattern in response_or_research_patterns)


def _normalize_message_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower()).strip()


def _parse_source_email(path: Path) -> ParsedMsg | None:
    if path.suffix.lower() != ".msg":
        return None
    return parse_msg(path)


def _fixture_payload(extraction_fixture_path: Path | None) -> dict[str, Any]:
    if extraction_fixture_path:
        return json.loads(extraction_fixture_path.read_text(encoding="utf-8"))
    return {}


def _email_metadata(
    source_email_path: Path | None,
    idempotency_key: str,
    source_hash: str | None,
    raw_payload: dict[str, Any],
    parsed_msg: ParsedMsg | None,
    source_system_override: str | None = None,
    source_message_id_override: str | None = None,
    office_web_link: str | None = None,
) -> dict[str, Any]:
    fixture_email = raw_payload.get("email", {})
    received_at = parsed_msg.received_at.isoformat() if parsed_msg and parsed_msg.received_at else fixture_email.get("received_at")
    metadata: dict[str, Any] = {
        "local_processor": True,
        "source_sha256": source_hash,
    }
    if parsed_msg:
        metadata.update(parsed_msg.metadata)
        metadata["sender_name"] = parsed_msg.sender_name
        metadata["attachment_count"] = len(parsed_msg.attachments)
    if source_system_override == "graph_mailbox" and source_message_id_override:
        metadata["claimed_processing_message_id"] = source_message_id_override
    if office_web_link:
        metadata["office_web_link"] = office_web_link

    return {
        "source_system": source_system_override or ("local_msg" if parsed_msg else "local_file"),
        "source_message_id": source_message_id_override or (source_email_path.name if source_email_path else "graph_message"),
        "idempotency_key": idempotency_key,
        "subject": (parsed_msg.subject if parsed_msg else None) or fixture_email.get("subject"),
        "sender_email": (parsed_msg.sender_email if parsed_msg else None) or fixture_email.get("sender_email"),
        "received_at": received_at,
        "raw_storage_path": _relative(source_email_path) if source_email_path else None,
        "office_web_link": office_web_link,
        "metadata": metadata,
    }


def _routing_path(destination: Destination) -> str:
    parts = [
        destination.parent_folder,
        destination.display_name,
    ]
    return " / ".join(str(part) for part in parts if part)


def _aggregate_item_decisions(
    item_decisions: list[Decision],
    workflow_rules: list[WorkflowRule],
    runtime_config: dict[str, Any],
    get_destination: Any,
    item_metadata: list[dict[str, Any]] | None = None,
) -> Decision:
    if not item_decisions:
        raise ValueError("Cannot aggregate an empty decision list.")
    metadata_by_index = item_metadata or [{} for _ in item_decisions]

    item_audit = [
        {
            "item_kind": metadata_by_index[index].get("item_kind"),
            "item_key": metadata_by_index[index].get("item_key"),
            "outcome": decision.outcome,
            "destination_code": decision.destination_code,
            "matched_rule_code": decision.matched_rule_code,
            "matched_rule_version": decision.matched_rule_version,
            "reason": decision.reason,
            "confidence": decision.confidence,
            "routing_match": decision.routing_match,
        }
        for index, decision in enumerate(item_decisions)
    ]

    effective_item_decisions = item_decisions

    terminal = [decision for decision in effective_item_decisions if decision.outcome in {"ESCALATE", "FLAG"}]
    if terminal:
        priority_by_rule = {rule.rule_code: rule.priority for rule in workflow_rules}
        winner = sorted(terminal, key=lambda decision: priority_by_rule.get(decision.matched_rule_code, 999999))[0]
        return _decision_with_aggregation(winner, item_audit, "highest_priority_item_escalation")

    signatures = {(decision.outcome, decision.destination_code) for decision in effective_item_decisions}
    if len(signatures) == 1:
        winner = effective_item_decisions[0]
        reason = winner.reason if len(effective_item_decisions) == 1 else f"All {len(effective_item_decisions)} actionable document items resolved to {winner.outcome} {winner.destination_code or 'without destination'}."
        return Decision(
            outcome=winner.outcome,
            destination_code=winner.destination_code,
            destination_email=winner.destination_email,
            reason=reason,
            confidence=min(decision.confidence for decision in effective_item_decisions),
            matched_rule_code=winner.matched_rule_code,
            matched_rule_version=winner.matched_rule_version,
            extracted_fields={"items": [decision.extracted_fields for decision in effective_item_decisions]},
            routing_match={**winner.routing_match, "aggregation": {"mode": "unanimous", "item_decisions": item_audit}},
        )

    rule = _mixed_destination_rule(workflow_rules)
    destination_code = rule.destination_code or runtime_config.get("default_escalate_destination")
    destination = get_destination(destination_code) if destination_code else None
    return Decision(
        outcome="ESCALATE",
        destination_code=destination.destination_code if destination else None,
        destination_email=destination.email_address if destination and destination.send_email else None,
        reason=rule.reason_template,
        confidence=min(decision.confidence for decision in effective_item_decisions),
        matched_rule_code=rule.rule_code,
        matched_rule_version=rule.version,
        extracted_fields={"items": [decision.extracted_fields for decision in effective_item_decisions]},
        routing_match={"aggregation": {"mode": "mixed_destinations", "item_decisions": item_audit}},
    )


def _decision_with_aggregation(decision: Decision, item_audit: list[dict[str, Any]], mode: str) -> Decision:
    return Decision(
        outcome=decision.outcome,
        destination_code=decision.destination_code,
        destination_email=decision.destination_email,
        reason=decision.reason,
        confidence=decision.confidence,
        matched_rule_code=decision.matched_rule_code,
        matched_rule_version=decision.matched_rule_version,
        extracted_fields=decision.extracted_fields,
        routing_match={**decision.routing_match, "aggregation": {"mode": mode, "item_decisions": item_audit}},
    )


def _mixed_destination_rule(workflow_rules: list[WorkflowRule]) -> WorkflowRule:
    for rule in sorted(workflow_rules, key=lambda item: item.priority):
        if rule.condition_type == "aggregation_mixed_destinations" or rule.rule_code == "hard_mixed_item_destinations":
            return rule
    for rule in sorted(workflow_rules, key=lambda item: item.priority):
        if rule.condition_type == "fallback":
            return WorkflowRule(
                "hard_mixed_item_destinations",
                "Multiple document items disagree on routing",
                rule.priority,
                "aggregation_mixed_destinations",
                "ESCALATE",
                rule.destination_code,
                "Multiple extracted document items resolved to different outcomes or destinations -> ESCALATE",
                rule.version,
                {},
            )
    raise ValueError("No fallback rule is available for mixed document item aggregation.")


def _relative(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _decision_context_from_parsed_msg(parsed_msg: ParsedMsg | None) -> DecisionContext:
    if parsed_msg is None:
        return DecisionContext()
    metadata = parsed_msg.metadata if isinstance(parsed_msg.metadata, dict) else {}
    thread_context = metadata.get("thread_context")
    if not isinstance(thread_context, dict):
        latest = latest_body_text(metadata, parsed_msg.body_text)
        return DecisionContext(latest_body_text=latest if latest and latest.strip() else None)
    latest = thread_context.get("latest_body_text")
    quoted = thread_context.get("quoted_history_text")
    return DecisionContext(
        latest_body_text=latest if isinstance(latest, str) and latest.strip() else None,
        quoted_history_text=quoted if isinstance(quoted, str) and quoted.strip() else None,
        has_quoted_history=thread_context.get("has_quoted_history") is True,
    )


def _pdf_evaluation_summary(attachment_records: list[dict[str, Any]]) -> dict[str, int]:
    pdf_total = 0
    pdf_success = 0
    pdf_failed = 0
    non_pdf_total = 0
    for record in attachment_records:
        evaluation = ((record.get("metadata") or {}).get("pdf_evaluation") if isinstance(record.get("metadata"), dict) else None) or {}
        eligible = bool(evaluation.get("eligible"))
        status = evaluation.get("status")
        if eligible:
            pdf_total += 1
            if status == "success":
                pdf_success += 1
            else:
                pdf_failed += 1
        else:
            non_pdf_total += 1
    return {
        "pdf_total": pdf_total,
        "pdf_success": pdf_success,
        "pdf_failed": pdf_failed,
        "non_pdf_total": non_pdf_total,
    }


def _business_attachment_records(attachment_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [record for record in attachment_records if not _is_inline_attachment_record(record)]


def _not_selected_business_attachments(
    attachment_records: list[dict[str, Any]],
    items: tuple[DocumentItem, ...],
    excluded_attachments: tuple[ExcludedAttachment, ...] = (),
) -> list[dict[str, Any]]:
    selected_names = {
        name
        for item in items
        for name in item.extraction.evidence.source_attachments
        if name
    }
    excluded_by_name = {attachment.file_name: attachment for attachment in excluded_attachments}
    not_selected: list[dict[str, Any]] = []
    for record in attachment_records:
        file_name = str(record.get("file_name") or "")
        if not file_name or file_name in selected_names:
            continue
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        selection = metadata.get("extractor_selection") if isinstance(metadata, dict) else None
        document_intelligence = metadata.get("document_intelligence") if isinstance(metadata, dict) else None
        not_selected_item = {
            "file_name": file_name,
            "reason": "excluded_by_extractor"
            if file_name in excluded_by_name
            else "not_returned_as_document_item_by_extractor",
            "extractor_selection": selection if isinstance(selection, dict) else None,
            "document_intelligence_status": document_intelligence.get("status")
            if isinstance(document_intelligence, dict)
            else None,
        }
        if file_name in excluded_by_name:
            not_selected_item["extractor_exclusion"] = {
                "reason_code": excluded_by_name[file_name].reason_code,
                "reason": excluded_by_name[file_name].reason,
                "source": excluded_by_name[file_name].source,
            }
        not_selected.append(not_selected_item)
    return not_selected


def _document_intelligence_selected_records(attachment_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for record in attachment_records:
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        selection = metadata.get("extractor_selection") if isinstance(metadata, dict) else None
        if isinstance(selection, dict) and selection.get("selected_extractor") == "document_intelligence":
            selected.append(record)
    return selected


def _is_inline_attachment_record(record: dict[str, Any]) -> bool:
    metadata = record.get("metadata")
    if not isinstance(metadata, dict):
        return False
    return bool(metadata.get("is_inline"))


def _remove_inline_source_attachments(raw_payload: dict[str, Any], attachment_records: list[dict[str, Any]]) -> None:
    if raw_payload.get("schema_version") == "extraction_batch.v1" and isinstance(raw_payload.get("items"), list):
        for item in raw_payload["items"]:
            if isinstance(item, dict) and isinstance(item.get("extraction"), dict):
                _remove_inline_source_attachments(item["extraction"], attachment_records)
        return
    inline_names = {str(record.get("file_name") or "") for record in attachment_records if _is_inline_attachment_record(record)}
    if not inline_names:
        return
    evidence = raw_payload.get("evidence")
    if not isinstance(evidence, dict):
        return
    source_attachments = evidence.get("source_attachments")
    if not isinstance(source_attachments, list):
        return
    evidence["source_attachments"] = [
        name for name in source_attachments if not (isinstance(name, str) and name in inline_names)
    ]


def _restore_cited_excluded_attachments(raw_payload: dict[str, Any]) -> dict[str, Any]:
    if raw_payload.get("schema_version") != "extraction_batch.v1":
        return {}
    raw_items = raw_payload.get("items")
    excluded_attachments = raw_payload.get("excluded_attachments")
    if not isinstance(raw_items, list) or not isinstance(excluded_attachments, list) or not excluded_attachments:
        return {}

    cited_names: set[str] = set()
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        extraction = item.get("extraction")
        if not isinstance(extraction, dict):
            continue
        evidence = extraction.get("evidence")
        if not isinstance(evidence, dict):
            continue
        source_attachments = evidence.get("source_attachments")
        if not isinstance(source_attachments, list):
            continue
        cited_names.update(name for name in source_attachments if isinstance(name, str) and name)

    if not cited_names:
        return {}

    kept: list[Any] = []
    restored: list[dict[str, Any]] = []
    for excluded in excluded_attachments:
        file_name = excluded.get("file_name") if isinstance(excluded, dict) else None
        if isinstance(file_name, str) and file_name in cited_names:
            restored.append(
                {
                    "file_name": file_name,
                    "reason_code": excluded.get("reason_code"),
                    "source": excluded.get("source"),
                }
            )
            continue
        kept.append(excluded)

    if not restored:
        return {}

    raw_payload["excluded_attachments"] = kept
    return {
        "status": "cited_excluded_attachments_restored_to_item_evidence",
        "restored_attachment_count": len(restored),
        "restored_attachments": restored,
    }


def _normalize_azure_extraction_payload(raw_payload: dict[str, Any]) -> dict[str, Any]:
    removed_paths: list[str] = []

    def normalize_one(payload: dict[str, Any], path_prefix: str) -> None:
        property_lookup = payload.get("property_lookup")
        if not isinstance(property_lookup, dict):
            return
        candidates = property_lookup.get("address_candidates")
        if not isinstance(candidates, list):
            return
        kept = []
        for index, candidate in enumerate(candidates):
            if isinstance(candidate, dict) and _is_componentless_address_candidate(candidate):
                removed_paths.append(f"{path_prefix}.property_lookup.address_candidates[{index}]")
                continue
            kept.append(candidate)
        if len(kept) != len(candidates):
            property_lookup["address_candidates"] = kept

    if raw_payload.get("schema_version") == "extraction_batch.v1" and isinstance(raw_payload.get("items"), list):
        for index, item in enumerate(raw_payload["items"]):
            if isinstance(item, dict) and isinstance(item.get("extraction"), dict):
                normalize_one(item["extraction"], f"items[{index}].extraction")
    else:
        normalize_one(raw_payload, "extraction")

    if not removed_paths:
        return {}
    return {
        "status": "valid_after_normalization",
        "removed_address_candidate_count": len(removed_paths),
        "removed_address_candidate_paths": removed_paths,
    }


def _apply_triage_risk_overrides(raw_payload: dict[str, Any], triage_audit: dict[str, Any] | None) -> None:
    if not triage_audit:
        return
    triage_payload = triage_audit.get("parsed_output")
    if not isinstance(triage_payload, dict):
        return
    triage_items = triage_payload.get("items")
    if not isinstance(triage_items, list):
        return
    flags_by_item_key = {
        item.get("item_key"): set(item.get("risk_flags") or [])
        for item in triage_items
        if isinstance(item, dict) and isinstance(item.get("item_key"), str) and isinstance(item.get("risk_flags"), list)
    }
    if raw_payload.get("schema_version") == "extraction_batch.v1" and isinstance(raw_payload.get("items"), list):
        for index, item in enumerate(raw_payload["items"]):
            if not isinstance(item, dict) or not isinstance(item.get("extraction"), dict):
                continue
            item_key = item.get("item_key")
            flags = flags_by_item_key.get(item_key)
            if flags is None and len(flags_by_item_key) == 1 and len(raw_payload["items"]) == 1:
                flags = next(iter(flags_by_item_key.values()))
            _apply_triage_flags_to_extraction(item["extraction"], flags or set())
        return
    if len(flags_by_item_key) == 1:
        _apply_triage_flags_to_extraction(raw_payload, next(iter(flags_by_item_key.values())))


def _apply_triage_flags_to_extraction(extraction_payload: dict[str, Any], risk_flags: set[str]) -> None:
    if not risk_flags:
        return
    document = extraction_payload.get("document")
    observed = extraction_payload.get("observed_facts")
    if not isinstance(document, dict) or not isinstance(observed, dict):
        return
    if "link_only" in risk_flags:
        document["link_only"] = True
        observed["mentions_payment_link_only"] = True
    if "multi_invoice" in risk_flags:
        detailed_rejected_multi_invoice = document.get("multi_invoice") is False and observed.get("indicates_multiple_invoices") is False
        if not detailed_rejected_multi_invoice:
            document["multi_invoice"] = True
            observed["indicates_multiple_invoices"] = True
    if "separate_supporting_document" in risk_flags:
        observed["mentions_separate_backup_document"] = True
    if "contract_or_pay_application" in risk_flags:
        observed["indicates_contract_or_pay_application"] = True
    if "vendor_question_or_payment_inquiry" in risk_flags:
        observed["indicates_vendor_question_or_payment_inquiry"] = True
    if "wrong_destination" in risk_flags:
        observed["indicates_wrong_destination"] = True
    # Past-due routing is intentionally not restored from triage. The detail
    # extraction must source-support explicit current-email past-due language.
    if "low_text_quality" in risk_flags:
        observed["has_low_text_quality"] = True
    if "conflicting_signals" in risk_flags:
        observed["has_conflicting_signals"] = True


def _json_equivalent(left: Any, right: Any) -> bool:
    return json.dumps(left, sort_keys=True, separators=(",", ":"), default=str) == json.dumps(
        right,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _with_triage_audit(detail_attempt: ExtractionAttempt, triage_attempt: ExtractionAttempt) -> ExtractionAttempt:
    return ExtractionAttempt(
        parsed_payload=detail_attempt.parsed_payload,
        prompt=detail_attempt.prompt,
        raw_response=detail_attempt.raw_response,
        extractor_type=detail_attempt.extractor_type,
        model=detail_attempt.model,
        prompt_version=detail_attempt.prompt_version,
        deployment_name=detail_attempt.deployment_name,
        api_version=detail_attempt.api_version,
        request_parameters=detail_attempt.request_parameters,
        raw_usage=detail_attempt.raw_usage,
        prompt_tokens=detail_attempt.prompt_tokens,
        completion_tokens=detail_attempt.completion_tokens,
        total_tokens=detail_attempt.total_tokens,
        cached_prompt_tokens=detail_attempt.cached_prompt_tokens,
        reasoning_tokens=detail_attempt.reasoning_tokens,
        latency_ms=detail_attempt.latency_ms,
        attempts=detail_attempt.attempts,
        triage={
            "status": "validated",
            "prompt_version": triage_attempt.prompt_version,
            "raw_response": triage_attempt.raw_response,
            "parsed_output": triage_attempt.parsed_payload,
            "validation_attempts": list(triage_attempt.attempts),
        },
    )


def _is_componentless_address_candidate(candidate: dict[str, Any]) -> bool:
    return not any(_has_text(candidate.get(key)) for key in ("street", "city", "state", "zipcode", "normalized_address"))


def _has_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _has_unreadable_required_attachment(attachment_records: list[dict[str, Any]], raw_payload: dict[str, Any]) -> bool:
    if raw_payload.get("schema_version") == "extraction_batch.v1" and isinstance(raw_payload.get("items"), list):
        return any(
            _has_unreadable_required_attachment(attachment_records, item.get("extraction"))
            for item in raw_payload["items"]
            if isinstance(item, dict) and isinstance(item.get("extraction"), dict)
        )
    document = raw_payload.get("document")
    if not isinstance(document, dict):
        return False
    if document.get("requires_attachment") is not True:
        return False
    if document.get("has_invoice_attachment") is not True:
        return False
    required_names = _source_attachment_names(raw_payload)
    for record in attachment_records:
        if _is_inline_attachment_record(record):
            continue
        if required_names and str(record.get("file_name") or "") not in required_names:
            continue
        selection = _extractor_selection(record)
        selected_extractor = selection.get("selected_extractor") if isinstance(selection, dict) else None
        if selected_extractor == "pymupdf":
            if not _is_pymupdf_readable(record):
                return True
            continue
        if selected_extractor == "document_intelligence":
            if not _is_document_intelligence_readable(record):
                return True
            continue
        if not _is_document_intelligence_supported_attachment(record):
            return True
        return True
    return False


def _source_attachment_names(raw_payload: dict[str, Any]) -> set[str]:
    evidence = raw_payload.get("evidence")
    if not isinstance(evidence, dict):
        return set()
    source_attachments = evidence.get("source_attachments")
    if not isinstance(source_attachments, list):
        return set()
    return {name for name in source_attachments if isinstance(name, str) and name}


def _has_unreadable_pdf(attachment_records: list[dict[str, Any]], raw_payload: dict[str, Any]) -> bool:
    return _has_unreadable_required_attachment(attachment_records, raw_payload)


def _is_document_intelligence_supported_attachment(record: dict[str, Any]) -> bool:
    storage_path = str(record.get("storage_path") or "")
    content_type = str(record.get("content_type") or "").lower()
    suffix = Path(storage_path).suffix.lower()
    return suffix in {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".heif"} or content_type in {
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/tiff",
        "image/bmp",
        "image/heif",
    }


def _is_document_intelligence_readable(record: dict[str, Any]) -> bool:
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    di = metadata.get("document_intelligence") if isinstance(metadata, dict) else None
    if not isinstance(di, dict):
        return False
    if di.get("eligible") is not True or di.get("status") != "success":
        return False
    text_excerpt = di.get("text_excerpt")
    return isinstance(text_excerpt, str) and bool(" ".join(text_excerpt.split()))


def _is_pymupdf_readable(record: dict[str, Any]) -> bool:
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    pdf = metadata.get("pdf_evaluation") if isinstance(metadata, dict) else None
    if not isinstance(pdf, dict) or pdf.get("status") != "success":
        return False
    text_excerpt = pdf.get("text_excerpt")
    return isinstance(text_excerpt, str) and bool(" ".join(text_excerpt.split()))


def _extractor_selection(record: dict[str, Any]) -> dict[str, Any]:
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    selection = metadata.get("extractor_selection") if isinstance(metadata, dict) else None
    return selection if isinstance(selection, dict) else {}


def _pre_decision_facts(attachment_records: list[dict[str, Any]], raw_payload: dict[str, Any]) -> dict[str, bool]:
    unreadable = _has_unreadable_required_attachment(attachment_records, raw_payload)
    low_quality = False
    return {
        "pdf_required_but_unreadable": unreadable,
        "pdf_text_low_quality": low_quality,
    }
