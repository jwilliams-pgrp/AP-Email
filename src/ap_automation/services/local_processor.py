from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ap_automation.models.extraction import ExtractionValidationError, validate_extraction
from ap_automation.repositories.protocols import OperationalRepository, PolicyRepository
from ap_automation.services.codex_extractor import CodexCliExtractor, CodexExtractionError, ExtractionAttempt
from ap_automation.services.decision_engine import DecisionEngine
from ap_automation.services.local_artifacts import LocalArtifactStore
from ap_automation.services.msg_parser import ParsedMsg, parse_msg


class LocalProcessor:
    """Runs the LOCAL dry-run pipeline using Codex CLI or fixture extraction."""

    def __init__(
        self,
        project_root: Path,
        policy_repository: PolicyRepository,
        operational_repository: OperationalRepository,
        codex_extractor: CodexCliExtractor | None = None,
    ) -> None:
        self._project_root = project_root
        self._artifacts = LocalArtifactStore(project_root)
        self._policy_repository = policy_repository
        self._operational_repository = operational_repository
        self._codex_extractor = codex_extractor or CodexCliExtractor(project_root)

    def process_fixture(self, source_email_path: Path, extraction_fixture_path: Path) -> str:
        return self._process(source_email_path, extraction_fixture_path)

    def process_email(self, source_email_path: Path) -> str:
        return self._process(source_email_path, None)

    def _extraction_attempt(
        self,
        source_email_path: Path,
        extraction_fixture_path: Path | None,
        fixture_payload: dict[str, Any],
        parsed_msg: ParsedMsg | None,
        attachment_records: list[dict[str, Any]],
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
            raise ValueError("Codex CLI extraction without a fixture currently requires a .msg source email.")
        return self._codex_extractor.extract_msg(parsed_msg, attachment_records)

    def _process(self, source_email_path: Path, extraction_fixture_path: Path | None) -> str:
        self._artifacts.ensure_directories()
        source_hash = _sha256_file(source_email_path)
        idempotency_key = f"local_file:{source_hash}"

        parsed_msg = _parse_source_email(source_email_path)
        fixture_payload = _fixture_payload(extraction_fixture_path)
        email_metadata = _email_metadata(source_email_path, idempotency_key, source_hash, fixture_payload, parsed_msg)
        email_id = self._operational_repository.upsert_email(
            email_metadata
        )
        run_id = self._operational_repository.create_audit_run(email_id, {"mode": "LOCAL", "dry_run": True})
        self._operational_repository.add_audit_step(
            run_id,
            "INGESTION",
            {"source_path": _relative(source_email_path), "source_sha256": source_hash},
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
        if parsed_msg:
            attachment_records = self._artifacts.write_attachments(email_id, parsed_msg.attachments)
            for record in attachment_records:
                record["sha256"] = _sha256_bytes((self._project_root / record["storage_path"]).read_bytes())
            self._operational_repository.save_attachments(email_id, attachment_records)
        self._operational_repository.add_audit_step(
            run_id,
            "ATTACHMENT_PROCESSING",
            {"source_path": _relative(source_email_path)},
            {
                "mode": "local_msg" if parsed_msg else "no_msg_parser",
                "attachments_extracted": len(attachment_records),
                "attachments": [
                    {
                        "file_name": record["file_name"],
                        "storage_path": record["storage_path"],
                        "sha256": record["sha256"],
                        "file_size_bytes": record["file_size_bytes"],
                    }
                    for record in attachment_records
                ],
            },
            reason="Local MSG attachments extracted to filesystem artifacts for downstream processing."
            if parsed_msg
            else "Source was not an MSG file; no local attachment extraction was attempted.",
        )

        extraction_input = (
            {"fixture_path": _relative(extraction_fixture_path)}
            if extraction_fixture_path
            else {"source_path": _relative(source_email_path)}
        )
        try:
            extraction_attempt = self._extraction_attempt(source_email_path, extraction_fixture_path, fixture_payload, parsed_msg, attachment_records)
        except CodexExtractionError as exc:
            audit_payload = {
                "extractor_type": "codex_cli",
                "prompt_version": "local_msg_extraction.v1",
                "llm_input": exc.prompt,
                "llm_output": exc.raw_response,
                "error": str(exc),
            }
            prompt_path = self._artifacts.write_prompt_snapshot(run_id, exc.prompt) if exc.prompt else None
            extraction_snapshot_path = self._artifacts.write_extraction_snapshot(run_id, audit_payload)
            self._operational_repository.save_extraction(email_id, None, audit_payload, [str(exc)], audit_payload)
            self._operational_repository.add_audit_step(
                run_id,
                "LLM_EXTRACTION",
                {**extraction_input, "prompt_artifact_path": prompt_path, "rendered_prompt": exc.prompt},
                {
                    "artifact_path": extraction_snapshot_path,
                    "extractor_type": "codex_cli",
                    "raw_response": exc.raw_response,
                },
                reason="Codex CLI extraction failed before schema validation.",
                error=str(exc),
            )
            trace_path = self._fail_run(run_id, "LLM_EXTRACTION", str(exc))
            self._operational_repository.add_audit_step(
                run_id,
                "FINALIZE",
                {"run_id": run_id},
                {"trace_artifact_path": trace_path, "final_outcome": None, "status": "failed"},
                reason="Local dry-run failed during LLM extraction.",
                error=str(exc),
            )
            raise

        raw_payload = extraction_attempt.parsed_payload
        audit_payload = extraction_attempt.audit_payload()
        extraction_snapshot_path = self._artifacts.write_extraction_snapshot(run_id, audit_payload)
        prompt_path = self._artifacts.write_prompt_snapshot(run_id, extraction_attempt.prompt) if extraction_attempt.prompt else None
        self._operational_repository.add_audit_step(
            run_id,
            "LLM_EXTRACTION",
            {**extraction_input, "prompt_artifact_path": prompt_path, "rendered_prompt": extraction_attempt.prompt},
            {
                "artifact_path": extraction_snapshot_path,
                "extractor_type": extraction_attempt.extractor_type,
                "model": extraction_attempt.model,
                "prompt_version": extraction_attempt.prompt_version,
                "raw_response": extraction_attempt.raw_response,
            },
            reason="Fixture extraction used for local development."
            if extraction_fixture_path
            else "Codex CLI extraction used for local development.",
        )

        try:
            extraction = validate_extraction(raw_payload)
            validation_errors: list[str] = []
            self._operational_repository.save_extraction(email_id, extraction, raw_payload, validation_errors, audit_payload)
            self._operational_repository.add_audit_step(
                run_id,
                "VALIDATION",
                {"artifact_path": extraction_snapshot_path},
                {"validation_status": "valid"},
                confidence=extraction.confidence.overall,
            )
        except ExtractionValidationError as exc:
            self._operational_repository.save_extraction(email_id, None, raw_payload, exc.errors, audit_payload)
            self._operational_repository.add_audit_step(
                run_id,
                "VALIDATION",
                {"artifact_path": extraction_snapshot_path},
                {"validation_status": "invalid", "errors": exc.errors},
                reason="Invalid extraction payload -> REVIEW",
                error=str(exc),
            )
            trace_path = self._fail_run(run_id, "VALIDATION", str(exc))
            self._operational_repository.add_audit_step(
                run_id,
                "FINALIZE",
                {"run_id": run_id},
                {"trace_artifact_path": trace_path, "final_outcome": None, "status": "failed"},
                reason="Local dry-run failed during extraction validation.",
                error=str(exc),
            )
            raise

        engine = DecisionEngine(self._policy_repository)
        result = engine.decide(extraction, idempotency_key)
        self._operational_repository.add_audit_step(
            run_id,
            "DUPLICATE_CHECK",
            {"invoice_number": extraction.invoice.invoice_number},
            {"duplicate_status": result.decision.routing_match.get("duplicate_status")},
        )
        self._operational_repository.add_audit_step(
            run_id,
            "ROUTING_MATCH",
            extraction.extracted_fields,
            result.decision.routing_match,
        )
        self._operational_repository.add_audit_step(
            run_id,
            "RULE_EVALUATION",
            {"rule_count": len(result.evaluations)},
            {"evaluations": [evaluation.__dict__ for evaluation in result.evaluations]},
            reason=result.decision.reason,
        )
        decision_id = self._operational_repository.save_decision(email_id, run_id, result.decision)
        self._operational_repository.add_audit_step(
            run_id,
            "DECISION",
            result.decision.extracted_fields,
            {"outcome": result.decision.outcome, "destination_code": result.decision.destination_code},
            decision=result.decision.__dict__,
            reason=result.decision.reason,
            confidence=result.decision.confidence,
        )
        manifest_path = self._artifacts.write_dry_run_manifest(run_id, result.decision)
        self._operational_repository.save_action(email_id, decision_id, result.decision, manifest_path)
        if result.decision.outcome in {"REVIEW", "FLAG"}:
            self._operational_repository.enqueue_review(email_id, decision_id, result.decision.reason)
        self._operational_repository.add_audit_step(
            run_id,
            "ACTION",
            {"decision_id": decision_id},
            {"dry_run_manifest_path": manifest_path},
            reason="Dry-run action manifest created; no external system was mutated.",
        )
        trace_path = self._artifacts.write_trace(run_id, result.decision)
        self._operational_repository.finalize_audit_run(run_id, result.decision.outcome, trace_path)
        self._operational_repository.add_audit_step(
            run_id,
            "FINALIZE",
            {"run_id": run_id},
            {"trace_artifact_path": trace_path, "final_outcome": result.decision.outcome},
        )
        return run_id

    def _fail_run(self, run_id: str, failed_step: str, error: str) -> str:
        trace_path = self._artifacts.write_failure_trace(run_id, failed_step, error)
        self._operational_repository.fail_audit_run(run_id, error, trace_path)
        return trace_path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _parse_source_email(path: Path) -> ParsedMsg | None:
    if path.suffix.lower() != ".msg":
        return None
    return parse_msg(path)


def _fixture_payload(extraction_fixture_path: Path | None) -> dict[str, Any]:
    if extraction_fixture_path:
        return json.loads(extraction_fixture_path.read_text(encoding="utf-8"))
    return {}


def _email_metadata(
    source_email_path: Path,
    idempotency_key: str,
    source_hash: str,
    raw_payload: dict[str, Any],
    parsed_msg: ParsedMsg | None,
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

    return {
        "source_system": "local_msg" if parsed_msg else "local_file",
        "source_message_id": source_email_path.name,
        "idempotency_key": idempotency_key,
        "subject": (parsed_msg.subject if parsed_msg else None) or fixture_email.get("subject"),
        "sender_email": (parsed_msg.sender_email if parsed_msg else None) or fixture_email.get("sender_email"),
        "received_at": received_at,
        "raw_storage_path": _relative(source_email_path),
        "metadata": metadata,
    }


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.as_posix()
