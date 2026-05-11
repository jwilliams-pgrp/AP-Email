from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from ap_automation.models.decision import Decision
from ap_automation.models.extraction import ExtractionPayload
from ap_automation.services.local_processor import LocalProcessor
from test_decision_engine import InMemoryPolicyRepository, _payload


class LocalProcessorTests(unittest.TestCase):
    def test_process_fixture_writes_audit_trace_and_dry_run_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_email = root / "local" / "ingest" / "sample.eml"
            fixture = root / "tests" / "fixtures" / "extractions" / "sample.json"
            source_email.parent.mkdir(parents=True)
            fixture.parent.mkdir(parents=True)
            source_email.write_text("sample email", encoding="utf-8")
            fixture.write_text(json.dumps(_payload()), encoding="utf-8")

            operational_repository = InMemoryOperationalRepository()
            processor = LocalProcessor(root, InMemoryPolicyRepository(), operational_repository)

            run_id = processor.process_fixture(source_email, fixture)

            self.assertEqual(operational_repository.runs[run_id]["final_outcome"], "AUTO")
            trace_path = root / "local" / "audit" / "traces" / f"{run_id}.mmd"
            self.assertTrue(trace_path.exists())
            trace = trace_path.read_text(encoding="utf-8")
            self.assertIn('ingestion["Email Received"]', trace)
            self.assertIn('rules -->|"property_routing_match reason"', trace)
            self.assertIn('decision["Business Decision<br/>AUTO"]', trace)
            self.assertIn("classDef success", trace)
            self.assertIn("class start,ingestion,attachments,extraction,validation,duplicate,routing,rules,decision,action,finalize success;", trace)
            self.assertTrue((root / "local" / "outbound" / "dry-run" / f"{run_id}.json").exists())
            self.assertEqual(
                [step["step_type"] for step in operational_repository.steps],
                [
                    "INGESTION",
                    "ATTACHMENT_PROCESSING",
                    "LLM_EXTRACTION",
                    "VALIDATION",
                    "DUPLICATE_CHECK",
                    "ROUTING_MATCH",
                    "RULE_EVALUATION",
                    "DECISION",
                    "ACTION",
                    "FINALIZE",
                ],
            )

    def test_process_msg_extracts_attachments_for_downstream_processing(self) -> None:
        source_email = Path("reference/test_emails/FW_ Attached is your invoice #2857 from Alliance Landscape Co LLC.msg")
        if not source_email.exists():
            self.skipTest("reference MSG fixture is not available")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fixture = root / "tests" / "fixtures" / "extractions" / "sample.json"
            fixture.parent.mkdir(parents=True)
            fixture.write_text(json.dumps(_payload()), encoding="utf-8")

            operational_repository = InMemoryOperationalRepository()
            processor = LocalProcessor(root, InMemoryPolicyRepository(), operational_repository)

            processor.process_fixture(source_email, fixture)

            self.assertGreater(len(operational_repository.attachments), 0)
            for attachment in operational_repository.attachments:
                storage_path = root / attachment["storage_path"]
                self.assertTrue(storage_path.exists())
                self.assertGreater(attachment["file_size_bytes"], 0)
                self.assertRegex(attachment["sha256"], r"^[a-f0-9]{64}$")

            attachment_step = next(step for step in operational_repository.steps if step["step_type"] == "ATTACHMENT_PROCESSING")
            self.assertEqual(attachment_step["output_summary"]["mode"], "local_msg")
            self.assertEqual(
                attachment_step["output_summary"]["attachments_extracted"],
                len(operational_repository.attachments),
            )

    def test_process_msg_can_use_codex_extractor_without_fixture(self) -> None:
        source_email = Path("reference/test_emails/FW_ 752944_80040277-ACH EzPay Projected Payment.msg")
        if not source_email.exists():
            self.skipTest("reference MSG fixture is not available")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            operational_repository = InMemoryOperationalRepository()
            codex_extractor = FakeCodexExtractor(
                _payload(document_type="ben_e_keith_notice", flags=["ach_or_auto_draft", "ben_e_keith"])
            )
            processor = LocalProcessor(root, InMemoryPolicyRepository(), operational_repository, codex_extractor)

            run_id = processor.process_email(source_email)

            self.assertEqual(operational_repository.runs[run_id]["final_outcome"], "FILE")
            decision = operational_repository.decisions["decision-1"]
            self.assertEqual(decision.destination_code, "FOLDER_BEN_E_KEITH")
            extraction_step = next(step for step in operational_repository.steps if step["step_type"] == "LLM_EXTRACTION")
            self.assertEqual(extraction_step["output_summary"]["extractor_type"], "codex_cli")
            self.assertGreater(len(codex_extractor.attachment_records), 0)

    def test_invalid_extraction_marks_run_failed_and_persists_llm_audit_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_email = root / "local" / "ingest" / "sample.eml"
            fixture = root / "tests" / "fixtures" / "extractions" / "invalid.json"
            source_email.parent.mkdir(parents=True)
            fixture.parent.mkdir(parents=True)
            source_email.write_text("sample email", encoding="utf-8")
            fixture.write_text(json.dumps({"schema_version": "extraction.v1"}), encoding="utf-8")

            operational_repository = InMemoryOperationalRepository()
            processor = LocalProcessor(root, InMemoryPolicyRepository(), operational_repository)

            with self.assertRaises(Exception):
                processor.process_fixture(source_email, fixture)

            self.assertEqual(operational_repository.runs["run-1"]["status"], "failed")
            trace_path = root / "local" / "audit" / "traces" / "run-1.mmd"
            self.assertTrue(trace_path.exists())
            trace = trace_path.read_text(encoding="utf-8")
            self.assertIn('validation["Failed Step<br/>Required Fields Checked"]', trace)
            self.assertIn('error["Failure Reason<br/>Invalid extraction payload:', trace)
            self.assertIn("class start,ingestion,attachments,extraction success;", trace)
            self.assertIn("class validation,error failure;", trace)
            self.assertEqual(operational_repository.extractions[0]["errors"][0], "extractor must be an object")
            self.assertIn("llm_output", operational_repository.extractions[0]["raw_output"])
            finalize_step = operational_repository.steps[-1]
            self.assertEqual(finalize_step["step_type"], "FINALIZE")
            self.assertEqual(finalize_step["output_summary"]["status"], "failed")


class FakeCodexExtractor:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.attachment_records: list[dict[str, Any]] = []

    def extract_msg(self, parsed_msg, attachment_records: list[dict[str, Any]]):
        self.attachment_records = attachment_records
        payload = dict(self.payload)
        payload["extractor"] = {
            "type": "codex_cli",
            "name": "codex_exec",
            "model": None,
            "prompt_version": "local_msg_extraction.v1",
        }
        from ap_automation.services.codex_extractor import ExtractionAttempt

        return ExtractionAttempt(
            parsed_payload=payload,
            prompt="rendered prompt",
            raw_response=json.dumps(payload),
            extractor_type="codex_cli",
            model=None,
            prompt_version="local_msg_extraction.v1",
        )


class InMemoryOperationalRepository:
    def __init__(self) -> None:
        self.emails: dict[str, dict[str, Any]] = {}
        self.runs: dict[str, dict[str, Any]] = {}
        self.steps: list[dict[str, Any]] = []
        self.extractions: list[dict[str, Any]] = []
        self.attachments: list[dict[str, Any]] = []
        self.decisions: dict[str, Decision] = {}
        self.actions: list[dict[str, Any]] = []
        self.review_items: list[dict[str, Any]] = []

    def upsert_email(self, metadata: dict[str, Any]) -> str:
        email_id = "email-1"
        self.emails[email_id] = metadata
        return email_id

    def create_audit_run(self, email_id: str, metadata: dict[str, Any]) -> str:
        run_id = "run-1"
        self.runs[run_id] = {"email_id": email_id, "metadata": metadata, "status": "started"}
        return run_id

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
        self.steps.append(
            {
                "run_id": run_id,
                "step_type": step_type,
                "input_summary": input_summary,
                "output_summary": output_summary,
                "reason": reason,
                "confidence": confidence,
                "decision": decision,
                "error": error,
            }
        )

    def save_extraction(
        self,
        email_id: str,
        extraction: ExtractionPayload | None,
        parsed_payload: dict[str, Any],
        validation_errors: list[str],
        raw_output: dict[str, Any] | None = None,
    ) -> None:
        self.extractions.append(
            {
                "email_id": email_id,
                "extraction": extraction,
                "parsed_payload": parsed_payload,
                "raw_output": raw_output,
                "errors": validation_errors,
            }
        )

    def save_attachments(self, email_id: str, attachments: list[dict[str, Any]]) -> None:
        for attachment in attachments:
            self.attachments.append({"email_id": email_id, **attachment})

    def save_decision(self, email_id: str, run_id: str, decision: Decision) -> str:
        decision_id = "decision-1"
        self.decisions[decision_id] = decision
        return decision_id

    def save_action(self, email_id: str, decision_id: str, decision: Decision, manifest_path: str) -> None:
        self.actions.append({"email_id": email_id, "decision_id": decision_id, "manifest_path": manifest_path})

    def enqueue_review(self, email_id: str, decision_id: str, reason: str, priority: str = "normal") -> None:
        self.review_items.append({"email_id": email_id, "decision_id": decision_id, "reason": reason, "priority": priority})

    def finalize_audit_run(self, run_id: str, final_outcome: str, trace_artifact_path: str) -> None:
        self.runs[run_id]["status"] = "completed"
        self.runs[run_id]["final_outcome"] = final_outcome
        self.runs[run_id]["trace_artifact_path"] = trace_artifact_path

    def fail_audit_run(self, run_id: str, error: str, trace_artifact_path: str | None = None) -> None:
        self.runs[run_id]["status"] = "failed"
        self.runs[run_id]["error"] = error
        self.runs[run_id]["trace_artifact_path"] = trace_artifact_path
