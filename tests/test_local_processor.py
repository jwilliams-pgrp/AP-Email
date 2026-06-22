from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

from ap_automation.models.decision import Decision, Destination
from ap_automation.models.extraction import ExtractionPayload, ExtractionValidationError
from ap_automation.services.msg_parser import ParsedAttachment, ParsedMsg
from ap_automation.services.local_processor import LocalProcessor, _looks_like_payment_link_only_email, _looks_like_vendor_question_or_payment_inquiry
from ap_automation.models.decision import PropertyMatchEvaluation
from test_decision_engine import InMemoryPolicyRepository, _payload


class LocalProcessorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._retry_delay_seconds = LocalProcessor.retry_delay_seconds
        LocalProcessor.retry_delay_seconds = 0

    def tearDown(self) -> None:
        LocalProcessor.retry_delay_seconds = self._retry_delay_seconds

    def test_process_fixture_writes_audit_trace_and_action_plan(self) -> None:
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
            self.assertIn("class start,ingestion,attachments,selection,document_intelligence,extraction,validation,duplicate,routing,rules,decision,action,finalize success;", trace)
            self.assertTrue((root / "local" / "audit" / "actions" / f"{run_id}.json").exists())
            self.assertEqual(
                [step["step_type"] for step in operational_repository.steps],
                [
                    "INGESTION",
                    "ATTACHMENT_PROCESSING",
                    "DOCUMENT_EXTRACTION_SELECTION",
                    "DOCUMENT_INTELLIGENCE",
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

    def test_parser_thread_context_is_passed_to_item_decision_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_email = root / "local" / "ingest" / "reply.msg"
            fixture = root / "tests" / "fixtures" / "extractions" / "reply.json"
            source_email.parent.mkdir(parents=True)
            fixture.parent.mkdir(parents=True)
            source_email.write_text("placeholder", encoding="utf-8")
            payload = _payload(
                document_type="invoice",
                sender_email="katie@hillwood.com",
                vendor_name=None,
                property_code=None,
                bill_to=None,
                business_unit_code=None,
                has_invoice_attachment=False,
                amount=0,
                source_attachments=[],
                flags=["latest_reply_no_action"],
            )
            fixture.write_text(json.dumps(payload), encoding="utf-8")
            parsed_msg = ParsedMsg(
                subject="RE: Invoice",
                sender_email="katie@hillwood.com",
                sender_name="Katie",
                received_at=None,
                body_text="Received - will process for payment.\n\nFrom: Vendor\nSubject: Invoice",
                transport_headers=None,
                attachments=(),
                metadata={
                    "thread_context": {
                        "latest_body_text": "Received - will process for payment.",
                        "quoted_history_text": "From: Vendor\nSubject: Invoice",
                        "has_quoted_history": True,
                    }
                },
            )
            operational_repository = InMemoryOperationalRepository()
            processor = LocalProcessor(root, InMemoryPolicyRepository(), operational_repository)

            with patch("ap_automation.services.local_processor._parse_source_email", return_value=parsed_msg):
                run_id = processor.process_fixture(source_email, fixture)

            decision = operational_repository.decisions["decision-1"]
            self.assertEqual(decision.outcome, "DISCARD")
            self.assertEqual(decision.destination_code, "NO_ACTION")
            self.assertEqual(decision.matched_rule_code, "hard_current_reply_no_action")
            self.assertEqual(
                decision.routing_match["decision_context"]["latest_body_text"],
                "Received - will process for payment.",
            )
            extraction_snapshot = json.loads((root / "local" / "audit" / "extractions" / f"{run_id}.json").read_text(encoding="utf-8"))
            self.assertTrue(
                extraction_snapshot["items"][0]["decision"]["routing_match"]["decision_context"]["has_quoted_history"]
            )

    def test_short_hillwood_statement_reply_routes_to_no_action_when_extracted_as_no_action(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_email = root / "local" / "ingest" / "reply.msg"
            fixture = root / "tests" / "fixtures" / "extractions" / "reply.json"
            source_email.parent.mkdir(parents=True)
            fixture.parent.mkdir(parents=True)
            source_email.write_text("placeholder", encoding="utf-8")
            payload = _payload(
                document_type="statement",
                sender_email="Dan.Landsberg@hillwood.com",
                vendor_name="Chefs' Produce",
                property_code=None,
                bill_to=None,
                business_unit_code=None,
                has_invoice_attachment=False,
                amount=0,
                source_attachments=[],
                flags=["latest_reply_no_action", "missing_invoice_attachment", "statement_or_account_summary"],
                evidence_summary="Latest reply says thank you and confirms the sender just sent the prior item.",
            )
            fixture.write_text(json.dumps(payload), encoding="utf-8")
            latest_body = (
                "Thank you. I just sent it.\n"
                "All My Best,\n"
                "Dan Landsberg\n"
                "Executive Chef - The Texas Barn at Circle T Ranch\n"
                "Hillwood, A Perot Company"
            )
            parsed_msg = ParsedMsg(
                subject="RE: Chefs' Produce AR Statement for HW 2421 Barn LTD",
                sender_email="Dan.Landsberg@hillwood.com",
                sender_name="Landsberg, Dan",
                received_at=None,
                body_text=f"{latest_body}\n\nFrom: PropertiesAP <PropertiesAP@hillwood.com>\nSubject: RE: Chefs' Produce AR Statement",
                transport_headers=None,
                attachments=(),
                metadata={
                    "thread_context": {
                        "latest_body_text": latest_body,
                        "quoted_history_text": "From: PropertiesAP <PropertiesAP@hillwood.com>\nSubject: RE: Chefs' Produce AR Statement",
                        "has_quoted_history": True,
                    }
                },
            )
            operational_repository = InMemoryOperationalRepository()
            processor = LocalProcessor(root, InMemoryPolicyRepository(), operational_repository)

            with patch("ap_automation.services.local_processor._parse_source_email", return_value=parsed_msg):
                run_id = processor.process_fixture(source_email, fixture)

            decision = operational_repository.decisions["decision-1"]
            self.assertEqual(operational_repository.runs[run_id]["final_outcome"], "DISCARD")
            self.assertEqual(decision.outcome, "DISCARD")
            self.assertEqual(decision.destination_code, "NO_ACTION")
            self.assertEqual(decision.matched_rule_code, "hard_current_reply_no_action")

    def test_batch_items_same_destination_aggregate_to_one_action(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_email = root / "local" / "ingest" / "sample.eml"
            fixture = root / "tests" / "fixtures" / "extractions" / "batch.json"
            source_email.parent.mkdir(parents=True)
            fixture.parent.mkdir(parents=True)
            source_email.write_text("sample email", encoding="utf-8")
            first = _payload(source_attachments=["a.pdf"])
            first["invoice"]["invoice_number"] = "100"
            second = _payload(source_attachments=["b.pdf"])
            second["invoice"]["invoice_number"] = "200"
            fixture.write_text(json.dumps(_batch(first, second)), encoding="utf-8")

            operational_repository = InMemoryOperationalRepository()
            processor = LocalProcessor(root, InMemoryPolicyRepository(), operational_repository)

            run_id = processor.process_fixture(source_email, fixture)

            self.assertEqual(operational_repository.runs[run_id]["final_outcome"], "AUTO")
            self.assertEqual(len(operational_repository.document_items), 2)
            self.assertEqual(len(operational_repository.actions), 1)
            final_decision = operational_repository.decisions["decision-3"]
            self.assertEqual(final_decision.destination_code, "MEDIUS_PROPERTIES")
            self.assertEqual(final_decision.routing_match["aggregation"]["mode"], "unanimous")

    def test_cover_email_and_attachment_with_same_property_route_unanimously(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_email = root / "local" / "ingest" / "sample.eml"
            fixture = root / "tests" / "fixtures" / "extractions" / "batch.json"
            source_email.parent.mkdir(parents=True)
            fixture.parent.mkdir(parents=True)
            source_email.write_text("sample email", encoding="utf-8")
            attachment = _payload(
                subject="Attached is your invoice #3803 from Routine Vendor LLC",
                vendor_name="Routine Vendor Company",
                property_code="GW9",
                property_name=None,
                business_unit_code=None,
                bill_to="Hillwood Development Company, LLC 9800 Hillwood Parkway Fort Worth TX 76177",
                bill_to_street_address="9800 Hillwood Parkway",
                bill_to_city="Fort Worth",
                bill_to_state="TX",
                bill_to_zip_code="76177",
                service_address="5300 Alliance Gateway Freeway Fort Worth, TX 76177",
                property_lookup={
                    "property_code": ["gw9"],
                    "property_name": [],
                    "tenant": [],
                    "address": ["5300 alliance gateway freeway", "5300 alliance gateway freeway fort worth tx 76177", "9800 hillwood parkway"],
                    "suite": [],
                    "city": ["fort worth"],
                    "state": ["tx"],
                    "zipcode": ["76177"],
                    "address_candidates": [
                        {
                            "rank": 1,
                            "label": "service_location",
                            "street": "5300 alliance gateway freeway",
                            "city": "fort worth",
                            "state": "tx",
                            "zipcode": "76177",
                            "normalized_address": "5300 alliance gateway freeway fort worth tx 76177",
                            "source": "attachment:invoice.pdf:1",
                            "confidence": 0.98,
                            "evidence_text": "Gateway 9 5300 Alliance Gateway Freeway Fort Worth, TX 76177",
                        },
                        {
                            "rank": 2,
                            "label": "bill_to",
                            "street": "9800 hillwood parkway",
                            "city": "fort worth",
                            "state": "tx",
                            "zipcode": "76177",
                            "normalized_address": "9800 hillwood parkway fort worth tx 76177",
                            "source": "attachment:invoice.pdf:1",
                            "confidence": 0.90,
                            "evidence_text": "Bill To Hillwood Development Company 9800 Hillwood Parkway",
                        },
                    ],
                },
                source_attachments=["invoice.pdf"],
                possible_property_aliases=["gateway 9"],
            )
            email = _payload(
                subject="Attached is your invoice #3803 from Routine Vendor LLC",
                vendor_name="Routine Vendor Company",
                property_code="GW9",
                property_name=None,
                business_unit_code=None,
                bill_to=None,
                amount=0,
                service_address=None,
                property_lookup={
                    "property_code": ["gw9"],
                    "property_name": [],
                    "tenant": [],
                    "address": [],
                    "suite": [],
                    "city": [],
                    "state": [],
                    "zipcode": [],
                    "address_candidates": [],
                },
                source_attachments=[],
                possible_property_aliases=["gateway 9"],
                confidence=0.91,
            )
            fixture.write_text(
                json.dumps(
                    {
                        "schema_version": "extraction_batch.v1",
                        "items": [
                            {
                                "item_kind": "attachment",
                                "item_key": "attachment:invoice",
                                "display_name": "invoice.pdf",
                                "metadata": {},
                                "extraction": attachment,
                            },
                            {
                                "item_kind": "email",
                                "item_key": "email:cover",
                                "display_name": "Attached is your invoice #3803 from Routine Vendor LLC",
                                "metadata": {},
                                "extraction": email,
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            operational_repository = InMemoryOperationalRepository()
            processor = LocalProcessor(root, InMemoryPolicyRepository(), operational_repository)

            processor.process_fixture(source_email, fixture)

            final_decision = operational_repository.decisions["decision-3"]
            self.assertEqual(final_decision.outcome, "AUTO")
            self.assertEqual(final_decision.destination_code, "TIFFANY_BECK")
            self.assertEqual(final_decision.matched_rule_code, "property_routing_match")
            aggregation = final_decision.routing_match["aggregation"]
            self.assertEqual(aggregation["mode"], "unanimous")
            self.assertNotIn("aggregation_suppressed", aggregation["item_decisions"][1])

    def test_batch_items_mixed_destinations_escalate_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_email = root / "local" / "ingest" / "sample.eml"
            fixture = root / "tests" / "fixtures" / "extractions" / "batch.json"
            source_email.parent.mkdir(parents=True)
            fixture.parent.mkdir(parents=True)
            source_email.write_text("sample email", encoding="utf-8")
            first = _payload(source_attachments=["a.pdf"])
            second = _payload(source_attachments=["b.pdf"], property_code="EXT1")
            fixture.write_text(json.dumps(_batch(first, second)), encoding="utf-8")

            operational_repository = InMemoryOperationalRepository()
            processor = LocalProcessor(root, InMemoryPolicyRepository(), operational_repository)

            run_id = processor.process_fixture(source_email, fixture)

            self.assertEqual(operational_repository.runs[run_id]["final_outcome"], "ESCALATE")
            self.assertEqual(len(operational_repository.actions), 1)
            final_decision = operational_repository.decisions["decision-3"]
            self.assertEqual(final_decision.matched_rule_code, "hard_mixed_item_destinations")
            self.assertEqual(final_decision.destination_code, "ESCALATE_SPLIT_MULTI_PDF")
            self.assertEqual(final_decision.routing_match["aggregation"]["mode"], "mixed_destinations")

    def test_batch_ap_relevant_second_attachment_is_processed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_email = root / "local" / "ingest" / "sample.eml"
            fixture = root / "tests" / "fixtures" / "extractions" / "batch.json"
            source_email.parent.mkdir(parents=True)
            fixture.parent.mkdir(parents=True)
            source_email.write_text("sample email", encoding="utf-8")
            invoice = _payload(source_attachments=["invoice.pdf"])
            statement = _payload(document_type="statement", source_attachments=["statement.pdf"])
            fixture.write_text(json.dumps(_batch(invoice, statement)), encoding="utf-8")

            operational_repository = InMemoryOperationalRepository()
            processor = LocalProcessor(root, InMemoryPolicyRepository(), operational_repository)

            run_id = processor.process_fixture(source_email, fixture)

            self.assertEqual(operational_repository.runs[run_id]["final_outcome"], "ESCALATE")
            self.assertEqual(len(operational_repository.document_items), 2)
            self.assertEqual(len(operational_repository.decisions), 3)
            final_decision = operational_repository.decisions["decision-3"]
            self.assertEqual(final_decision.matched_rule_code, "hard_mixed_item_destinations")
            self.assertEqual(final_decision.destination_code, "ESCALATE_SPLIT_MULTI_PDF")
            self.assertEqual(final_decision.routing_match["aggregation"]["mode"], "mixed_destinations")

    def test_invoice_with_embedded_photo_references_routes_without_lien_waiver_escalation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_email = root / "local" / "ingest" / "sample.eml"
            fixture = root / "tests" / "fixtures" / "extractions" / "batch.json"
            source_email.parent.mkdir(parents=True)
            fixture.parent.mkdir(parents=True)
            source_email.write_text("sample email", encoding="utf-8")
            invoice = _payload(source_attachments=["invoice.pdf"])
            invoice["observed_facts"]["mentions_separate_backup_document"] = True
            invoice["evidence"]["summary"] = "Invoice PDF includes same-invoice work detail and embedded photo references."
            fixture.write_text(json.dumps(_batch(invoice)), encoding="utf-8")

            operational_repository = InMemoryOperationalRepository()
            processor = LocalProcessor(root, InMemoryPolicyRepository(), operational_repository)

            run_id = processor.process_fixture(source_email, fixture)

            self.assertEqual(operational_repository.runs[run_id]["final_outcome"], "AUTO")
            item_decision = operational_repository.decisions["decision-1"]
            self.assertEqual(item_decision.matched_rule_code, "property_routing_match")
            self.assertNotIn("separate_lien_waiver", operational_repository.extractions[0]["extraction"].document.document_flags)

    def test_invoice_plus_distinct_supporting_document_routes_to_multi_pdf_merge_escalation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_email = root / "local" / "ingest" / "sample.eml"
            fixture = root / "tests" / "fixtures" / "extractions" / "batch.json"
            source_email.parent.mkdir(parents=True)
            fixture.parent.mkdir(parents=True)
            source_email.write_text("sample email", encoding="utf-8")
            invoice = _payload(source_attachments=["invoice.pdf"])
            support = _payload(document_type="unknown", source_attachments=["ticket.pdf"])
            support["document"]["has_invoice_attachment"] = False
            support["invoice"]["invoice_number"] = None
            support["evidence"]["summary"] = "Signed field ticket backup for Vendor at HW1."
            fixture.write_text(json.dumps(_batch(invoice, support)), encoding="utf-8")

            operational_repository = InMemoryOperationalRepository()
            processor = LocalProcessor(root, InMemoryPolicyRepository(), operational_repository)

            run_id = processor.process_fixture(source_email, fixture)

            self.assertEqual(operational_repository.runs[run_id]["final_outcome"], "ESCALATE")
            item_decision = operational_repository.decisions["decision-1"]
            self.assertEqual(item_decision.matched_rule_code, "hard_separate_lien_waiver")
            self.assertEqual(item_decision.destination_code, "ESCALATE_MULTI_PDF_MERGE")

    def test_invoice_plus_staffing_hours_support_routes_to_multi_pdf_merge_escalation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_email = root / "local" / "ingest" / "sample.eml"
            fixture = root / "tests" / "fixtures" / "extractions" / "batch.json"
            source_email.parent.mkdir(parents=True)
            fixture.parent.mkdir(parents=True)
            source_email.write_text("sample email", encoding="utf-8")
            invoice = _payload(
                invoice_number="1069",
                vendor_name="Blue Moon Event Staffing LLC",
                source_attachments=["Invoice_1069_from_Blue_Moon_Event_Staffing_LLC.pdf"],
            )
            support = _payload(
                document_type="unknown",
                invoice_number=None,
                vendor_name=None,
                property_code=None,
                source_attachments=["05 21 CT Kitchen Shift Rpt and Actual Hours Worked.pdf"],
                evidence_summary="Supporting labor shift report/actual hours worked.",
            )
            support["document"]["has_invoice_attachment"] = False
            support["property_lookup"]["property_code"] = None
            fixture.write_text(json.dumps(_batch(invoice, support)), encoding="utf-8")

            operational_repository = InMemoryOperationalRepository()
            processor = LocalProcessor(root, InMemoryPolicyRepository(), operational_repository)

            run_id = processor.process_fixture(source_email, fixture)

            self.assertEqual(operational_repository.runs[run_id]["final_outcome"], "ESCALATE")
            item_decision = operational_repository.decisions["decision-1"]
            self.assertEqual(item_decision.matched_rule_code, "hard_separate_lien_waiver")
            self.assertEqual(item_decision.destination_code, "ESCALATE_MULTI_PDF_MERGE")

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
            self.assertEqual(operational_repository.emails["email-1"]["html_storage_path"], "local/emails/email-1/email.html")
            html_preview = root / operational_repository.emails["email-1"]["html_storage_path"]
            self.assertTrue(html_preview.exists())
            html_content = html_preview.read_text(encoding="utf-8")
            self.assertIn("<!doctype html>", html_content.lower())
            self.assertIn("Attachments", html_content)
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
            self.assertEqual(attachment_step["output_summary"]["html_storage_path"], "local/emails/email-1/email.html")
            self.assertIn("pdf_evaluation_summary", attachment_step["output_summary"])
            self.assertIn("pdf_total", attachment_step["output_summary"]["pdf_evaluation_summary"])
            self.assertIn("pdf_success", attachment_step["output_summary"]["pdf_evaluation_summary"])
            self.assertIn("pdf_failed", attachment_step["output_summary"]["pdf_evaluation_summary"])
            self.assertIn("non_pdf_total", attachment_step["output_summary"]["pdf_evaluation_summary"])
            self.assertTrue(all("pdf_evaluation" in (a.get("metadata") or {}) for a in operational_repository.attachments))

    def test_process_msg_can_use_azure_openai_extractor_without_fixture(self) -> None:
        source_email = Path("reference/test_emails/FW_ Attached is your invoice #2857 from Alliance Landscape Co LLC.msg")
        if not source_email.exists():
            self.skipTest("reference MSG fixture is not available")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            operational_repository = InMemoryOperationalRepository()
            llm_extractor = FakeAzureOpenAIExtractor(
                _payload(document_type="ben_e_keith_notice", flags=["ach_or_auto_draft", "ben_e_keith"])
            )
            processor = LocalProcessor(root, InMemoryPolicyRepository(), operational_repository, llm_extractor, document_intelligence_analyzer=FakeDocumentIntelligenceAnalyzer())

            run_id = processor.process_email(source_email)

            self.assertEqual(operational_repository.runs[run_id]["final_outcome"], "FILE")
            decision = operational_repository.decisions["decision-1"]
            self.assertEqual(decision.destination_code, "FOLDER_BEN_E_KEITH")
            extraction_step = next(step for step in operational_repository.steps if step["step_type"] == "LLM_EXTRACTION")
            self.assertEqual(extraction_step["output_summary"]["extractor_type"], "azure_openai")
            step_types = [step["step_type"] for step in operational_repository.steps]
            self.assertLess(step_types.index("ATTACHMENT_PROCESSING"), step_types.index("DOCUMENT_EXTRACTION_SELECTION"))
            self.assertLess(step_types.index("DOCUMENT_EXTRACTION_SELECTION"), step_types.index("DOCUMENT_INTELLIGENCE"))
            self.assertLess(step_types.index("DOCUMENT_INTELLIGENCE"), step_types.index("LLM_EXTRACTION"))
            di_step = next(step for step in operational_repository.steps if step["step_type"] == "DOCUMENT_INTELLIGENCE")
            selection_step = next(step for step in operational_repository.steps if step["step_type"] == "DOCUMENT_EXTRACTION_SELECTION")
            self.assertIn("selected_extractors", selection_step["output_summary"])
            self.assertGreaterEqual(sum(selection_step["output_summary"]["selected_extractors"].values()), 1)
            self.assertGreater(len(llm_extractor.attachment_records), 0)
            self.assertTrue(all("extractor_selection" in (a.get("metadata") or {}) for a in operational_repository.attachments))
            first_metadata = llm_extractor.attachment_records[0].get("metadata") or {}
            if first_metadata.get("extractor_selection", {}).get("selected_extractor") == "document_intelligence":
                self.assertEqual(llm_extractor.attachment_records[0].get("text_excerpt"), "di invoice text")
            else:
                self.assertEqual(first_metadata.get("extractor_selection", {}).get("selected_extractor"), "pymupdf")
                self.assertIsInstance(llm_extractor.attachment_records[0].get("text_excerpt"), str)
            self.assertEqual(len(operational_repository.llm_interactions), 1)
            self.assertEqual(operational_repository.llm_interactions[0]["interaction_type"], "extraction")
            self.assertEqual(operational_repository.llm_interactions[0]["provider"], "azure_openai")
            self.assertEqual(operational_repository.llm_interactions[0]["status"], "completed")
            self.assertEqual(operational_repository.llm_interactions[0]["model_name"], "gpt-test")
            self.assertEqual(operational_repository.llm_interactions[0]["deployment_name"], "ap-extractor")
            self.assertEqual(operational_repository.llm_interactions[0]["api_version"], "2024-10-21")
            self.assertEqual(operational_repository.llm_interactions[0]["prompt_tokens"], 120)
            self.assertEqual(operational_repository.llm_interactions[0]["completion_tokens"], 80)
            self.assertEqual(operational_repository.llm_interactions[0]["total_tokens"], 200)
            self.assertEqual(operational_repository.llm_interactions[0]["cached_prompt_tokens"], 10)
            self.assertEqual(operational_repository.llm_interactions[0]["reasoning_tokens"], 5)
            self.assertEqual(operational_repository.llm_interactions[0]["latency_ms"], 1234)
            self.assertEqual(
                operational_repository.llm_interactions[0]["response_artifact_path"],
                "local/audit/extractions/run-1.json",
            )
            self.assertEqual(
                operational_repository.llm_interactions[0]["request_parameters"],
                {"temperature": 0, "response_format": {"type": "json_object"}},
            )
            self.assertGreater(len(llm_extractor.asset_reference_rows), 0)

    def test_triage_past_due_flag_does_not_override_account_aging_detail_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_email = root / "local" / "ingest" / "gateway-aging.msg"
            source_email.parent.mkdir(parents=True)
            source_email.write_bytes(b"not-real-msg")
            payload = _payload(
                subject="Attached is your invoice #3735 from Alliance Landscape Co LLC",
                document_type="invoice",
                invoice_number="3735",
                invoice_date="2026-06-01",
                due_date=None,
                amount=1213.57,
                vendor_name="Alliance Landscape Company",
                property_code="GW34",
                property_name="Alliance Gateway 34",
                service_address="3202 Alliance Gateway Freeway Fort Worth, TX 76177",
                bill_to="Hillwood Development Company, LLC 9800 Hillwood Parkway Suite 300 Fort Worth, TX 76177",
                source_attachments=["Invoice 3735 - Gateway 34.pdf"],
                evidence_summary=(
                    "Invoice 3735 shows balance due $1,213.57 and an account-level aging table. "
                    "The current invoice is not explicitly past due."
                ),
            )
            payload["observed_facts"]["contains_aging_summary"] = True
            payload["observed_facts"]["account_has_past_due_aging_balance"] = True
            payload["observed_facts"]["current_invoice_is_past_due"] = False
            parsed_msg = ParsedMsg(
                subject="Attached is your invoice #3735 from Alliance Landscape Co LLC",
                sender_email="casey.miner@hillwood.com",
                sender_name="Miner, Casey",
                received_at=None,
                body_text="Attached is Invoice #3735 for work completed at Gateway 34.",
                transport_headers=None,
                attachments=(ParsedAttachment("Invoice 3735 - Gateway 34.pdf", b"%PDF fake", "application/pdf", {}),),
                metadata={},
            )
            operational_repository = InMemoryOperationalRepository()
            llm_extractor = PastDueTriageFakeAzureOpenAIExtractor(payload)
            processor = LocalProcessor(
                root,
                AllianceGatewayPolicyRepository(),
                operational_repository,
                llm_extractor,
                document_intelligence_analyzer=FakeDocumentIntelligenceAnalyzer(),
            )

            with patch("ap_automation.services.local_processor._parse_source_email", return_value=parsed_msg):
                run_id = processor.process_email(source_email)

            decision = operational_repository.decisions["decision-1"]
            self.assertEqual(operational_repository.runs[run_id]["final_outcome"], "AUTO")
            self.assertEqual(decision.matched_rule_code, "property_routing_match")
            self.assertEqual(decision.destination_code, "MEDIUS_PROPERTIES")
            extraction = operational_repository.extractions[0]["extraction"]
            self.assertIsNotNone(extraction)
            self.assertTrue(extraction.observed_facts.contains_aging_summary)
            self.assertTrue(extraction.observed_facts.account_has_past_due_aging_balance)
            self.assertFalse(extraction.observed_facts.current_invoice_is_past_due)
            self.assertNotIn("past_due", extraction.document.document_flags)

    def test_due_on_receipt_invoice_date_before_received_does_not_route_to_past_due(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_email = root / "local" / "ingest" / "three-branches-floral.msg"
            source_email.parent.mkdir(parents=True)
            source_email.write_bytes(b"not-real-msg")
            payload = _payload(
                subject="FW: New payment request from THREE BRANCHES FLORAL LLC - invoice 2684",
                document_type="invoice",
                invoice_number="2684",
                invoice_date="2026-06-09",
                due_date="2026-06-09",
                amount=21007.27,
                vendor_name="THREE BRANCHES FLORAL LLC",
                property_code=None,
                property_name=None,
                service_address=None,
                bill_to="Brianna Broussard",
                source_attachments=["Invoice_2684_from_THREE_BRANCHES_FLORAL_LLC.pdf"],
                evidence_summary=(
                    "Invoice 2684 says Due Date 06/09/2026, Terms Due on receipt, "
                    "and Balance Due $21,007.27. It does not explicitly say past due or overdue."
                ),
            )
            payload["email"]["received_at"] = "2026-06-10T21:09:21+00:00"
            payload["observed_facts"]["current_invoice_is_past_due"] = False
            parsed_msg = ParsedMsg(
                subject="FW: New payment request from THREE BRANCHES FLORAL LLC - invoice 2684",
                sender_email="Courtney.Gonzalez@hillwood.com",
                sender_name="Gonzalez, Courtney",
                received_at=None,
                body_text=(
                    "Your invoice is ready!\nBALANCE DUE$21,007.27\n"
                    "Please find the invoice for the FIFA event on 6/14."
                ),
                transport_headers=None,
                attachments=(
                    ParsedAttachment(
                        "Invoice_2684_from_THREE_BRANCHES_FLORAL_LLC.pdf",
                        b"%PDF fake",
                        "application/pdf",
                        {},
                    ),
                ),
                metadata={},
            )
            operational_repository = InMemoryOperationalRepository()
            processor = LocalProcessor(
                root,
                InMemoryPolicyRepository(),
                operational_repository,
                FakeAzureOpenAIExtractor(payload),
                document_intelligence_analyzer=FakeDocumentIntelligenceAnalyzer(),
            )

            with patch("ap_automation.services.local_processor._parse_source_email", return_value=parsed_msg):
                run_id = processor.process_email(source_email)

            decision = operational_repository.decisions["decision-1"]
            self.assertEqual(operational_repository.runs[run_id]["final_outcome"], "ESCALATE")
            self.assertNotEqual(decision.destination_code, "ESCALATE_PAST_DUE")
            self.assertNotEqual(decision.matched_rule_code, "hard_past_due_notice")
            extraction = operational_repository.extractions[0]["extraction"]
            self.assertIsNotNone(extraction)
            self.assertFalse(extraction.observed_facts.current_invoice_is_past_due)
            self.assertNotIn("past_due", extraction.document.document_flags)

    def test_triage_multi_invoice_flag_does_not_override_single_invoice_detail_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_email = root / "local" / "ingest" / "single-invoice-aging.msg"
            source_email.parent.mkdir(parents=True)
            source_email.write_bytes(b"not-real-msg")
            payload = _payload(
                subject="Attached is your invoice #3685 from Alliance Landscape Co LLC",
                document_type="invoice",
                invoice_number="3685",
                invoice_date="2026-06-03",
                due_date=None,
                amount=210.24,
                vendor_name="Alliance Landscape Company",
                property_code="HW1",
                property_name="Hillwood One",
                bill_to="Hillwood Development Company, LLC 9800 Hillwood Parkway Suite 300 Fort Worth, TX 76177",
                source_attachments=["Invoice 3685 - ACN 15.pdf"],
                evidence_summary=(
                    "Single invoice 3685 shows one balance due, line items, and an aging table with current balance only."
                ),
            )
            payload["document"]["multi_invoice"] = False
            payload["observed_facts"]["indicates_multiple_invoices"] = False
            payload["observed_facts"]["contains_aging_summary"] = True
            parsed_msg = ParsedMsg(
                subject="Attached is your invoice #3685 from Alliance Landscape Co LLC",
                sender_email="casey.miner@hillwood.com",
                sender_name="Miner, Casey",
                received_at=None,
                body_text="Attached is Invoice #3685 for work completed at ACN 15.",
                transport_headers=None,
                attachments=(ParsedAttachment("Invoice 3685 - ACN 15.pdf", b"%PDF fake", "application/pdf", {}),),
                metadata={},
            )
            operational_repository = InMemoryOperationalRepository()
            llm_extractor = MultiInvoiceTriageFakeAzureOpenAIExtractor(payload)
            processor = LocalProcessor(
                root,
                InMemoryPolicyRepository(),
                operational_repository,
                llm_extractor,
                document_intelligence_analyzer=FakeDocumentIntelligenceAnalyzer(),
            )

            with patch("ap_automation.services.local_processor._parse_source_email", return_value=parsed_msg):
                run_id = processor.process_email(source_email)

            decision = operational_repository.decisions["decision-1"]
            self.assertEqual(operational_repository.runs[run_id]["final_outcome"], "AUTO")
            self.assertEqual(decision.matched_rule_code, "property_routing_match")
            extraction = operational_repository.extractions[0]["extraction"]
            self.assertIsNotNone(extraction)
            self.assertFalse(extraction.document.multi_invoice)
            self.assertFalse(extraction.observed_facts.indicates_multiple_invoices)
            self.assertNotIn("multi_invoice_pdf", extraction.document.document_flags)

    def test_triage_multi_invoice_flag_still_escalates_when_detail_extraction_agrees(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_email = root / "local" / "ingest" / "multi-invoice.msg"
            source_email.parent.mkdir(parents=True)
            source_email.write_bytes(b"not-real-msg")
            payload = _payload(
                subject="Multiple invoices attached",
                document_type="invoice",
                invoice_number="3685",
                amount=210.24,
                vendor_name="Alliance Landscape Company",
                property_code="HW1",
                property_name="Hillwood One",
                source_attachments=["Invoice packet.pdf"],
                evidence_summary="One PDF contains multiple distinct invoice sections.",
                multi_invoice=True,
            )
            payload["observed_facts"]["indicates_multiple_invoices"] = True
            parsed_msg = ParsedMsg(
                subject="Multiple invoices attached",
                sender_email="vendor@example.com",
                sender_name="Vendor",
                received_at=None,
                body_text="Please see invoice packet.",
                transport_headers=None,
                attachments=(ParsedAttachment("Invoice packet.pdf", b"%PDF fake", "application/pdf", {}),),
                metadata={},
            )
            operational_repository = InMemoryOperationalRepository()
            processor = LocalProcessor(
                root,
                InMemoryPolicyRepository(),
                operational_repository,
                FakeAzureOpenAIExtractor(payload),
                document_intelligence_analyzer=FakeDocumentIntelligenceAnalyzer(),
            )

            with patch("ap_automation.services.local_processor._parse_source_email", return_value=parsed_msg):
                run_id = processor.process_email(source_email)

            decision = operational_repository.decisions["decision-1"]
            self.assertEqual(operational_repository.runs[run_id]["final_outcome"], "ESCALATE")
            self.assertEqual(decision.matched_rule_code, "hard_multi_invoice_pdf")
            extraction = operational_repository.extractions[0]["extraction"]
            self.assertIsNotNone(extraction)
            self.assertIn("multi_invoice_pdf", extraction.document.document_flags)

    def test_azure_extraction_receives_asset_reference_rows_for_canonical_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_email = root / "local" / "ingest" / "sample.msg"
            source_email.parent.mkdir(parents=True)
            source_email.write_bytes(b"not-real-msg")
            parsed_msg = ParsedMsg(
                subject="Invoice for 3202 Alliance Gateway 34 Shell Bldg.",
                sender_email="vendor@example.com",
                sender_name="Vendor",
                received_at=None,
                body_text="Invoice site: 3202 Alliance Gateway 34 Shell Bldg.",
                transport_headers=None,
                attachments=(),
                metadata={},
            )
            operational_repository = InMemoryOperationalRepository()
            policy_repository = AllianceGatewayPolicyRepository()
            llm_extractor = AssetAwareFakeAzureOpenAIExtractor()
            processor = LocalProcessor(root, policy_repository, operational_repository, llm_extractor, document_intelligence_analyzer=FakeDocumentIntelligenceAnalyzer())

            with patch("ap_automation.services.local_processor._parse_source_email", return_value=parsed_msg):
                run_id = processor.process_email(source_email)

            self.assertEqual(operational_repository.runs[run_id]["status"], "completed")
            self.assertEqual(llm_extractor.asset_reference_rows[0]["asset_name"], "Alliance Gateway 34")
            saved_extraction = operational_repository.extractions[0]["extraction"]
            self.assertEqual(saved_extraction.property_lookup.property_name, ("alliance gateway 34",))
            self.assertEqual(saved_extraction.property_lookup.property_code, ("gw34",))

    def test_azure_batch_excludes_irrelevant_jpeg_from_item_decisions_and_audit_marks_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_email = root / "local" / "ingest" / "sample.msg"
            source_email.parent.mkdir(parents=True)
            source_email.write_bytes(b"not-real-msg")
            parsed_msg = ParsedMsg(
                subject="Invoice with site photo",
                sender_email="vendor@example.com",
                sender_name="Vendor",
                received_at=None,
                body_text="Attached invoice and a site photo.",
                transport_headers=None,
                attachments=(
                    ParsedAttachment("invoice.pdf", b"%PDF-1.4 invoice", "application/pdf", {}),
                    ParsedAttachment("photo.jpg", b"jpeg bytes", "image/jpeg", {}),
                ),
                metadata={},
            )
            invoice = _payload(source_attachments=["invoice.pdf"])
            batch_payload = {
                "schema_version": "extraction_batch.v1",
                "excluded_attachments": [
                    {
                        "file_name": "photo.jpg",
                        "reason_code": "irrelevant_to_ap_workflow",
                        "reason": "Generic site photo with no invoice or AP workflow facts.",
                        "source": "document_intelligence",
                    }
                ],
                "items": [
                    {
                        "item_kind": "attachment",
                        "item_key": "attachment:invoice",
                        "display_name": "invoice.pdf",
                        "metadata": {},
                        "extraction": invoice,
                    }
                ],
            }
            llm_extractor = FakeAzureOpenAIExtractor(batch_payload)
            operational_repository = InMemoryOperationalRepository()
            processor = LocalProcessor(
                root,
                InMemoryPolicyRepository(),
                operational_repository,
                llm_extractor,
                document_intelligence_analyzer=FakeDocumentIntelligenceAnalyzer(),
            )

            with patch("ap_automation.services.local_processor._parse_source_email", return_value=parsed_msg):
                run_id = processor.process_email(source_email)

            self.assertEqual(operational_repository.runs[run_id]["final_outcome"], "AUTO")
            self.assertEqual(len(operational_repository.document_items), 1)
            self.assertEqual(operational_repository.document_items[0]["display_name"], "invoice.pdf")
            validation_step = next(step for step in operational_repository.steps if step["step_type"] == "VALIDATION")
            self.assertEqual(
                validation_step["output_summary"]["not_selected_attachments"][0]["reason"],
                "excluded_by_extractor",
            )
            self.assertEqual(
                validation_step["output_summary"]["not_selected_attachments"][0]["extractor_exclusion"]["reason_code"],
                "irrelevant_to_ap_workflow",
            )

    def test_cited_excluded_ben_e_keith_excel_attachment_routes_to_ben_e_keith_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_email = root / "local" / "ingest" / "sample.msg"
            source_email.parent.mkdir(parents=True)
            source_email.write_bytes(b"not-real-msg")
            parsed_msg = ParsedMsg(
                subject="80040277-ACH EzPay Projected Payment",
                sender_email="FDFWACH@BENEKEITH.COM",
                sender_name="Ben E Keith",
                received_at=None,
                body_text="Projected payment notice from Ben E Keith.",
                transport_headers=None,
                attachments=(
                    ParsedAttachment(
                        "752944_Projected Payment.xlsx",
                        b"xlsx bytes",
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        {},
                    ),
                ),
                metadata={},
            )
            notice = _payload(
                document_type="ben_e_keith_notice",
                flags=["ach_or_auto_draft", "ben_e_keith"],
                source_attachments=["752944_Projected Payment.xlsx"],
                subject="80040277-ACH EzPay Projected Payment",
                sender_email="FDFWACH@BENEKEITH.COM",
            )
            batch_payload = {
                "schema_version": "extraction_batch.v1",
                "excluded_attachments": [
                    {
                        "file_name": "752944_Projected Payment.xlsx",
                        "reason_code": "unsupported_file_type",
                        "reason": "Spreadsheet attachment was not eligible for PDF extraction.",
                        "source": "filename",
                    }
                ],
                "items": [
                    {
                        "item_kind": "attachment",
                        "item_key": "attachment:752944",
                        "display_name": "752944_Projected Payment.xlsx",
                        "metadata": {},
                        "extraction": notice,
                    }
                ],
            }
            llm_extractor = FakeAzureOpenAIExtractor(batch_payload)
            operational_repository = InMemoryOperationalRepository()
            processor = LocalProcessor(
                root,
                InMemoryPolicyRepository(),
                operational_repository,
                llm_extractor,
                document_intelligence_analyzer=FakeDocumentIntelligenceAnalyzer(),
            )

            with patch("ap_automation.services.local_processor._parse_source_email", return_value=parsed_msg):
                run_id = processor.process_email(source_email)

            self.assertEqual(operational_repository.runs[run_id]["status"], "completed")
            self.assertEqual(operational_repository.runs[run_id]["final_outcome"], "FILE")
            final_decision = operational_repository.decisions["decision-2"]
            self.assertEqual(final_decision.destination_code, "FOLDER_BEN_E_KEITH")
            self.assertEqual(final_decision.matched_rule_code, "ben_e_keith_notice_file")
            validation_step = next(step for step in operational_repository.steps if step["step_type"] == "VALIDATION")
            self.assertEqual(validation_step["output_summary"]["validation_status"], "valid_after_normalization")
            self.assertEqual(
                validation_step["output_summary"]["normalization"]["excluded_attachment_conflicts"]["restored_attachments"][0]["file_name"],
                "752944_Projected Payment.xlsx",
            )
            self.assertFalse(any(step["step_type"] == "FINALIZE" and step.get("error") for step in operational_repository.steps))

    def test_cited_excluded_generic_excel_attachment_routes_to_wrong_file_type_escalation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_email = root / "local" / "ingest" / "sample.msg"
            source_email.parent.mkdir(parents=True)
            source_email.write_bytes(b"not-real-msg")
            parsed_msg = ParsedMsg(
                subject="Invoice with spreadsheet",
                sender_email="vendor@example.com",
                sender_name="Vendor",
                received_at=None,
                body_text="Attached invoice spreadsheet.",
                transport_headers=None,
                attachments=(
                    ParsedAttachment(
                        "invoice.xlsx",
                        b"xlsx bytes",
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        {},
                    ),
                ),
                metadata={},
            )
            invoice = _payload(source_attachments=["invoice.xlsx"])
            batch_payload = {
                "schema_version": "extraction_batch.v1",
                "excluded_attachments": [
                    {
                        "file_name": "invoice.xlsx",
                        "reason_code": "unsupported_file_type",
                        "reason": "Spreadsheet attachment was not eligible for PDF extraction.",
                        "source": "filename",
                    }
                ],
                "items": [
                    {
                        "item_kind": "attachment",
                        "item_key": "attachment:invoice",
                        "display_name": "invoice.xlsx",
                        "metadata": {},
                        "extraction": invoice,
                    }
                ],
            }
            llm_extractor = FakeAzureOpenAIExtractor(batch_payload)
            operational_repository = InMemoryOperationalRepository()
            processor = LocalProcessor(
                root,
                InMemoryPolicyRepository(),
                operational_repository,
                llm_extractor,
                document_intelligence_analyzer=FakeDocumentIntelligenceAnalyzer(),
            )

            with patch("ap_automation.services.local_processor._parse_source_email", return_value=parsed_msg):
                run_id = processor.process_email(source_email)

            self.assertEqual(operational_repository.runs[run_id]["status"], "completed")
            self.assertEqual(operational_repository.runs[run_id]["final_outcome"], "ESCALATE")
            final_decision = operational_repository.decisions["decision-2"]
            self.assertEqual(final_decision.destination_code, "ESCALATE_WRONG_FILE_TYPE")
            self.assertEqual(final_decision.matched_rule_code, "hard_wrong_file_type")

    def test_azure_batch_excludes_payment_instruction_support_from_item_decisions_and_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_email = root / "local" / "ingest" / "sample.msg"
            source_email.parent.mkdir(parents=True)
            source_email.write_bytes(b"not-real-msg")
            parsed_msg = ParsedMsg(
                subject="Invoices with wire instructions",
                sender_email="vendor@example.com",
                sender_name="Vendor",
                received_at=None,
                body_text="Attached are invoices and wire instructions.",
                transport_headers=None,
                attachments=(
                    ParsedAttachment("invoice-100.pdf", b"%PDF-1.4 invoice 100", "application/pdf", {}),
                    ParsedAttachment("invoice-200.pdf", b"%PDF-1.4 invoice 200", "application/pdf", {}),
                    ParsedAttachment("wire-instructions.pdf", b"%PDF-1.4 wire instructions", "application/pdf", {}),
                ),
                metadata={},
            )
            first = _payload(invoice_number="100", source_attachments=["invoice-100.pdf"])
            second = _payload(invoice_number="200", source_attachments=["invoice-200.pdf"])
            batch_payload = _batch(first, second)
            batch_payload["excluded_attachments"] = [
                {
                    "file_name": "wire-instructions.pdf",
                    "reason_code": "payment_instruction_support",
                    "reason": "Standalone wire instructions attached with separate invoice PDFs.",
                    "source": "pymupdf",
                }
            ]
            llm_extractor = FakeAzureOpenAIExtractor(batch_payload)
            operational_repository = InMemoryOperationalRepository()
            processor = LocalProcessor(
                root,
                InMemoryPolicyRepository(),
                operational_repository,
                llm_extractor,
                document_intelligence_analyzer=FakeDocumentIntelligenceAnalyzer(),
            )

            with patch("ap_automation.services.local_processor._parse_source_email", return_value=parsed_msg):
                run_id = processor.process_email(source_email)

            self.assertEqual(operational_repository.runs[run_id]["final_outcome"], "AUTO")
            self.assertEqual(len(operational_repository.document_items), 2)
            self.assertEqual([item["display_name"] for item in operational_repository.document_items], ["invoice-100.pdf", "invoice-200.pdf"])
            self.assertEqual(len(operational_repository.decisions), 3)
            final_decision = operational_repository.decisions["decision-3"]
            self.assertEqual(final_decision.destination_code, "MEDIUS_PROPERTIES")
            validation_step = next(step for step in operational_repository.steps if step["step_type"] == "VALIDATION")
            not_selected = validation_step["output_summary"]["not_selected_attachments"]
            self.assertEqual(len(not_selected), 1)
            self.assertEqual(not_selected[0]["file_name"], "wire-instructions.pdf")
            self.assertEqual(not_selected[0]["reason"], "excluded_by_extractor")
            self.assertEqual(not_selected[0]["extractor_exclusion"]["reason_code"], "payment_instruction_support")

    def test_standalone_ach_instruction_item_is_not_silently_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_email = root / "local" / "ingest" / "sample.msg"
            source_email.parent.mkdir(parents=True)
            source_email.write_bytes(b"not-real-msg")
            parsed_msg = ParsedMsg(
                subject="ACH instructions",
                sender_email="vendor@example.com",
                sender_name="Vendor",
                received_at=None,
                body_text="Attached ACH payment instructions.",
                transport_headers=None,
                attachments=(ParsedAttachment("ach-instructions.pdf", b"%PDF-1.4 ach", "application/pdf", {}),),
                metadata={},
            )
            ach_item = _payload(document_type="ach_notice", source_attachments=["ach-instructions.pdf"])
            ach_item["observed_facts"]["indicates_ach_or_auto_draft"] = True
            batch_payload = _batch(ach_item)
            llm_extractor = FakeAzureOpenAIExtractor(batch_payload)
            operational_repository = InMemoryOperationalRepository()
            processor = LocalProcessor(
                root,
                InMemoryPolicyRepository(),
                operational_repository,
                llm_extractor,
                document_intelligence_analyzer=FakeDocumentIntelligenceAnalyzer(),
            )

            with patch("ap_automation.services.local_processor._parse_source_email", return_value=parsed_msg):
                run_id = processor.process_email(source_email)

            self.assertEqual(len(operational_repository.document_items), 1)
            self.assertEqual(operational_repository.document_items[0]["display_name"], "ach-instructions.pdf")
            self.assertEqual(operational_repository.runs[run_id]["final_outcome"], "FILE")
            decision = operational_repository.decisions["decision-1"]
            self.assertEqual(decision.destination_code, "FOLDER_ACH")

    def test_payment_instruction_with_exception_content_remains_item_and_escalates(self) -> None:
        cases = [
            ("vendor-question.pdf", "vendor_question", {}, "hard_vendor_inquiry"),
            ("dispute.pdf", "payment_inquiry", {}, "hard_vendor_inquiry"),
            ("missing-remittance.pdf", "payment_inquiry", {}, "hard_vendor_inquiry"),
            ("statement.pdf", "statement", {}, "statement_file"),
            ("lien-waiver.pdf", "lien_release", {"mentions_lien_waiver_or_release": True}, "fallback_escalate"),
            ("contract.pdf", "contract", {}, "hard_contract_or_pay_app"),
            ("pay-app.pdf", "pay_application", {}, "hard_contract_or_pay_app"),
        ]
        for file_name, document_type, observed_updates, expected_rule in cases:
            with self.subTest(file_name=file_name), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                source_email = root / "local" / "ingest" / "sample.msg"
                source_email.parent.mkdir(parents=True)
                source_email.write_bytes(b"not-real-msg")
                parsed_msg = ParsedMsg(
                    subject="Payment instruction exception",
                    sender_email="vendor@example.com",
                    sender_name="Vendor",
                    received_at=None,
                    body_text="Attached payment instruction support requires review.",
                    transport_headers=None,
                    attachments=(ParsedAttachment(file_name, b"%PDF-1.4 support", "application/pdf", {}),),
                    metadata={},
                )
                item = _payload(document_type=document_type, source_attachments=[file_name])
                for key, value in observed_updates.items():
                    item["observed_facts"][key] = value
                batch_payload = _batch(item)
                llm_extractor = FakeAzureOpenAIExtractor(batch_payload)
                operational_repository = InMemoryOperationalRepository()
                processor = LocalProcessor(
                    root,
                    InMemoryPolicyRepository(),
                    operational_repository,
                    llm_extractor,
                    document_intelligence_analyzer=FakeDocumentIntelligenceAnalyzer(),
                )

                with patch("ap_automation.services.local_processor._parse_source_email", return_value=parsed_msg):
                    run_id = processor.process_email(source_email)

                self.assertEqual(len(operational_repository.document_items), 1)
                expected_outcome = "FILE" if expected_rule == "statement_file" else "ESCALATE"
                self.assertEqual(operational_repository.runs[run_id]["final_outcome"], expected_outcome)
                decision = operational_repository.decisions["decision-1"]
                self.assertEqual(decision.matched_rule_code, expected_rule)

    def test_standalone_contractor_timesheet_routes_to_dedicated_escalation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_email = root / "local" / "ingest" / "sample.msg"
            source_email.parent.mkdir(parents=True)
            source_email.write_bytes(b"not-real-msg")
            parsed_msg = ParsedMsg(
                subject="Contractor timesheet",
                sender_email="vendor@example.com",
                sender_name="Vendor",
                received_at=None,
                body_text="Attached contractor timesheet with no invoice.",
                transport_headers=None,
                attachments=(ParsedAttachment("contractor-timesheet.pdf", b"%PDF-1.4 timesheet", "application/pdf", {}),),
                metadata={},
            )
            item = _payload(document_type="unknown", source_attachments=["contractor-timesheet.pdf"])
            item["document"]["has_invoice_attachment"] = False
            item["invoice"]["invoice_number"] = None
            item["invoice"]["property_code"] = None
            item["property_lookup"]["property_code"] = None
            item["evidence"]["summary"] = "Contractor timesheet with actual hours worked and no invoice."
            batch_payload = _batch(item)
            llm_extractor = FakeAzureOpenAIExtractor(batch_payload)
            operational_repository = InMemoryOperationalRepository()
            processor = LocalProcessor(
                root,
                InMemoryPolicyRepository(),
                operational_repository,
                llm_extractor,
                document_intelligence_analyzer=FakeDocumentIntelligenceAnalyzer(),
            )

            with patch("ap_automation.services.local_processor._parse_source_email", return_value=parsed_msg):
                run_id = processor.process_email(source_email)

            self.assertEqual(operational_repository.runs[run_id]["final_outcome"], "ESCALATE")
            decision = operational_repository.decisions["decision-1"]
            self.assertEqual(decision.matched_rule_code, "hard_contractor_timesheet_no_invoice")
            self.assertEqual(decision.destination_code, "ESCALATE_CONTRACTOR_TIMESHEET")

    def test_azure_batch_wrongly_returned_jpeg_item_still_escalates_wrong_file_type(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_email = root / "local" / "ingest" / "sample.msg"
            source_email.parent.mkdir(parents=True)
            source_email.write_bytes(b"not-real-msg")
            parsed_msg = ParsedMsg(
                subject="Invoice with site photo",
                sender_email="vendor@example.com",
                sender_name="Vendor",
                received_at=None,
                body_text="Attached invoice and a site photo.",
                transport_headers=None,
                attachments=(
                    ParsedAttachment("invoice.pdf", b"%PDF-1.4 invoice", "application/pdf", {}),
                    ParsedAttachment("photo.jpg", b"jpeg bytes", "image/jpeg", {}),
                ),
                metadata={},
            )
            invoice = _payload(source_attachments=["invoice.pdf"])
            photo_item = _payload(source_attachments=["photo.jpg"])
            batch_payload = _batch(invoice, photo_item)
            llm_extractor = FakeAzureOpenAIExtractor(batch_payload)
            operational_repository = InMemoryOperationalRepository()
            processor = LocalProcessor(
                root,
                InMemoryPolicyRepository(),
                operational_repository,
                llm_extractor,
                document_intelligence_analyzer=FakeDocumentIntelligenceAnalyzer(),
            )

            with patch("ap_automation.services.local_processor._parse_source_email", return_value=parsed_msg):
                run_id = processor.process_email(source_email)

            self.assertEqual(operational_repository.runs[run_id]["final_outcome"], "ESCALATE")
            self.assertEqual(len(operational_repository.document_items), 2)
            final_decision = operational_repository.decisions["decision-3"]
            self.assertEqual(final_decision.matched_rule_code, "hard_wrong_file_type")
            self.assertEqual(final_decision.destination_code, "ESCALATE_WRONG_FILE_TYPE")

    def test_azure_extraction_retries_invalid_contract_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_email = root / "local" / "ingest" / "sample.msg"
            source_email.parent.mkdir(parents=True)
            source_email.write_bytes(b"not-real-msg")
            parsed_msg = ParsedMsg(
                subject="Invoice",
                sender_email="vendor@example.com",
                sender_name="Vendor",
                received_at=None,
                body_text="Invoice body",
                transport_headers=None,
                attachments=(),
                metadata={},
            )
            invalid_payload = _payload()
            invalid_payload["property_lookup"]["address_candidates"] = [
                {
                    "rank": 1,
                    "label": "invalid_label",
                    "street": "123 main street",
                    "city": None,
                    "state": None,
                    "zipcode": None,
                    "normalized_address": "123 main street",
                    "confidence": "high",
                }
            ]
            valid_payload = _payload()
            llm_extractor = RetryingAzureOpenAIExtractor(invalid_payload, valid_payload)
            operational_repository = InMemoryOperationalRepository()
            processor = LocalProcessor(root, InMemoryPolicyRepository(), operational_repository, llm_extractor, document_intelligence_analyzer=FakeDocumentIntelligenceAnalyzer())

            with patch("ap_automation.services.local_processor._parse_source_email", return_value=parsed_msg):
                run_id = processor.process_email(source_email)

            self.assertEqual(operational_repository.runs[run_id]["status"], "completed")
            self.assertGreaterEqual(llm_extractor.json_prompt_calls, 2)
            validation_step = next(step for step in operational_repository.steps if step["step_type"] == "VALIDATION")
            self.assertEqual(validation_step["output_summary"]["validation_status"], "valid_after_retry")
            self.assertEqual(validation_step["output_summary"]["retry_count"], 1)
            attempts = operational_repository.extractions[0]["raw_output"]["attempts"]
            self.assertEqual([attempt["attempt"] for attempt in attempts], [1, 2])
            self.assertIn("address_candidates", attempts[0]["validation_errors"][0])
            self.assertIn("contract_lint", attempts[0])
            self.assertTrue(attempts[1]["changed_payload"])
            self.assertEqual(validation_step["output_summary"]["initial_validation_errors"], attempts[0]["validation_errors"])

    def test_azure_triage_invalid_contract_fails_before_detail_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_email = root / "local" / "ingest" / "sample.msg"
            source_email.parent.mkdir(parents=True)
            source_email.write_bytes(b"not-real-msg")
            parsed_msg = ParsedMsg(
                subject="Invoice",
                sender_email="vendor@example.com",
                sender_name="Vendor",
                received_at=None,
                body_text="Invoice body",
                transport_headers=None,
                attachments=(),
                metadata={},
            )
            llm_extractor = BadTriageAzureOpenAIExtractor(_payload())
            operational_repository = InMemoryOperationalRepository()
            processor = LocalProcessor(
                root,
                InMemoryPolicyRepository(),
                operational_repository,
                llm_extractor,
                document_intelligence_analyzer=FakeDocumentIntelligenceAnalyzer(),
            )

            with self.assertRaises(ExtractionValidationError):
                with patch("ap_automation.services.local_processor._parse_source_email", return_value=parsed_msg):
                    processor.process_email(source_email)

            self.assertEqual(llm_extractor.detail_calls, 0)
            finalize_errors = [
                step["error"]
                for step in operational_repository.steps
                if step["step_type"] == "FINALIZE" and step.get("error")
            ]
            self.assertTrue(any("schema_version must be extraction_triage_batch.v1" in error for error in finalize_errors))

    def test_azure_extraction_retry_gets_advisory_lint_for_misspelled_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_email = root / "local" / "ingest" / "sample.msg"
            source_email.parent.mkdir(parents=True)
            source_email.write_bytes(b"not-real-msg")
            parsed_msg = ParsedMsg(
                subject="Invoice",
                sender_email="vendor@example.com",
                sender_name="Vendor",
                received_at=None,
                body_text="Invoice body",
                transport_headers=None,
                attachments=(),
                metadata={},
            )
            invalid_payload = _payload()
            invalid_payload["observed_facts"]["indicates_ben_e_kieth"] = invalid_payload["observed_facts"].pop("indicates_ben_e_keith")
            valid_payload = _payload()
            llm_extractor = RetryingAzureOpenAIExtractor(invalid_payload, valid_payload)
            operational_repository = InMemoryOperationalRepository()
            processor = LocalProcessor(root, InMemoryPolicyRepository(), operational_repository, llm_extractor, document_intelligence_analyzer=FakeDocumentIntelligenceAnalyzer())

            with patch("ap_automation.services.local_processor._parse_source_email", return_value=parsed_msg):
                processor.process_email(source_email)

            attempts = operational_repository.extractions[0]["raw_output"]["attempts"]
            lint = attempts[0]["contract_lint"]
            self.assertIn("extraction.observed_facts.indicates_ben_e_keith", lint["missing_required_keys"])
            self.assertIn("extraction.observed_facts.indicates_ben_e_kieth", lint["unknown_keys"])
            self.assertIn("indicates_ben_e_kieth", llm_extractor.prompts[0])
            self.assertIn("Canonical field checklist", llm_extractor.prompts[0])

    def test_azure_extraction_bad_retry_fails_with_clear_audit_details(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_email = root / "local" / "ingest" / "sample.msg"
            source_email.parent.mkdir(parents=True)
            source_email.write_bytes(b"not-real-msg")
            parsed_msg = ParsedMsg(
                subject="Invoice",
                sender_email="vendor@example.com",
                sender_name="Vendor",
                received_at=None,
                body_text="Invoice body",
                transport_headers=None,
                attachments=(),
                metadata={},
            )
            invalid_payload = _payload()
            invalid_payload["invoice"]["property_code"] = ["gw34"]
            retry_payload = _payload()
            retry_payload["confidence"]["invoice_fields"] = "0.95"
            llm_extractor = RetryingAzureOpenAIExtractor(invalid_payload, retry_payload)
            operational_repository = InMemoryOperationalRepository()
            processor = LocalProcessor(root, InMemoryPolicyRepository(), operational_repository, llm_extractor, document_intelligence_analyzer=FakeDocumentIntelligenceAnalyzer())

            with self.assertRaises(ExtractionValidationError):
                with patch("ap_automation.services.local_processor._parse_source_email", return_value=parsed_msg):
                    processor.process_email(source_email)

            validation_step = next(step for step in operational_repository.steps if step["step_type"] == "VALIDATION")
            self.assertEqual(validation_step["output_summary"]["validation_status"], "invalid")
            self.assertIn("initial_validation_errors", validation_step["output_summary"])
            self.assertIn("repair_validation_errors", validation_step["output_summary"])
            raw_output = operational_repository.extractions[0]["raw_output"]
            self.assertIn("initial_validation_errors", raw_output)
            self.assertIn("repair_validation_errors", raw_output)
            self.assertIn("invoice.property_code expected string or null, got list", raw_output["initial_validation_errors"][0])
            self.assertIn("confidence.invoice_fields expected number, got str", raw_output["repair_validation_errors"][0])
            self.assertIn("invoice.property_code expected string or null, got list", llm_extractor.prompts[0])
            self.assertIn("Type Contract Rules", llm_extractor.prompts[0])
            self.assertIn("invoice.property_code and invoice.property_name are string or null, never arrays", llm_extractor.prompts[0])

    def test_azure_extraction_normalizes_componentless_address_candidates_without_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_email = root / "local" / "ingest" / "sample.msg"
            source_email.parent.mkdir(parents=True)
            source_email.write_bytes(b"not-real-msg")
            parsed_msg = ParsedMsg(
                subject="Invoice",
                sender_email="vendor@example.com",
                sender_name="Vendor",
                received_at=None,
                body_text="Invoice body",
                transport_headers=None,
                attachments=(),
                metadata={},
            )
            payload = _payload()
            payload["property_lookup"]["address_candidates"] = [
                {
                    "rank": 1,
                    "label": "bill_to",
                    "street": "5201 alliance gateway freeway",
                    "city": "fort worth",
                    "state": "tx",
                    "zipcode": "76177",
                    "normalized_address": "5201 alliance gateway freeway fort worth tx 76177",
                    "source": "attachment:invoice.pdf:page",
                    "confidence": 0.95,
                    "evidence_text": "Bill To: 5201 Alliance Gateway Freeway, Fort Worth, TX 76177",
                },
                {
                    "rank": 2,
                    "label": "property",
                    "street": None,
                    "city": None,
                    "state": None,
                    "zipcode": None,
                    "normalized_address": None,
                    "source": "attachment:invoice.pdf:page",
                    "confidence": 0.7,
                    "evidence_text": "Project Paloma Villas",
                },
            ]
            llm_extractor = RetryingAzureOpenAIExtractor(payload, _payload())
            operational_repository = InMemoryOperationalRepository()
            processor = LocalProcessor(root, InMemoryPolicyRepository(), operational_repository, llm_extractor, document_intelligence_analyzer=FakeDocumentIntelligenceAnalyzer())

            with patch("ap_automation.services.local_processor._parse_source_email", return_value=parsed_msg):
                run_id = processor.process_email(source_email)

            self.assertEqual(operational_repository.runs[run_id]["status"], "completed")
            self.assertEqual(operational_repository.extractions[0]["raw_output"]["attempts"], [])
            saved_extraction = operational_repository.extractions[0]["extraction"]
            self.assertEqual(len(saved_extraction.property_lookup.address_candidates), 1)
            self.assertEqual(saved_extraction.property_lookup.address_candidates[0].street, "5201 alliance gateway freeway")
            validation_step = next(step for step in operational_repository.steps if step["step_type"] == "VALIDATION")
            self.assertEqual(validation_step["output_summary"]["validation_status"], "valid_after_normalization")
            normalization = validation_step["output_summary"]["normalization"]
            self.assertEqual(normalization["removed_address_candidate_count"], 1)
            self.assertEqual(
                normalization["removed_address_candidate_paths"],
                ["extraction.property_lookup.address_candidates[1]"],
            )
            self.assertEqual(
                operational_repository.extractions[0]["raw_output"]["normalization"]["removed_address_candidate_count"],
                1,
            )

    def test_asset_reference_does_not_allow_unmatched_invented_codes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_email = root / "local" / "ingest" / "sample.msg"
            source_email.parent.mkdir(parents=True)
            source_email.write_bytes(b"not-real-msg")
            parsed_msg = ParsedMsg(
                subject="Invoice for Unknown Building",
                sender_email="vendor@example.com",
                sender_name="Vendor",
                received_at=None,
                body_text="Invoice site: Unknown Building.",
                transport_headers=None,
                attachments=(),
                metadata={},
            )
            operational_repository = InMemoryOperationalRepository()
            policy_repository = AllianceGatewayPolicyRepository()
            llm_extractor = AssetAwareFakeAzureOpenAIExtractor()
            processor = LocalProcessor(root, policy_repository, operational_repository, llm_extractor, document_intelligence_analyzer=FakeDocumentIntelligenceAnalyzer())

            with patch("ap_automation.services.local_processor._parse_source_email", return_value=parsed_msg):
                run_id = processor.process_email(source_email)

            self.assertEqual(operational_repository.runs[run_id]["final_outcome"], "ESCALATE")
            saved_extraction = operational_repository.extractions[0]["extraction"]
            self.assertEqual(saved_extraction.property_lookup.property_name, ())
            self.assertEqual(saved_extraction.property_lookup.property_code, ())

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
            self.assertIn("class start,ingestion,attachments,selection,document_intelligence,extraction success;", trace)
            self.assertIn("class validation,error failure;", trace)
            self.assertEqual(operational_repository.extractions[0]["errors"][0], "extractor expected object, got NoneType")
            self.assertIn("llm_output", operational_repository.extractions[0]["raw_output"])
            finalize_step = operational_repository.steps[-1]
            self.assertEqual(finalize_step["step_type"], "FINALIZE")
            self.assertEqual(finalize_step["output_summary"]["status"], "failed")

    def test_document_intelligence_failure_marks_run_failed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_email = root / "local" / "ingest" / "sample.msg"
            source_email.parent.mkdir(parents=True)
            source_email.write_bytes(b"not-real-msg")

            operational_repository = InMemoryOperationalRepository()
            llm_extractor = FakeAzureOpenAIExtractor(_payload())
            processor = LocalProcessor(
                root,
                InMemoryPolicyRepository(),
                operational_repository,
                llm_extractor,
                document_intelligence_analyzer=FailingDocumentIntelligenceAnalyzer(),
            )
            parsed_msg = ParsedMsg(
                subject="Invoice",
                sender_email="vendor@example.com",
                sender_name="Vendor",
                received_at=None,
                body_text="Body",
                transport_headers=None,
                attachments=(ParsedAttachment("invoice.pdf", b"%PDF-1.4", "application/pdf", {}),),
                metadata={},
            )
            evals = [
                {"eligible": True, "status": "empty_text", "reason_code": "pdf_text_empty", "page_count": 1, "extraction_method": "pymupdf_text", "text_excerpt": None, "text_quality_score": 0.0, "evaluation_version": "pdf_eval.v2"},
            ]

            with patch("ap_automation.services.local_processor._parse_source_email", return_value=parsed_msg):
                with patch.object(processor._pdf_evaluator, "evaluate_attachments", return_value=evals):
                    with self.assertRaises(RuntimeError):
                        processor.process_email(source_email)

            self.assertEqual(operational_repository.runs["run-1"]["status"], "failed")
            di_step = next(step for step in operational_repository.steps if step["step_type"] == "DOCUMENT_INTELLIGENCE")
            self.assertEqual(di_step["error"], "missing DI config")
            finalize_step = operational_repository.steps[-1]
            self.assertEqual(finalize_step["step_type"], "FINALIZE")
            self.assertEqual(finalize_step["output_summary"]["status"], "failed")

    def test_processing_retries_once_after_transient_failure(self) -> None:
        class TransientUpsertFailureRepository(InMemoryOperationalRepository):
            def __init__(self) -> None:
                super().__init__()
                self.upsert_attempts = 0

            def upsert_email(self, metadata: dict[str, Any]) -> str:
                self.upsert_attempts += 1
                if self.upsert_attempts == 1:
                    raise RuntimeError("temporary postgres failure")
                return super().upsert_email(metadata)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_email = root / "local" / "ingest" / "sample.eml"
            fixture = root / "tests" / "fixtures" / "extractions" / "sample.json"
            source_email.parent.mkdir(parents=True)
            fixture.parent.mkdir(parents=True)
            source_email.write_text("sample email", encoding="utf-8")
            fixture.write_text(json.dumps(_payload()), encoding="utf-8")

            operational_repository = TransientUpsertFailureRepository()
            processor = LocalProcessor(root, InMemoryPolicyRepository(), operational_repository)

            with patch("ap_automation.services.local_processor.time.sleep") as sleep:
                run_id = processor.process_fixture(source_email, fixture)

            self.assertEqual(run_id, "run-1")
            self.assertEqual(operational_repository.upsert_attempts, 2)
            sleep.assert_called_once_with(0)
            self.assertEqual(operational_repository.runs[run_id]["status"], "completed")

    def test_processing_marks_second_post_run_failure_failed_when_repository_is_available(self) -> None:
        class FailingAuditStepRepository(InMemoryOperationalRepository):
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
                if step_type == "INGESTION":
                    return super().add_audit_step(run_id, step_type, input_summary, output_summary, reason, confidence, decision, error)
                raise RuntimeError("audit step failed")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_email = root / "local" / "ingest" / "sample.eml"
            fixture = root / "tests" / "fixtures" / "extractions" / "sample.json"
            source_email.parent.mkdir(parents=True)
            fixture.parent.mkdir(parents=True)
            source_email.write_text("sample email", encoding="utf-8")
            fixture.write_text(json.dumps(_payload()), encoding="utf-8")

            operational_repository = FailingAuditStepRepository()
            processor = LocalProcessor(root, InMemoryPolicyRepository(), operational_repository)

            with patch("ap_automation.services.local_processor.time.sleep") as sleep:
                with self.assertRaisesRegex(RuntimeError, "audit step failed"):
                    processor.process_fixture(source_email, fixture)

            sleep.assert_called_once_with(0)
            self.assertEqual(operational_repository.runs["run-1"]["status"], "failed")
            self.assertIn("audit step failed", operational_repository.runs["run-1"]["error"])

    def test_processing_does_not_mask_original_failure_when_failure_marking_cannot_persist(self) -> None:
        class FailingAuditStepAndFailRunRepository(InMemoryOperationalRepository):
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
                if step_type == "INGESTION":
                    return super().add_audit_step(run_id, step_type, input_summary, output_summary, reason, confidence, decision, error)
                raise RuntimeError("audit step failed")

            def fail_audit_run(self, run_id: str, error: str, trace_artifact_path: str | None = None) -> None:
                raise RuntimeError("postgres unavailable")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_email = root / "local" / "ingest" / "sample.eml"
            fixture = root / "tests" / "fixtures" / "extractions" / "sample.json"
            source_email.parent.mkdir(parents=True)
            fixture.parent.mkdir(parents=True)
            source_email.write_text("sample email", encoding="utf-8")
            fixture.write_text(json.dumps(_payload()), encoding="utf-8")

            operational_repository = FailingAuditStepAndFailRunRepository()
            processor = LocalProcessor(root, InMemoryPolicyRepository(), operational_repository)

            with patch("ap_automation.services.local_processor.time.sleep"):
                with self.assertRaisesRegex(RuntimeError, "audit step failed"):
                    processor.process_fixture(source_email, fixture)

            self.assertEqual(operational_repository.runs["run-1"]["status"], "started")

    def test_property_matching_does_not_bypass_missing_required_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_email = root / "local" / "ingest" / "sample.eml"
            fixture = root / "tests" / "fixtures" / "extractions" / "sample.json"
            source_email.parent.mkdir(parents=True)
            fixture.parent.mkdir(parents=True)
            source_email.write_text("sample email", encoding="utf-8")
            fixture.write_text(
                json.dumps(
                    _payload(
                        property_code=None,
                        property_name="Gateway 51",
                        bill_to=None,
                        business_unit_code=None,
                        vendor_name=None,
                    )
                ),
                encoding="utf-8",
            )

            operational_repository = InMemoryOperationalRepository()
            policy_repository = AliasAwareInMemoryPolicyRepository()
            processor = LocalProcessor(
                root,
                policy_repository,
                operational_repository,
            )

            run_id = processor.process_fixture(source_email, fixture)

            self.assertEqual(operational_repository.runs[run_id]["final_outcome"], "ESCALATE")
            decision = operational_repository.decisions["decision-1"]
            self.assertEqual(decision.matched_rule_code, "fallback_escalate")
            extraction_snapshot = json.loads((root / "local" / "audit" / "extractions" / f"{run_id}.json").read_text(encoding="utf-8"))
            self.assertEqual(extraction_snapshot["property_lookup"]["sql"], "select property candidates")
            self.assertEqual(extraction_snapshot["property_lookup"]["returned_payload"][0]["property_code"], "HW1")

    def test_property_matching_does_not_override_high_risk_rule(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_email = root / "local" / "ingest" / "sample.eml"
            fixture = root / "tests" / "fixtures" / "extractions" / "sample.json"
            source_email.parent.mkdir(parents=True)
            fixture.parent.mkdir(parents=True)
            source_email.write_text("sample email", encoding="utf-8")
            fixture.write_text(
                json.dumps(
                    _payload(
                        property_code=None,
                        property_name="Gateway 51",
                        bill_to=None,
                        business_unit_code=None,
                        flags=["multi_invoice_pdf"],
                        multi_invoice=True,
                    )
                ),
                encoding="utf-8",
            )

            operational_repository = InMemoryOperationalRepository()
            processor = LocalProcessor(
                root,
                AliasAwareInMemoryPolicyRepository(),
                operational_repository,
            )

            run_id = processor.process_fixture(source_email, fixture)

            self.assertEqual(operational_repository.runs[run_id]["final_outcome"], "ESCALATE")
            decision = operational_repository.decisions["decision-1"]
            self.assertEqual(decision.matched_rule_code, "hard_multi_invoice_pdf")

    def test_pdf_attachment_evaluation_persisted_and_audited(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            operational_repository = InMemoryOperationalRepository()
            processor = LocalProcessor(root, InMemoryPolicyRepository(), operational_repository)
            records = [
                {
                    "file_name": "invoice.pdf",
                    "content_type": "application/pdf",
                    "storage_path": "local/attachments/email-1/invoice.pdf",
                    "metadata": {},
                }
            ]
            evaluations = [
                {
                    "eligible": True,
                    "status": "success",
                    "reason_code": "text_extracted",
                    "page_count": 1,
                    "extraction_method": "pymupdf_text",
                    "text_excerpt": "Address 9800 HILLWOOD PWKY STE 300",
                    "text_quality_score": 0.88,
                    "evaluation_version": "pdf_eval.v2",
                }
            ]
            with patch.object(processor._pdf_evaluator, "evaluate_attachments", return_value=evaluations):
                processor._evaluate_attachment_records(records)
            processor._select_attachment_extractors(records)
            self.assertEqual(records[0].get("text_excerpt"), "Address 9800 HILLWOOD PWKY STE 300")
            self.assertEqual(records[0]["metadata"]["pdf_evaluation"]["status"], "success")
            self.assertEqual(records[0]["metadata"]["extractor_selection"]["selected_extractor"], "pymupdf")

    def test_word_attachment_evaluation_persisted_and_sent_to_extractor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_email = root / "local" / "ingest" / "sample.msg"
            source_email.parent.mkdir(parents=True)
            source_email.write_bytes(b"not-real-msg")
            parsed_msg = ParsedMsg(
                subject="6S CFA Fee",
                sender_email="mitchell.dunson@hillwood.com",
                sender_name="Mitchell",
                received_at=None,
                body_text="Attached check request and invoice backup.",
                transport_headers=None,
                attachments=(
                    ParsedAttachment(
                        "6S CFA Check Request.docx",
                        b"docx bytes",
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        {},
                    ),
                    ParsedAttachment("CFA Application fees.pdf", b"%PDF-1.4", "application/pdf", {}),
                ),
                metadata={},
            )
            word_evals = [
                {
                    "eligible": True,
                    "status": "success",
                    "reason_code": "word_text_extracted",
                    "text_excerpt": "Check Request Keller 305 Project amount 13713.70",
                    "text_quality_score": 1.0,
                    "evaluation_version": "word_eval.v1",
                },
                {
                    "eligible": False,
                    "status": "unsupported_file_type",
                    "reason_code": "attachment_not_word",
                    "text_excerpt": None,
                    "text_quality_score": 0.0,
                    "evaluation_version": "word_eval.v1",
                },
            ]
            pdf_evals = [
                {"eligible": False, "status": "not_pdf", "reason_code": "attachment_not_pdf", "page_count": 0, "extraction_method": "none", "text_excerpt": None, "text_quality_score": 0.0, "evaluation_version": "pdf_eval.v2"},
                {"eligible": True, "status": "success", "reason_code": "text_extracted", "page_count": 1, "extraction_method": "pymupdf_text", "text_excerpt": "Invoice backup text", "text_quality_score": 0.9, "evaluation_version": "pdf_eval.v2"},
            ]
            operational_repository = InMemoryOperationalRepository()
            llm_extractor = FakeAzureOpenAIExtractor(_payload(document_type="check_request", source_attachments=["6S CFA Check Request.docx"]))
            processor = LocalProcessor(root, InMemoryPolicyRepository(), operational_repository, llm_extractor, document_intelligence_analyzer=FakeDocumentIntelligenceAnalyzer())

            with patch("ap_automation.services.local_processor._parse_source_email", return_value=parsed_msg):
                with patch.object(processor._pdf_evaluator, "evaluate_attachments", return_value=pdf_evals):
                    with patch.object(processor._word_evaluator, "evaluate_attachments", return_value=word_evals):
                        run_id = processor.process_email(source_email)

            word_record = llm_extractor.attachment_records[0]
            self.assertEqual(word_record["file_name"], "6S CFA Check Request.docx")
            self.assertEqual(word_record["text_excerpt"], "Check Request Keller 305 Project amount 13713.70")
            self.assertEqual(word_record["metadata"]["extractor_selection"]["selected_extractor"], "word_text")
            attachment_step = next(step for step in operational_repository.steps if step["step_type"] == "ATTACHMENT_PROCESSING")
            self.assertEqual(attachment_step["output_summary"]["word_evaluation_summary"]["word_success"], 1)
            self.assertEqual(operational_repository.runs[run_id]["final_outcome"], "AUTO")
            final_decision = next(reversed(operational_repository.decisions.values()))
            self.assertEqual(final_decision.destination_code, "MEDIUS_PROPERTIES")
            self.assertEqual(final_decision.matched_rule_code, "check_request_medius_property")

    def test_extractor_receives_excerpt_only_on_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_email = root / "local" / "ingest" / "sample.msg"
            fixture = root / "tests" / "fixtures" / "extractions" / "sample.json"
            source_email.parent.mkdir(parents=True)
            fixture.parent.mkdir(parents=True)
            source_email.write_bytes(b"not-real-msg")
            fixture.write_text(json.dumps(_payload()), encoding="utf-8")

            operational_repository = InMemoryOperationalRepository()
            llm_extractor = FakeAzureOpenAIExtractor(_payload())
            processor = LocalProcessor(root, InMemoryPolicyRepository(), operational_repository, llm_extractor, document_intelligence_analyzer=FakeDocumentIntelligenceAnalyzer(success=False))
            parsed_msg = ParsedMsg(
                subject="Invoice",
                sender_email="vendor@example.com",
                sender_name="Vendor",
                received_at=None,
                body_text="Body",
                transport_headers=None,
                attachments=(
                    ParsedAttachment("ok.pdf", b"%PDF-1.4", "application/pdf", {}),
                    ParsedAttachment("bad.pdf", b"%PDF-1.4", "application/pdf", {}),
                ),
                metadata={},
            )
            evals = [
                {"eligible": True, "status": "success", "reason_code": "text_extracted", "page_count": 1, "extraction_method": "pymupdf_text", "text_excerpt": "usable", "text_quality_score": 0.9, "evaluation_version": "pdf_eval.v2"},
                {"eligible": True, "status": "empty_text", "reason_code": "pdf_text_empty", "page_count": 1, "extraction_method": "pymupdf_text", "text_excerpt": None, "text_quality_score": 0.0, "evaluation_version": "pdf_eval.v2"},
            ]
            with patch("ap_automation.services.local_processor._parse_source_email", return_value=parsed_msg):
                with patch.object(processor._pdf_evaluator, "evaluate_attachments", return_value=evals):
                    processor.process_email(source_email)
            self.assertEqual(llm_extractor.attachment_records[0].get("text_excerpt"), "usable")
            self.assertIsNone(llm_extractor.attachment_records[1].get("text_excerpt"))

    def test_inline_attachments_are_persisted_but_not_sent_to_extractor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_email = root / "local" / "ingest" / "sample.msg"
            source_email.parent.mkdir(parents=True)
            source_email.write_bytes(b"not-real-msg")

            operational_repository = InMemoryOperationalRepository()
            llm_extractor = FakeAzureOpenAIExtractor(_payload(source_attachments=["invoice.pdf"]))
            processor = LocalProcessor(root, InMemoryPolicyRepository(), operational_repository, llm_extractor, document_intelligence_analyzer=FakeDocumentIntelligenceAnalyzer())
            parsed_msg = ParsedMsg(
                subject="Invoice",
                sender_email="vendor@example.com",
                sender_name="Vendor",
                received_at=None,
                body_text="Body <img src=\"cid:logo\">",
                transport_headers=None,
                attachments=(
                    ParsedAttachment("logo.png", b"png", "image/png", {"content_id": "logo", "is_inline": True}),
                    ParsedAttachment("invoice.pdf", b"%PDF-1.4", "application/pdf", {}),
                ),
                metadata={},
            )
            evals = [
                {"eligible": False, "status": "not_pdf", "reason_code": "attachment_not_pdf", "page_count": 0, "extraction_method": "none", "text_excerpt": None, "text_quality_score": 0.0, "evaluation_version": "pdf_eval.v2"},
                {"eligible": True, "status": "success", "reason_code": "text_extracted", "page_count": 1, "extraction_method": "pymupdf_text", "text_excerpt": "invoice text", "text_quality_score": 0.9, "evaluation_version": "pdf_eval.v2"},
            ]

            with patch("ap_automation.services.local_processor._parse_source_email", return_value=parsed_msg):
                with patch.object(processor._pdf_evaluator, "evaluate_attachments", return_value=evals):
                    processor.process_email(source_email)

            self.assertEqual([record["file_name"] for record in operational_repository.attachments], ["logo.png", "invoice.pdf"])
            self.assertEqual([record["file_name"] for record in llm_extractor.attachment_records], ["invoice.pdf"])
            attachment_step = next(step for step in operational_repository.steps if step["step_type"] == "ATTACHMENT_PROCESSING")
            self.assertEqual(attachment_step["output_summary"]["attachments_extracted"], 2)
            self.assertEqual(attachment_step["output_summary"]["business_attachments_extracted"], 1)

    def test_pymupdf_corrupt_required_pdf_does_not_escalate_when_di_layout_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_email = root / "local" / "ingest" / "sample.msg"
            fixture = root / "tests" / "fixtures" / "extractions" / "sample.json"
            source_email.parent.mkdir(parents=True)
            fixture.parent.mkdir(parents=True)
            source_email.write_bytes(b"not-real-msg")
            fixture.write_text(json.dumps(_payload(document_type="invoice", has_invoice_attachment=True)), encoding="utf-8")

            operational_repository = InMemoryOperationalRepository()
            processor = LocalProcessor(root, InMemoryPolicyRepository(), operational_repository, document_intelligence_analyzer=FakeDocumentIntelligenceAnalyzer())
            parsed_msg = ParsedMsg(
                subject="Invoice",
                sender_email="vendor@example.com",
                sender_name="Vendor",
                received_at=None,
                body_text="Body",
                transport_headers=None,
                attachments=(ParsedAttachment("invoice.pdf", b"%PDF-1.4", "application/pdf", {}),),
                metadata={},
            )
            evals = [
                {"eligible": True, "status": "corrupt_pdf", "reason_code": "pdf_parse_error:PdfReadError", "page_count": 0, "extraction_method": "pymupdf_text", "text_excerpt": None, "text_quality_score": 0.0, "evaluation_version": "pdf_eval.v2"},
            ]
            with patch("ap_automation.services.local_processor._parse_source_email", return_value=parsed_msg):
                with patch.object(processor._pdf_evaluator, "evaluate_attachments", return_value=evals):
                    run_id = processor.process_fixture(source_email, fixture)
            self.assertEqual(operational_repository.runs[run_id]["final_outcome"], "AUTO")
            self.assertEqual(operational_repository.decisions["decision-1"].matched_rule_code, "property_routing_match")

    def test_di_empty_layout_required_pdf_leads_to_ESCALATE(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_email = root / "local" / "ingest" / "sample.msg"
            fixture = root / "tests" / "fixtures" / "extractions" / "sample.json"
            source_email.parent.mkdir(parents=True)
            fixture.parent.mkdir(parents=True)
            source_email.write_bytes(b"not-real-msg")
            fixture.write_text(json.dumps(_payload(document_type="invoice", has_invoice_attachment=True)), encoding="utf-8")

            operational_repository = InMemoryOperationalRepository()
            processor = LocalProcessor(root, InMemoryPolicyRepository(), operational_repository, document_intelligence_analyzer=FakeDocumentIntelligenceAnalyzer(text_excerpt="   "))
            parsed_msg = ParsedMsg(
                subject="Invoice",
                sender_email="vendor@example.com",
                sender_name="Vendor",
                received_at=None,
                body_text="Body",
                transport_headers=None,
                attachments=(ParsedAttachment("invoice.pdf", b"%PDF-1.4", "application/pdf", {}),),
                metadata={},
            )
            evals = [
                {"eligible": True, "status": "empty_text", "reason_code": "pdf_text_empty", "page_count": 1, "extraction_method": "pymupdf_text", "text_excerpt": None, "text_quality_score": 0.0, "evaluation_version": "pdf_eval.v2"},
            ]
            with patch("ap_automation.services.local_processor._parse_source_email", return_value=parsed_msg):
                with patch.object(processor._pdf_evaluator, "evaluate_attachments", return_value=evals):
                    run_id = processor.process_fixture(source_email, fixture)
            self.assertEqual(operational_repository.runs[run_id]["final_outcome"], "ESCALATE")
            self.assertEqual(operational_repository.decisions["decision-1"].matched_rule_code, "hard_pdf_required_unreadable")

    def test_di_invoice_model_error_does_not_make_layout_readable_pdf_unreadable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_email = root / "local" / "ingest" / "sample.msg"
            source_email.parent.mkdir(parents=True)
            source_email.write_bytes(b"not-real-msg")

            operational_repository = InMemoryOperationalRepository()
            llm_extractor = FakeAzureOpenAIExtractor(_payload(document_type="invoice", has_invoice_attachment=True))
            processor = LocalProcessor(
                root,
                InMemoryPolicyRepository(),
                operational_repository,
                llm_extractor,
                document_intelligence_analyzer=FakeDocumentIntelligenceAnalyzer(errors=["prebuilt-invoice:RuntimeError: service unavailable"]),
            )
            parsed_msg = ParsedMsg(
                subject="Invoice",
                sender_email="vendor@example.com",
                sender_name="Vendor",
                received_at=None,
                body_text="Body",
                transport_headers=None,
                attachments=(ParsedAttachment("invoice.pdf", b"%PDF-1.4", "application/pdf", {}),),
                metadata={},
            )
            evals = [
                {"eligible": True, "status": "empty_text", "reason_code": "pdf_text_empty", "page_count": 1, "extraction_method": "pymupdf_text", "text_excerpt": None, "text_quality_score": 0.0, "evaluation_version": "pdf_eval.v2"},
            ]
            with patch("ap_automation.services.local_processor._parse_source_email", return_value=parsed_msg):
                with patch.object(processor._pdf_evaluator, "evaluate_attachments", return_value=evals):
                    run_id = processor.process_email(source_email)

            self.assertEqual(operational_repository.runs[run_id]["final_outcome"], "AUTO")
            self.assertEqual(llm_extractor.attachment_records[0].get("text_excerpt"), "di invoice text")
            self.assertEqual(llm_extractor.attachment_records[0]["metadata"]["document_intelligence"]["errors"], ["prebuilt-invoice:RuntimeError: service unavailable"])

    def test_di_unsupported_required_attachment_leads_to_ESCALATE(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_email = root / "local" / "ingest" / "sample.msg"
            fixture = root / "tests" / "fixtures" / "extractions" / "sample.json"
            source_email.parent.mkdir(parents=True)
            fixture.parent.mkdir(parents=True)
            source_email.write_bytes(b"not-real-msg")
            fixture.write_text(json.dumps(_payload(document_type="invoice", has_invoice_attachment=True, source_attachments=["invoice.zip"])), encoding="utf-8")

            operational_repository = InMemoryOperationalRepository()
            processor = LocalProcessor(root, InMemoryPolicyRepository(), operational_repository, document_intelligence_analyzer=FakeDocumentIntelligenceAnalyzer(success=False))
            parsed_msg = ParsedMsg(
                subject="Invoice",
                sender_email="vendor@example.com",
                sender_name="Vendor",
                received_at=None,
                body_text="Body",
                transport_headers=None,
                attachments=(ParsedAttachment("invoice.zip", b"zip", "application/zip", {}),),
                metadata={},
            )
            evals = [
                {"eligible": False, "status": "not_pdf", "reason_code": "attachment_not_pdf", "page_count": 0, "extraction_method": "none", "text_excerpt": None, "text_quality_score": 0.0, "evaluation_version": "pdf_eval.v2"},
            ]
            with patch("ap_automation.services.local_processor._parse_source_email", return_value=parsed_msg):
                with patch.object(processor._pdf_evaluator, "evaluate_attachments", return_value=evals):
                    run_id = processor.process_fixture(source_email, fixture)
            self.assertEqual(operational_repository.runs[run_id]["final_outcome"], "ESCALATE")
            self.assertEqual(operational_repository.decisions["decision-1"].matched_rule_code, "hard_pdf_required_unreadable")

    def test_llm_omitted_irrelevant_jpeg_is_audited_and_does_not_block_valid_invoice_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_email = root / "local" / "ingest" / "sample.msg"
            fixture = root / "tests" / "fixtures" / "extractions" / "sample.json"
            source_email.parent.mkdir(parents=True)
            fixture.parent.mkdir(parents=True)
            source_email.write_bytes(b"not-real-msg")
            fixture.write_text(json.dumps(_payload(document_type="invoice", has_invoice_attachment=True, source_attachments=["invoice.pdf"])), encoding="utf-8")

            operational_repository = InMemoryOperationalRepository()
            processor = LocalProcessor(root, InMemoryPolicyRepository(), operational_repository, document_intelligence_analyzer=FakeDocumentIntelligenceAnalyzer(success=True))
            parsed_msg = ParsedMsg(
                subject="Invoice",
                sender_email="vendor@example.com",
                sender_name="Vendor",
                received_at=None,
                body_text="Body",
                transport_headers=None,
                attachments=(
                    ParsedAttachment("invoice.pdf", b"%PDF-1.4", "application/pdf", {}),
                    ParsedAttachment("sign-notice.jpg", b"jpeg", "image/jpeg", {}),
                ),
                metadata={},
            )
            evals = [
                {"eligible": True, "status": "success", "reason_code": "text_extracted", "page_count": 1, "extraction_method": "pymupdf_text", "text_excerpt": "Invoice HW1 100.00", "text_quality_score": 0.9, "evaluation_version": "pdf_eval.v2"},
                {"eligible": False, "status": "not_pdf", "reason_code": "attachment_not_pdf", "page_count": 0, "extraction_method": "none", "text_excerpt": None, "text_quality_score": 0.0, "evaluation_version": "pdf_eval.v2"},
            ]
            with patch("ap_automation.services.local_processor._parse_source_email", return_value=parsed_msg):
                with patch.object(processor._pdf_evaluator, "evaluate_attachments", return_value=evals):
                    run_id = processor.process_fixture(source_email, fixture)

            self.assertEqual(operational_repository.runs[run_id]["final_outcome"], "AUTO")
            self.assertEqual(operational_repository.decisions["decision-1"].matched_rule_code, "property_routing_match")
            self.assertEqual(len(operational_repository.attachments), 2)
            selection_step = next(step for step in operational_repository.steps if step["step_type"] == "DOCUMENT_EXTRACTION_SELECTION")
            jpeg_selection = next(item for item in selection_step["output_summary"]["attachments"] if item["file_name"] == "sign-notice.jpg")
            self.assertEqual(jpeg_selection["extractor_selection"]["selected_extractor"], "document_intelligence")
            validation_step = next(step for step in operational_repository.steps if step["step_type"] == "VALIDATION")
            self.assertEqual(
                validation_step["output_summary"]["not_selected_attachments"],
                [
                    {
                        "file_name": "sign-notice.jpg",
                        "reason": "not_returned_as_document_item_by_extractor",
                        "extractor_selection": jpeg_selection["extractor_selection"],
                        "document_intelligence_status": "success",
                    }
                ],
            )

    def test_irrelevant_unsupported_attachment_does_not_block_valid_invoice_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_email = root / "local" / "ingest" / "sample.msg"
            fixture = root / "tests" / "fixtures" / "extractions" / "sample.json"
            source_email.parent.mkdir(parents=True)
            fixture.parent.mkdir(parents=True)
            source_email.write_bytes(b"not-real-msg")
            fixture.write_text(json.dumps(_payload(document_type="invoice", has_invoice_attachment=True, source_attachments=["invoice.pdf"])), encoding="utf-8")

            operational_repository = InMemoryOperationalRepository()
            processor = LocalProcessor(root, InMemoryPolicyRepository(), operational_repository, document_intelligence_analyzer=FakeDocumentIntelligenceAnalyzer(success=False))
            parsed_msg = ParsedMsg(
                subject="Invoice",
                sender_email="vendor@example.com",
                sender_name="Vendor",
                received_at=None,
                body_text="Body",
                transport_headers=None,
                attachments=(
                    ParsedAttachment("invoice.pdf", b"%PDF-1.4", "application/pdf", {}),
                    ParsedAttachment("backup.xlsx", b"xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", {}),
                ),
                metadata={},
            )
            evals = [
                {"eligible": True, "status": "success", "reason_code": "text_extracted", "page_count": 1, "extraction_method": "pymupdf_text", "text_excerpt": "Invoice HW1 100.00", "text_quality_score": 0.9, "evaluation_version": "pdf_eval.v2"},
                {"eligible": False, "status": "not_pdf", "reason_code": "attachment_not_pdf", "page_count": 0, "extraction_method": "none", "text_excerpt": None, "text_quality_score": 0.0, "evaluation_version": "pdf_eval.v2"},
            ]
            with patch("ap_automation.services.local_processor._parse_source_email", return_value=parsed_msg):
                with patch.object(processor._pdf_evaluator, "evaluate_attachments", return_value=evals):
                    run_id = processor.process_fixture(source_email, fixture)

            self.assertEqual(operational_repository.runs[run_id]["final_outcome"], "AUTO")
            self.assertEqual(operational_repository.decisions["decision-1"].matched_rule_code, "property_routing_match")

    def test_deterministic_payment_link_override_sets_link_only_fact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_email = root / "local" / "ingest" / "sample.eml"
            fixture = root / "tests" / "fixtures" / "extractions" / "sample.json"
            source_email.parent.mkdir(parents=True)
            fixture.parent.mkdir(parents=True)
            source_email.write_text("sample email", encoding="utf-8")
            payload = _payload(document_type="account_summary", link_only=False, has_invoice_attachment=False)
            payload["observed_facts"]["mentions_payment_link_only"] = False
            fixture.write_text(json.dumps(payload), encoding="utf-8")
            operational_repository = InMemoryOperationalRepository()
            teams_notifier = FakeTeamsNotifier()
            processor = LocalProcessor(root, InMemoryPolicyRepository(), operational_repository, teams_notifier=teams_notifier)

            parsed_msg = ParsedMsg(
                subject="REMINDER - Your bill is due",
                sender_email="vendor@example.com",
                sender_name="Vendor",
                received_at=None,
                body_text="REMINDER your bill is due. Log In https://example.com/pay",
                transport_headers=None,
                attachments=(),
                metadata={},
            )
            with patch("ap_automation.services.local_processor._parse_source_email", return_value=parsed_msg):
                run_id = processor.process_fixture(source_email, fixture)

            self.assertEqual(operational_repository.runs[run_id]["final_outcome"], "ESCALATE")
            decision = operational_repository.decisions["decision-1"]
            self.assertEqual(decision.matched_rule_code, "hard_link_only_invoice")
            self.assertEqual(len(teams_notifier.notifications), 1)

    def test_forwarded_link_only_invoice_with_signature_only_body_escalates_when_not_social_reply(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_email = root / "local" / "ingest" / "sample.eml"
            fixture = root / "tests" / "fixtures" / "extractions" / "sample.json"
            source_email.parent.mkdir(parents=True)
            fixture.parent.mkdir(parents=True)
            source_email.write_text("sample email", encoding="utf-8")
            payload = _payload(
                document_type="invoice",
                sender_email="Christine.Long@hillwood.com",
                vendor_name="Frontier Waste Solutions",
                has_invoice_attachment=False,
                link_only=True,
                amount=0,
                flags=["link_only_invoice"],
                evidence_summary="Forwarded invoice link notice in quoted history; latest body is signature-only, not social/no-action language.",
            )
            payload["invoice"]["invoice_number"] = "9169178"
            payload["observed_facts"]["mentions_payment_link_only"] = True
            payload["observed_facts"]["latest_reply_indicates_no_ap_action"] = False
            fixture.write_text(json.dumps(payload), encoding="utf-8")
            operational_repository = InMemoryOperationalRepository()
            teams_notifier = FakeTeamsNotifier()
            processor = LocalProcessor(root, InMemoryPolicyRepository(), operational_repository, teams_notifier=teams_notifier)

            latest_body = (
                "Christine Long, CPM\n"
                "Senior Property Manager\n"
                "Hillwood, A Perot Company\n"
                "9800 Hillwood Parkway, Suite 300, Fort Worth, TX 76177"
            )
            parsed_msg = ParsedMsg(
                subject="Fw: Frontier Waste Solutions Invoice #9169178 Link",
                sender_email="Christine.Long@hillwood.com",
                sender_name="Long, Christine",
                received_at=None,
                body_text=(
                    f"{latest_body}\n\n"
                    "From: jus_custsvc@frontierwaste.com\n"
                    "Subject: Frontier Waste Solutions Invoice #9169178 Link\n"
                    "Please click here https://example.com/payinvoice to view your Frontier Waste Solutions invoice."
                ),
                transport_headers=None,
                attachments=(),
                metadata={
                    "thread_context": {
                        "latest_body_text": latest_body,
                        "quoted_history_text": (
                            "From: jus_custsvc@frontierwaste.com\n"
                            "Subject: Frontier Waste Solutions Invoice #9169178 Link\n"
                            "Please click here https://example.com/payinvoice to view your Frontier Waste Solutions invoice."
                        ),
                        "has_quoted_history": True,
                    }
                },
            )
            with patch("ap_automation.services.local_processor._parse_source_email", return_value=parsed_msg):
                processor.process_fixture(source_email, fixture)

            decision = operational_repository.decisions["decision-1"]
            self.assertEqual(decision.outcome, "ESCALATE")
            self.assertEqual(decision.destination_code, "ESCALATE_LINK_ONLY")
            self.assertEqual(decision.matched_rule_code, "hard_link_only_invoice")
            self.assertEqual(len(teams_notifier.notifications), 1)

    def test_utility_bill_available_link_override_sets_link_only_fact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_email = root / "local" / "ingest" / "sample.eml"
            fixture = root / "tests" / "fixtures" / "extractions" / "sample.json"
            source_email.parent.mkdir(parents=True)
            fixture.parent.mkdir(parents=True)
            source_email.write_text("sample email", encoding="utf-8")
            payload = _payload(document_type="account_summary", link_only=False, has_invoice_attachment=False)
            payload["observed_facts"]["mentions_payment_link_only"] = False
            fixture.write_text(json.dumps(payload), encoding="utf-8")
            operational_repository = InMemoryOperationalRepository()
            teams_notifier = FakeTeamsNotifier()
            processor = LocalProcessor(root, InMemoryPolicyRepository(), operational_repository, teams_notifier=teams_notifier)

            parsed_msg = ParsedMsg(
                subject="Your Electric Service Bill is Available",
                sender_email="billing@example.com",
                sender_name="Billing",
                received_at=None,
                body_text=(
                    "Your Electric Service Bill is Available\n"
                    "Your bill is available.\n"
                    "Account: 800000\n"
                    "Service Location: 3101 Example Road\n"
                    "Amount: $193.98\n"
                    "Due Date: Jun 8, 2026\n"
                    "Click here https://example.com/pay"
                ),
                transport_headers=None,
                attachments=(),
                metadata={},
            )
            with patch("ap_automation.services.local_processor._parse_source_email", return_value=parsed_msg):
                run_id = processor.process_fixture(source_email, fixture)

            self.assertEqual(operational_repository.runs[run_id]["final_outcome"], "ESCALATE")
            decision = operational_repository.decisions["decision-1"]
            self.assertEqual(decision.matched_rule_code, "hard_link_only_invoice")
            self.assertEqual(len(teams_notifier.notifications), 1)

    def test_duplicate_payment_confirmation_override_routes_to_vendor_question(self) -> None:
        run_id, operational_repository = self._process_fixture_with_parsed_text(
            subject="Duplicate payments received",
            body_text=(
                "We received duplicate payments for invoices INV-100 and INV-101. "
                "Can you please Confirm?"
            ),
        )

        self.assertEqual(operational_repository.runs[run_id]["final_outcome"], "ESCALATE")
        decision = operational_repository.decisions["decision-1"]
        self.assertEqual(decision.matched_rule_code, "hard_vendor_inquiry")
        self.assertEqual(decision.destination_code, "ESCALATE_VENDOR_QUESTION")
        self.assertTrue(
            operational_repository.extractions[0]["parsed_payload"]["observed_facts"][
                "indicates_vendor_question_or_payment_inquiry"
            ]
        )

    def test_missing_remittance_override_routes_to_vendor_question(self) -> None:
        run_id, operational_repository = self._process_fixture_with_parsed_text(
            subject="ACH payment question",
            body_text="ACH payment 8831 arrived without remittance. Which invoice did this ACH pay?",
        )

        decision = operational_repository.decisions["decision-1"]
        self.assertEqual(operational_repository.runs[run_id]["final_outcome"], "ESCALATE")
        self.assertEqual(decision.matched_rule_code, "hard_vendor_inquiry")
        self.assertEqual(decision.destination_code, "ESCALATE_VENDOR_QUESTION")

    def test_reconciliation_credit_dispute_override_routes_to_vendor_question(self) -> None:
        run_id, operational_repository = self._process_fixture_with_parsed_text(
            subject="Account reconciliation needed",
            body_text=(
                "Please advise on account reconciliation for open invoices. "
                "There is a credit and disputed invoice with missing backup support."
            ),
        )

        decision = operational_repository.decisions["decision-1"]
        self.assertEqual(operational_repository.runs[run_id]["final_outcome"], "ESCALATE")
        self.assertEqual(decision.matched_rule_code, "hard_vendor_inquiry")
        self.assertEqual(decision.destination_code, "ESCALATE_VENDOR_QUESTION")

    def test_payment_inquiry_with_link_text_does_not_become_link_only_invoice(self) -> None:
        run_id, operational_repository = self._process_fixture_with_parsed_text(
            subject="Payment remittance question",
            body_text=(
                "Can you please confirm which account this payment applies to? "
                "We need remittance details for payment 8831. "
                "Portal: https://example.com/remittance"
            ),
            payload_overrides={
                "document_type": "payment_inquiry",
                "has_invoice_attachment": False,
                "link_only": False,
                "source_attachments": [],
            },
            observed_overrides={
                "indicates_vendor_question_or_payment_inquiry": True,
            },
        )

        self.assertEqual(operational_repository.runs[run_id]["final_outcome"], "ESCALATE")
        decision = operational_repository.decisions["decision-1"]
        self.assertEqual(decision.matched_rule_code, "hard_vendor_inquiry")
        self.assertEqual(decision.destination_code, "ESCALATE_VENDOR_QUESTION")
        extraction = operational_repository.extractions[0]["extraction"]
        self.assertIsNotNone(extraction)
        flags = extraction.document.document_flags
        self.assertIn("vendor_inquiry", flags)
        self.assertNotIn("link_only_invoice", flags)

    def test_body_embedded_quickbooks_invoice_with_print_link_does_not_become_link_only_invoice(self) -> None:
        run_id, operational_repository = self._process_fixture_with_parsed_text(
            subject="Invoice 1462 from 2CL Specialties Construction, LLC",
            body_text=(
                "2CL Specialties Construction, LLC\n"
                "Invoice 1462\n"
                "Balance due $2,966.05\n"
                "Due date 05/31/2026\n"
                "Bill to: Hillwood Construction Services, LP\n"
                "Service address: 13901 Aviator Way, Gridiron Suite 210\n"
                "Work description: drywall repair and paint touch-up.\n"
                "Print or save this invoice: https://quickbooks.example.com/print/1462\n"
                "Pay invoice: https://quickbooks.example.com/pay/1462"
            ),
            payload_overrides={
                "invoice_number": "1462",
                "vendor_name": "2CL Specialties Construction, LLC",
                "amount": 2966.05,
                "property_code": "HW1",
                "bill_to": "Hillwood Construction Services, LP",
                "service_address": "13901 Aviator Way, Gridiron Suite 210",
                "has_invoice_attachment": False,
                "link_only": False,
                "source_attachments": [],
            },
            observed_overrides={
                "mentions_payment_link_only": True,
            },
        )

        self.assertEqual(operational_repository.runs[run_id]["final_outcome"], "AUTO")
        decision = operational_repository.decisions["decision-1"]
        self.assertNotEqual(decision.matched_rule_code, "hard_link_only_invoice")
        extraction = operational_repository.extractions[0]["extraction"]
        self.assertIsNotNone(extraction)
        self.assertNotIn("link_only_invoice", extraction.document.document_flags)
        self.assertTrue(
            operational_repository.extractions[0]["parsed_payload"]["observed_facts"]["mentions_payment_link_only"]
        )

    def test_explicit_link_only_invoice_with_body_facts_persists_link_only_escalation(self) -> None:
        run_id, operational_repository = self._process_fixture_with_parsed_text(
            subject="Republic Services invoice is available",
            body_text=(
                "Republic Services\n"
                "Invoice 17222aa6\n"
                "Amount due $517.42\n"
                "Bill to: Hillwood Properties\n"
                "View and pay invoice: https://example.com/pay/17222aa6"
            ),
            payload_overrides={
                "invoice_number": "17222aa6",
                "vendor_name": "Republic Services",
                "amount": 517.42,
                "property_code": None,
                "property_name": None,
                "service_address": None,
                "bill_to": "Hillwood Properties",
                "business_unit_code": None,
                "has_invoice_attachment": False,
                "link_only": True,
                "source_attachments": [],
                "property_lookup": {
                    "property_code": [],
                    "property_name": [],
                    "tenant": [],
                    "address": [],
                    "suite": [],
                    "city": [],
                    "state": [],
                    "zipcode": [],
                    "address_candidates": [],
                },
            },
            observed_overrides={
                "mentions_payment_link_only": True,
            },
        )

        self.assertEqual(operational_repository.runs[run_id]["final_outcome"], "ESCALATE")
        decision = operational_repository.decisions["decision-1"]
        self.assertEqual(decision.matched_rule_code, "hard_link_only_invoice")
        self.assertEqual(decision.destination_code, "ESCALATE_LINK_ONLY")
        extraction = operational_repository.extractions[0]["extraction"]
        self.assertIsNotNone(extraction)
        self.assertIn("link_only_invoice", extraction.document.document_flags)

    def test_portal_bill_with_body_facts_routes_to_link_only_escalation(self) -> None:
        run_id, operational_repository = self._process_fixture_with_parsed_text(
            subject="Your Account Center bill is available",
            body_text=(
                "Your current bill is available in Account Center.\n"
                "Amount due: $193.98\n"
                "Service address: 3101 Example Road\n"
                "Log in to view bill: https://example.com/account-center"
            ),
            payload_overrides={
                "invoice_number": None,
                "vendor_name": "Utility Account Center",
                "amount": 193.98,
                "property_code": "HW1",
                "property_name": "Hillwood One",
                "service_address": "3101 Example Road",
                "has_invoice_attachment": False,
                "link_only": True,
                "source_attachments": [],
            },
            observed_overrides={
                "mentions_payment_link_only": True,
            },
        )

        self.assertEqual(operational_repository.runs[run_id]["final_outcome"], "ESCALATE")
        decision = operational_repository.decisions["decision-1"]
        self.assertEqual(decision.matched_rule_code, "hard_link_only_invoice")
        self.assertEqual(decision.destination_code, "ESCALATE_LINK_ONLY")
        extraction = operational_repository.extractions[0]["extraction"]
        self.assertIsNotNone(extraction)
        self.assertIn("link_only_invoice", extraction.document.document_flags)

    def test_link_only_invoice_without_property_or_business_unit_persists_link_only_escalation(self) -> None:
        run_id, operational_repository = self._process_fixture_with_parsed_text(
            subject="Your bill is available",
            body_text="Your bill is due. Log in to view invoice: https://example.com/pay",
            payload_overrides={
                "invoice_number": "INV-100",
                "vendor_name": "Portal Vendor",
                "amount": 1000,
                "property_code": None,
                "property_name": None,
                "service_address": None,
                "bill_to": None,
                "business_unit_code": None,
                "has_invoice_attachment": False,
                "link_only": True,
                "source_attachments": [],
                "property_lookup": {
                    "property_code": [],
                    "property_name": [],
                    "tenant": [],
                    "address": [],
                    "suite": [],
                    "city": [],
                    "state": [],
                    "zipcode": [],
                    "address_candidates": [],
                },
            },
            observed_overrides={
                "mentions_payment_link_only": True,
                "mentions_missing_invoice_attachment": True,
            },
        )

        self.assertEqual(operational_repository.runs[run_id]["final_outcome"], "ESCALATE")
        decision = operational_repository.decisions["decision-1"]
        self.assertEqual(decision.matched_rule_code, "hard_link_only_invoice")
        self.assertEqual(decision.destination_code, "ESCALATE_LINK_ONLY")

    def test_routine_attached_invoice_collection_text_does_not_route_to_vendor_question(self) -> None:
        run_id, operational_repository = self._process_fixture_with_parsed_text(
            subject="Invoice INV-100",
            body_text="Attached invoice is due. Please review for payment. When can we expect payment?",
            payload_overrides={
                "has_invoice_attachment": True,
                "source_attachments": ["invoice.pdf"],
                "amount": 1000,
                "property_code": "HW1",
                "bill_to": None,
            },
        )

        decision = operational_repository.decisions["decision-1"]
        self.assertEqual(operational_repository.runs[run_id]["final_outcome"], "AUTO")
        self.assertEqual(decision.matched_rule_code, "property_routing_match")

    def test_account_summary_with_invoice_facts_files_as_statement(self) -> None:
        run_id, operational_repository = self._process_fixture_with_parsed_text(
            subject="Invoice from Southwest Nursery",
            body_text=(
                "Receipt.pdf\n"
                "Invoice #: 1599669\n"
                "Invoice Date: 05/01/2026\n"
                "Terms: NET 30\n"
                "Line items: plant material, delivery charge\n"
                "Tax: $82.15\n"
                "Total: $1,204.44"
            ),
            payload_overrides={
                "document_type": "account_summary",
                "invoice_number": "1599669",
                "vendor_name": "Southwest Nursery",
                "amount": 1204.44,
                "property_code": None,
                "bill_to": "Unmapped Building",
                "source_attachments": ["Receipt.pdf"],
                "has_invoice_attachment": True,
                "evidence_summary": "Receipt.pdf shows Invoice #: 1599669, Invoice Date, NET 30, line items, tax, total, and balance due.",
            },
        )

        decision = operational_repository.decisions["decision-1"]
        self.assertEqual(operational_repository.runs[run_id]["final_outcome"], "FILE")
        self.assertEqual(decision.matched_rule_code, "statement_file")
        self.assertEqual(decision.destination_code, "FOLDER_STATEMENTS")
        extraction = operational_repository.extractions[0]["extraction"]
        self.assertIsNotNone(extraction)
        self.assertEqual(extraction.document.document_type, "account_summary")

    def test_lone_star_style_statement_routes_to_statement_folder_not_past_due(self) -> None:
        run_id, operational_repository = self._process_fixture_with_parsed_text(
            subject="Lone Star statement",
            body_text=(
                "Statement\n"
                "Invoice 3576 Due Date 05/10/2026 Balance Due $731.77\n"
                "Aging Summary: Current $731.77, 1-30 Days Past Due $6,530.41"
            ),
            payload_overrides={
                "document_type": "statement",
                "invoice_number": "3576",
                "due_date": "2026-05-10",
                "amount": 731.77,
                "vendor_name": "Lone Star",
                "property_code": "HW1",
                "bill_to": "Hillwood Properties",
                "source_attachments": ["Lone Star Statement.pdf"],
                "has_invoice_attachment": True,
                "received_at": "2026-05-20T09:15:00-05:00",
                "flags": ["statement_or_account_summary"],
                "evidence_summary": (
                    "Statement with invoice number, due date, balance due, and separate aging summary. "
                    "Current amount is in the Current bucket."
                ),
            },
            observed_overrides={
                "contains_aging_summary": True,
                "account_has_past_due_aging_balance": True,
                "current_invoice_is_past_due": True,
            },
        )

        decision = operational_repository.decisions["decision-1"]
        self.assertEqual(operational_repository.runs[run_id]["final_outcome"], "FILE")
        self.assertEqual(decision.matched_rule_code, "statement_file")
        self.assertEqual(decision.destination_code, "FOLDER_STATEMENTS")
        self.assertNotEqual(decision.destination_code, "ESCALATE_PAST_DUE")
        extraction = operational_repository.extractions[0]["extraction"]
        self.assertIsNotNone(extraction)
        self.assertEqual(extraction.document.document_type, "statement")
        self.assertTrue(extraction.observed_facts.current_invoice_is_past_due)
        self.assertNotIn("past_due", extraction.document.document_flags)

    def test_fiberfirst_style_statement_labeled_service_bill_routes_as_invoice(self) -> None:
        run_id, operational_repository = self._process_fixture_with_parsed_text(
            subject="FiberFirst Statement 628308",
            body_text=(
                "FiberFirst\n"
                "Statement Date: 05/01/2026\n"
                "Invoice Number: 628308\n"
                "Summary of Charges\n"
                "Previous Balance: $0.00\n"
                "Current Service Charges: $172.89\n"
                "Current Amount Due: $172.89\n"
                "Due Date: 05/28/2026\n"
                "Bill To: Hillwood Commons I HWC1\n"
                "Service Address: Hillwood Commons I"
            ),
            payload_overrides={
                "document_type": "invoice",
                "invoice_number": "628308",
                "due_date": "2026-05-28",
                "vendor_name": "FiberFirst",
                "amount": 172.89,
                "property_code": "HWC1",
                "property_name": "Hillwood Commons I",
                "bill_to": "Hillwood Commons I HWC1",
                "source_attachments": ["FiberFirst Statement 628308.pdf"],
                "has_invoice_attachment": True,
                "evidence_summary": (
                    "Single payable FiberFirst service bill with invoice number 628308, due date, "
                    "current amount due 172.89, service charges, and Hillwood Commons I / HWC1 facts; "
                    "statement labels include Statement Date, Summary of Charges, and Previous Balance."
                ),
            },
            observed_overrides={
                "indicates_statement_or_account_summary": False,
                "has_conflicting_signals": True,
            },
        )

        decision = operational_repository.decisions["decision-1"]
        self.assertEqual(operational_repository.runs[run_id]["final_outcome"], "AUTO")
        self.assertEqual(decision.matched_rule_code, "property_routing_match")
        self.assertEqual(decision.destination_code, "MEDIUS_PROPERTIES")
        extraction = operational_repository.extractions[0]["extraction"]
        self.assertIsNotNone(extraction)
        self.assertEqual(extraction.document.document_type, "invoice")
        self.assertEqual(extraction.invoice.vendor_name, "FiberFirst")
        self.assertEqual(extraction.invoice.invoice_number, "628308")
        self.assertEqual(extraction.invoice.amount, 172.89)
        self.assertEqual(extraction.invoice.property_code, "HWC1")
        self.assertEqual(extraction.invoice.property_name, "Hillwood Commons I")
        self.assertNotIn("statement_or_account_summary", extraction.document.document_flags)

    def test_payment_link_heuristic_requires_link_and_payment_signal(self) -> None:
        self.assertTrue(_looks_like_payment_link_only_email("Your bill is due. Log In https://example.com"))
        self.assertTrue(
            _looks_like_payment_link_only_email(
                "Your bill is available. Amount: $193.98 Due Date: Jun 8, 2026 Click here https://example.com/pay"
            )
        )
        self.assertTrue(
            _looks_like_payment_link_only_email(
                "Utility bill available. Account: 123 Amount: $193.98 Due Date: Jun 8, 2026 View bill https://example.com/pay"
            )
        )
        self.assertFalse(_looks_like_payment_link_only_email("Reminder with no url present"))
        self.assertFalse(_looks_like_payment_link_only_email("Not registered? Click here https://example.com/register"))
        self.assertFalse(
            _looks_like_payment_link_only_email(
                "Service appointment reminder for account 123. Customer Portal: https://example.com/portal"
            )
        )

    def test_vendor_question_heuristic_requires_context_and_call_to_action(self) -> None:
        self.assertTrue(
            _looks_like_vendor_question_or_payment_inquiry(
                "Duplicate payments",
                "Duplicate payments received for INV-100. Can you please confirm?",
            )
        )
        self.assertTrue(
            _looks_like_vendor_question_or_payment_inquiry(
                "ACH question",
                "Missing remittance. Which invoice did this ACH pay?",
            )
        )
        self.assertFalse(_looks_like_vendor_question_or_payment_inquiry("Confirm lunch", "Can you please confirm attendance?"))
        self.assertFalse(_looks_like_vendor_question_or_payment_inquiry("Invoice", "Attached invoice is due for payment."))

    def _process_fixture_with_parsed_text(
        self,
        *,
        subject: str,
        body_text: str,
        payload_overrides: dict[str, Any] | None = None,
        observed_overrides: dict[str, Any] | None = None,
    ) -> tuple[str, InMemoryOperationalRepository]:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_email = root / "local" / "ingest" / "sample.eml"
            fixture = root / "tests" / "fixtures" / "extractions" / "sample.json"
            source_email.parent.mkdir(parents=True)
            fixture.parent.mkdir(parents=True)
            source_email.write_text("sample email", encoding="utf-8")
            overrides = {
                "document_type": "invoice",
                "has_invoice_attachment": False,
                "source_attachments": [],
                "amount": 0,
                "property_code": None,
                "bill_to": "Unmapped Building",
            }
            if payload_overrides:
                overrides.update(payload_overrides)
            payload = _payload(**overrides)
            payload["observed_facts"]["indicates_vendor_question_or_payment_inquiry"] = False
            if observed_overrides:
                payload["observed_facts"].update(observed_overrides)
            fixture.write_text(json.dumps(payload), encoding="utf-8")
            operational_repository = InMemoryOperationalRepository()
            processor = LocalProcessor(
                root,
                InMemoryPolicyRepository(),
                operational_repository,
                teams_notifier=FakeTeamsNotifier(),
            )
            parsed_msg = ParsedMsg(
                subject=subject,
                sender_email="vendor@example.com",
                sender_name="Vendor",
                received_at=None,
                body_text=body_text,
                transport_headers=None,
                attachments=(),
                metadata={},
            )
            with patch("ap_automation.services.local_processor._parse_source_email", return_value=parsed_msg):
                run_id = processor.process_fixture(source_email, fixture)
            return run_id, operational_repository

    def test_graph_intake_routes_to_parent_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fixture = root / "tests" / "fixtures" / "extractions" / "sample.json"
            fixture.parent.mkdir(parents=True)
            fixture.write_text(json.dumps(_payload(document_type="statement")), encoding="utf-8")

            operational_repository = InMemoryOperationalRepository()
            graph_mailbox = FakeGraphMailboxClient()
            policy_repository = InMemoryPolicyRepository()
            policy_repository.destinations["FOLDER_STATEMENTS"] = Destination(
                "FOLDER_STATEMENTS",
                "ESCALATE Statement",
                None,
                "FOLDER_STATEMENTS",
                None,
            )
            processor = LocalProcessor(
                root,
                policy_repository,
                operational_repository,
                graph_mailbox=graph_mailbox,
            )
            envelope = FakeGraphMessageEnvelope(
                message_id="claimed-processing-msg-1",
                categories=("Inbox",),
                internet_message_id="<internet-id-1>",
                web_link="https://outlook.office.com/mail/processing/id/claimed-processing-msg-1",
            )

            processor.process_graph_email(envelope, extraction_fixture_path=fixture)

            self.assertEqual(len(graph_mailbox.calls), 1)
            self.assertEqual(graph_mailbox.calls[0]["message_id"], "claimed-processing-msg-1")
            self.assertEqual(graph_mailbox.calls[0]["parent_folder"], "FOLDER_STATEMENTS")
            self.assertEqual(graph_mailbox.calls[0]["destination_display_name"], "ESCALATE Statement")
            self.assertIsNone(graph_mailbox.calls[0]["destination_folder_path"])
            self.assertEqual(operational_repository.emails["email-1"]["source_message_id"], "claimed-processing-msg-1")
            self.assertEqual(operational_repository.emails["email-1"]["idempotency_key"], "graph_mailbox:<internet-id-1>")
            self.assertEqual(
                operational_repository.emails["email-1"]["metadata"]["claimed_processing_message_id"],
                "claimed-processing-msg-1",
            )
            self.assertEqual(
                operational_repository.emails["email-1"]["office_web_link"],
                "https://outlook.office.com/mail/FOLDER_STATEMENTS/id/moved-claimed-processing-msg-1",
            )
            action_step = next(step for step in operational_repository.steps if step["step_type"] == "ACTION")
            self.assertIn("graph_result", action_step["output_summary"])

    def test_graph_intake_processes_without_extraction_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            operational_repository = InMemoryOperationalRepository()
            processor = LocalProcessor(
                root,
                InMemoryPolicyRepository(),
                operational_repository,
                llm_extractor=FakeAzureOpenAIExtractor(_payload()),
                graph_mailbox=FakeGraphMailboxClient(),
                document_intelligence_analyzer=FakeDocumentIntelligenceAnalyzer(),
            )
            envelope = FakeGraphMessageEnvelope(
                message_id="claimed-processing-msg-1",
                categories=("Inbox",),
                internet_message_id="<internet-id-1>",
                web_link="https://outlook.office.com/mail/processing/id/claimed-processing-msg-1",
            )
            envelope.parsed_msg = ParsedMsg(
                subject="Invoice 100",
                sender_email="vendor@example.com",
                sender_name="Vendor",
                received_at=None,
                body_text="Invoice for 100 Main",
                transport_headers=None,
                attachments=(),
                metadata={"parser": "graph_api"},
            )

            run_id = processor.process_graph_email(envelope)

            self.assertEqual(operational_repository.runs[run_id]["status"], "completed")
            self.assertEqual(
                operational_repository.emails["email-1"]["idempotency_key"],
                "graph_mailbox:<internet-id-1>",
            )

    def test_graph_intake_idempotency_falls_back_to_claimed_message_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fixture = root / "tests" / "fixtures" / "extractions" / "sample.json"
            fixture.parent.mkdir(parents=True)
            fixture.write_text(json.dumps(_payload()), encoding="utf-8")

            operational_repository = InMemoryOperationalRepository()
            processor = LocalProcessor(
                root,
                InMemoryPolicyRepository(),
                operational_repository,
                graph_mailbox=FakeGraphMailboxClient(),
            )
            envelope = FakeGraphMessageEnvelope(
                message_id="claimed-processing-msg-1",
                categories=(),
                internet_message_id=None,
                web_link="https://outlook.office.com/mail/processing/id/claimed-processing-msg-1",
            )

            processor.process_graph_email(envelope, extraction_fixture_path=fixture)

            self.assertEqual(
                operational_repository.emails["email-1"]["idempotency_key"],
                "graph_mailbox:claimed-processing-msg-1",
            )

    def test_graph_intake_sends_teams_notification_when_destination_requests_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fixture = root / "tests" / "fixtures" / "extractions" / "sample.json"
            fixture.parent.mkdir(parents=True)
            fixture.write_text(json.dumps(_payload(flags=["link_only_invoice"], link_only=True, has_invoice_attachment=False)), encoding="utf-8")

            operational_repository = InMemoryOperationalRepository()
            teams_notifier = FakeTeamsNotifier()
            policy_repository = InMemoryPolicyRepository()
            policy_repository.destinations["ESCALATE_LINK_ONLY"] = Destination(
                "ESCALATE_LINK_ONLY",
                "LINK-ONLY",
                None,
                "ESCALATE",
                "Link Only",
                send_teams_message=True,
            )
            processor = LocalProcessor(
                root,
                policy_repository,
                operational_repository,
                graph_mailbox=FakeGraphMailboxClient(),
                teams_notifier=teams_notifier,
            )
            envelope = FakeGraphMessageEnvelope(
                message_id="graph-msg-1",
                categories=("Inbox",),
                internet_message_id="<internet-id-1>",
                web_link="https://outlook.office.com/mail/inbox/id/graph-msg-1",
            )

            processor.process_graph_email(envelope, extraction_fixture_path=fixture)

            self.assertEqual(operational_repository.emails["email-1"]["office_web_link"], "https://outlook.office.com/mail/ESCALATE/id/moved-graph-msg-1")
            self.assertEqual(len(teams_notifier.notifications), 1)
            self.assertEqual(teams_notifier.notifications[0].email_subject, "Invoice 100")
            self.assertEqual(teams_notifier.notifications[0].routing_path, "ESCALATE / LINK-ONLY")
            self.assertEqual(teams_notifier.notifications[0].office_web_link, "https://outlook.office.com/mail/ESCALATE/id/moved-graph-msg-1")
            action_step = next(step for step in operational_repository.steps if step["step_type"] == "ACTION")
            self.assertTrue(action_step["output_summary"]["teams_notification"]["sent"])


class FakeAzureOpenAIExtractor:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.attachment_records: list[dict[str, Any]] = []
        self.asset_reference_rows: list[dict[str, Any]] = []
        self.json_prompt_calls = 0
        self.prompts: list[str] = []

    def extract_msg(
        self,
        parsed_msg,
        attachment_records: list[dict[str, Any]],
        asset_reference_rows: list[dict[str, Any]] | None = None,
    ):
        self.attachment_records = attachment_records
        self.asset_reference_rows = list(asset_reference_rows or [])
        payload = dict(self.payload)
        payload["extractor"] = {
            "type": "azure_openai",
            "name": "azure_openai_foundry",
            "model": "gpt-test",
            "prompt_version": "azure_msg_extraction.v1",
        }
        from ap_automation.services.azure_openai_extractor import ExtractionAttempt

        return ExtractionAttempt(
            parsed_payload=payload,
            prompt="rendered prompt",
            raw_response=json.dumps(payload),
            extractor_type="azure_openai",
            model="gpt-test",
            prompt_version="azure_msg_extraction.v1",
            deployment_name="ap-extractor",
            api_version="2024-10-21",
            request_parameters={"temperature": 0, "response_format": {"type": "json_object"}},
            raw_usage={
                "prompt_tokens": 120,
                "completion_tokens": 80,
                "total_tokens": 200,
                "prompt_tokens_details": {"cached_tokens": 10},
                "completion_tokens_details": {"reasoning_tokens": 5},
            },
            prompt_tokens=120,
            completion_tokens=80,
            total_tokens=200,
            cached_prompt_tokens=10,
            reasoning_tokens=5,
            latency_ms=1234,
        )

    def triage_msg(self, parsed_msg, attachment_records: list[dict[str, Any]]):
        self.attachment_records = attachment_records
        from ap_automation.services.azure_openai_extractor import ExtractionAttempt

        payload = _triage_payload(self.payload)
        return ExtractionAttempt(
            parsed_payload=payload,
            prompt="rendered triage prompt",
            raw_response=json.dumps(payload),
            extractor_type="azure_openai",
            model="gpt-test",
            prompt_version="azure_msg_triage.v1",
            deployment_name="ap-extractor",
            api_version="2024-10-21",
            request_parameters={"temperature": 0, "response_format": {"type": "json_object"}},
            raw_usage={},
            prompt_tokens=20,
            completion_tokens=20,
            total_tokens=40,
            cached_prompt_tokens=None,
            reasoning_tokens=None,
            latency_ms=100,
        )

    def extract_msg_with_triage(
        self,
        parsed_msg,
        attachment_records: list[dict[str, Any]],
        triage_batch,
        asset_reference_rows: list[dict[str, Any]] | None = None,
    ):
        return self.extract_msg(parsed_msg, attachment_records, asset_reference_rows=asset_reference_rows)

    def run_json_prompt(self, prompt: str):
        self.json_prompt_calls += 1
        self.prompts.append(prompt)
        raw = {
            "schema_version": "llm_property_match_batch.v1",
            "items": [
                {
                    "item_key": item_key,
                    "interpretation": {
                        "schema_version": "llm_interpretation.v1",
                        "candidate_property_matches": [
                            {
                                "asset_id": "asset-HW1",
                                "asset_alias": "HW1",
                                "confidence": 0.93,
                                "evidence": [{"source": "fixture", "page": None, "text": "Hillwood One"}],
                            }
                        ],
                        "candidate_rule_matches": [],
                        "ambiguity_flags": [],
                        "recommended_outcome": None,
                        "reason": "Test reviewer selected asset-HW1.",
                    },
                }
                for item_key in _batch_prompt_item_keys(prompt)
            ],
        }
        return json.dumps(raw), raw


class PastDueTriageFakeAzureOpenAIExtractor(FakeAzureOpenAIExtractor):
    def triage_msg(self, parsed_msg, attachment_records: list[dict[str, Any]]):
        attempt = super().triage_msg(parsed_msg, attachment_records)
        payload = json.loads(json.dumps(attempt.parsed_payload))
        for item in payload.get("items", []):
            if isinstance(item, dict):
                item["risk_flags"] = sorted(set(item.get("risk_flags") or []) | {"past_due"})
                item["extraction_route"] = "exception_detail"
                item["reason"] = "Test triage incorrectly flagged account-level aging as past due."
        return replace(attempt, parsed_payload=payload, raw_response=json.dumps(payload))


class MultiInvoiceTriageFakeAzureOpenAIExtractor(FakeAzureOpenAIExtractor):
    def triage_msg(self, parsed_msg, attachment_records: list[dict[str, Any]]):
        attempt = super().triage_msg(parsed_msg, attachment_records)
        payload = json.loads(json.dumps(attempt.parsed_payload))
        for item in payload.get("items", []):
            if isinstance(item, dict):
                item["risk_flags"] = sorted(set(item.get("risk_flags") or []) | {"multi_invoice"})
                item["extraction_route"] = "exception_detail"
                item["reason"] = "Test triage incorrectly flagged a single invoice with aging footer as multi-invoice."
        return replace(attempt, parsed_payload=payload, raw_response=json.dumps(payload))


class AssetAwareFakeAzureOpenAIExtractor(FakeAzureOpenAIExtractor):
    def __init__(self) -> None:
        super().__init__(_payload(property_code=None, property_name=None, bill_to="Invoice site"))

    def extract_msg(
        self,
        parsed_msg,
        attachment_records: list[dict[str, Any]],
        asset_reference_rows: list[dict[str, Any]] | None = None,
    ):
        text = " ".join(value for value in (parsed_msg.subject, parsed_msg.body_text) if value).lower()
        payload = _payload(property_code=None, property_name=None, bill_to="Invoice site")
        payload["property_lookup"] = {
            "property_code": [],
            "property_name": [],
            "tenant": [],
            "address": [],
            "suite": [],
            "city": [],
            "state": [],
            "zipcode": [],
        }
        for row in asset_reference_rows or []:
            asset_name = str(row.get("asset_name") or "")
            asset_alias = str(row.get("asset_alias") or "")
            address = str(row.get("address") or "")
            if "3202 alliance gateway 34" in text and asset_name == "Alliance Gateway 34":
                payload["property_lookup"]["property_name"] = ["alliance gateway 34"]
                payload["property_lookup"]["property_code"] = ["gw34"]
                payload["property_lookup"]["address"] = ["3202 alliance gateway freeway"]
                payload["evidence"]["summary"] = f"Visible source text matched {asset_name} / {asset_alias} at {address}."
                break
        self.payload = payload
        return super().extract_msg(parsed_msg, attachment_records, asset_reference_rows=asset_reference_rows)


class RetryingAzureOpenAIExtractor(FakeAzureOpenAIExtractor):
    def __init__(self, first_payload: dict[str, Any], retry_payload: dict[str, Any]) -> None:
        super().__init__(first_payload)
        self.retry_payload = retry_payload

    def extract_msg(
        self,
        parsed_msg,
        attachment_records: list[dict[str, Any]],
        asset_reference_rows: list[dict[str, Any]] | None = None,
    ):
        self.json_prompt_calls += 1
        return super().extract_msg(parsed_msg, attachment_records, asset_reference_rows=asset_reference_rows)

    def run_json_prompt(self, prompt: str):
        self.json_prompt_calls += 1
        self.prompts.append(prompt)
        payload = dict(self.retry_payload)
        payload["extractor"] = {
            "type": "azure_openai",
            "name": "azure_openai_foundry",
            "model": "gpt-test",
            "prompt_version": "azure_msg_extraction.v1",
        }
        return json.dumps(payload), payload


class BadTriageAzureOpenAIExtractor(FakeAzureOpenAIExtractor):
    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__(payload)
        self.detail_calls = 0

    def triage_msg(self, parsed_msg, attachment_records: list[dict[str, Any]]):
        from ap_automation.services.azure_openai_extractor import ExtractionAttempt

        payload = {"schema_version": "wrong", "items": []}
        return ExtractionAttempt(
            parsed_payload=payload,
            prompt="bad triage prompt",
            raw_response=json.dumps(payload),
            extractor_type="azure_openai",
            model="gpt-test",
            prompt_version="azure_msg_triage.v1",
        )

    def extract_msg_with_triage(
        self,
        parsed_msg,
        attachment_records: list[dict[str, Any]],
        triage_batch,
        asset_reference_rows: list[dict[str, Any]] | None = None,
    ):
        self.detail_calls += 1
        return super().extract_msg_with_triage(parsed_msg, attachment_records, triage_batch, asset_reference_rows=asset_reference_rows)

    def run_json_prompt(self, prompt: str):
        payload = {"schema_version": "still_wrong", "items": []}
        return json.dumps(payload), payload


def _triage_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") == "extraction_batch.v1":
        raw_items = payload.get("items") or []
        triage_items = [
            _triage_item_from_extraction(
                item.get("extraction", {}),
                item_key=str(item.get("item_key") or f"item:{index}"),
                item_kind=str(item.get("item_kind") or "attachment"),
                display_name=item.get("display_name"),
            )
            for index, item in enumerate(raw_items)
            if isinstance(item, dict)
        ]
    else:
        triage_items = [_triage_item_from_extraction(payload, item_key="attachment:invoice", item_kind="attachment", display_name="invoice.pdf")]
    return {
        "schema_version": "extraction_triage_batch.v1",
        "excluded_attachments": [],
        "items": triage_items,
    }


def _triage_item_from_extraction(
    extraction: dict[str, Any],
    *,
    item_key: str,
    item_kind: str,
    display_name: Any,
) -> dict[str, Any]:
    document = extraction.get("document") if isinstance(extraction.get("document"), dict) else {}
    evidence = extraction.get("evidence") if isinstance(extraction.get("evidence"), dict) else {}
    observed = extraction.get("observed_facts") if isinstance(extraction.get("observed_facts"), dict) else {}
    document_type = str(document.get("document_type") or "invoice")
    risk_flags: list[str] = []
    if document.get("link_only") or observed.get("mentions_payment_link_only"):
        risk_flags.append("link_only")
    if document.get("multi_invoice") or observed.get("indicates_multiple_invoices"):
        risk_flags.append("multi_invoice")
    if observed.get("indicates_vendor_question_or_payment_inquiry"):
        risk_flags.append("vendor_question_or_payment_inquiry")
    if document_type in {"contract", "pay_application"}:
        risk_flags.append("contract_or_pay_application")
    route = "invoice_detail"
    if document_type in {"statement", "account_summary", "ach_notice", "auto_draft_notice", "ben_e_keith_notice"}:
        route = "statement_detail"
    if document_type in {"contract", "pay_application", "vendor_question", "payment_inquiry", "past_due_notice", "lien_release", "unknown"} or risk_flags:
        route = "exception_detail"
    source_attachments = evidence.get("source_attachments")
    return {
        "item_kind": item_kind if item_kind in {"attachment", "email"} else "attachment",
        "item_key": item_key,
        "display_name": display_name if isinstance(display_name, str) else "invoice.pdf",
        "source_attachments": source_attachments if isinstance(source_attachments, list) else [],
        "document_type": document_type,
        "requires_detail_extraction": True,
        "extraction_route": route,
        "risk_flags": risk_flags,
        "confidence": 0.9,
        "reason": "Test triage item derived from fake extraction payload.",
    }


def _batch_prompt_item_keys(prompt: str) -> list[str]:
    marker = "Input:\n"
    payload = json.loads(prompt.split(marker, 1)[1])
    return [str(item["item_key"]) for item in payload.get("items", [])]


class FakeDocumentIntelligenceDependencyStatus:
    available = True
    detail = "fake"


class FakeDocumentIntelligenceAnalyzer:
    analysis_version = "document_intelligence.v1"
    dependency_status = FakeDocumentIntelligenceDependencyStatus()

    def __init__(self, success: bool = True, text_excerpt: str | None = "di invoice text", errors: list[str] | None = None) -> None:
        self.success = success
        self.text_excerpt = text_excerpt
        self.errors = errors or []

    def analyze_attachments(self, attachment_records: list[dict[str, Any]], *, run_id: str, require_config: bool) -> list[dict[str, Any]]:
        results = []
        for record in attachment_records:
            if (record.get("metadata") or {}).get("is_inline"):
                status = "skipped_inline"
                eligible = False
                text_excerpt = None
            elif not self.success:
                status = "unsupported_file_type"
                eligible = False
                text_excerpt = None
            else:
                status = "success"
                eligible = True
                text_excerpt = self.text_excerpt
            results.append(
                {
                    "eligible": eligible,
                    "status": status,
                    "reason_code": status,
                    "model_ids": ["prebuilt-layout"] if eligible else [],
                    "page_count": 1 if eligible else 0,
                    "text_excerpt": text_excerpt,
                    "fields": {},
                    "confidences": {},
                    "artifact_paths": [],
                    "latency_ms": 1,
                    "errors": self.errors,
                    "analysis_version": self.analysis_version,
                }
            )
        return results


class FailingDocumentIntelligenceAnalyzer(FakeDocumentIntelligenceAnalyzer):
    def analyze_attachments(self, attachment_records: list[dict[str, Any]], *, run_id: str, require_config: bool) -> list[dict[str, Any]]:
        raise RuntimeError("missing DI config")


class InMemoryOperationalRepository:
    def __init__(self) -> None:
        self.emails: dict[str, dict[str, Any]] = {}
        self.runs: dict[str, dict[str, Any]] = {}
        self.steps: list[dict[str, Any]] = []
        self.extractions: list[dict[str, Any]] = []
        self.invoice_facts: list[dict[str, Any]] = []
        self.llm_interactions: list[dict[str, Any]] = []
        self.attachments: list[dict[str, Any]] = []
        self.document_items: list[dict[str, Any]] = []
        self.decisions: dict[str, Decision] = {}
        self.actions: list[dict[str, Any]] = []
        self.escalate_items: list[dict[str, Any]] = []

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
        document_item_id: str | None = None,
    ) -> dict[str, Any]:
        persisted_output = dict(raw_output or parsed_payload)
        self.extractions.append(
            {
                "email_id": email_id,
                "extraction": extraction,
                "parsed_payload": parsed_payload,
                "raw_output": persisted_output,
                "errors": validation_errors,
                "document_item_id": document_item_id,
            }
        )
        return persisted_output

    def save_llm_interaction(self, email_id: str, run_id: str, interaction: dict[str, Any]) -> None:
        self.llm_interactions.append({"email_id": email_id, "run_id": run_id, **interaction})

    def save_attachments(self, email_id: str, attachments: list[dict[str, Any]]) -> None:
        for attachment in attachments:
            self.attachments.append({"email_id": email_id, **attachment})

    def save_document_item(
        self,
        email_id: str,
        item_kind: str,
        item_key: str,
        display_name: str | None,
        metadata: dict[str, Any],
        attachment_id: str | None = None,
    ) -> str:
        document_item_id = f"document-item-{len(self.document_items) + 1}"
        self.document_items.append(
            {
                "document_item_id": document_item_id,
                "email_id": email_id,
                "item_kind": item_kind,
                "item_key": item_key,
                "display_name": display_name,
                "attachment_id": attachment_id,
                "metadata": metadata,
            }
        )
        return document_item_id

    def save_invoice_fact(self, email_id: str, extraction: ExtractionPayload, document_item_id: str | None = None) -> None:
        self.invoice_facts.append(
            {
                "email_id": email_id,
                "document_item_id": document_item_id,
                "vendor_name": extraction.invoice.vendor_name,
                "invoice_number": extraction.invoice.invoice_number,
                "invoice_date": extraction.invoice.invoice_date,
                "amount": extraction.invoice.amount,
                "currency": extraction.invoice.currency,
            }
        )

    def update_email_html_storage_path(self, email_id: str, html_storage_path: str) -> None:
        self.emails[email_id]["html_storage_path"] = html_storage_path

    def update_email_office_web_link(self, email_id: str, office_web_link: str) -> None:
        self.emails[email_id]["office_web_link"] = office_web_link
        metadata = self.emails[email_id].setdefault("metadata", {})
        metadata["office_web_link"] = office_web_link

    def save_decision(self, email_id: str, run_id: str, decision: Decision, document_item_id: str | None = None) -> str:
        decision_id = f"decision-{len(self.decisions) + 1}"
        self.decisions[decision_id] = decision
        return decision_id

    def save_action(self, email_id: str, decision_id: str, decision: Decision, manifest_path: str, document_item_id: str | None = None) -> None:
        self.actions.append({"email_id": email_id, "decision_id": decision_id, "manifest_path": manifest_path, "document_item_id": document_item_id})

    def enqueue_escalate(self, email_id: str, decision_id: str, reason: str, priority: str = "normal", document_item_id: str | None = None) -> None:
        self.escalate_items.append({"email_id": email_id, "decision_id": decision_id, "reason": reason, "priority": priority, "document_item_id": document_item_id})

    def finalize_audit_run(self, run_id: str, final_outcome: str, trace_artifact_path: str) -> None:
        self.runs[run_id]["status"] = "completed"
        self.runs[run_id]["final_outcome"] = final_outcome
        self.runs[run_id]["trace_artifact_path"] = trace_artifact_path

    def fail_audit_run(self, run_id: str, error: str, trace_artifact_path: str | None = None) -> None:
        self.runs[run_id]["status"] = "failed"
        self.runs[run_id]["error"] = error
        self.runs[run_id]["trace_artifact_path"] = trace_artifact_path


class AliasAwareInMemoryPolicyRepository(InMemoryPolicyRepository):
    def evaluate_property_match(self, extraction):
        values = [
            *extraction.property_lookup.property_code,
            *extraction.property_lookup.property_name,
            *extraction.property_lookup.tenant,
            *extraction.property_lookup.address,
            *extraction.property_lookup.suite,
            *extraction.property_lookup.city,
            *extraction.property_lookup.state,
            *extraction.property_lookup.zipcode,
            *extraction.business_signals.possible_property_aliases,
        ]
        match = None
        for value in values:
            if value in self.properties:
                match = self.properties[value]
                break
        if match is None and "gateway51" in extraction.property_lookup.property_name:
            match = self.properties["HW1"]
        return PropertyMatchEvaluation(
            property_match=match,
            standardized_signals={"query_values": [value for value in values if value]},
            candidates=(),
            llm_advisory={"candidate_property_codes": []},
            gate={"passed": bool(match), "reason": "test repository"},
            lookup_audit={
                "sql": "select property candidates",
                "sent_payload": {"values": [value for value in values if value]},
                "returned_payload": [match.to_audit_dict()] if match else [],
            },
        )


class AllianceGatewayPolicyRepository(AliasAwareInMemoryPolicyRepository):
    def __init__(self) -> None:
        super().__init__()
        from ap_automation.models.decision import PropertyMatch

        self.properties = {
            "gw34": PropertyMatch(
                "asset-GW34",
                "GW34",
                "Alliance Gateway 34",
                "hillwood_owned",
                "hillwood_owned",
                "industrial",
                "PROP",
                "MEDIUS_PROPERTIES",
            )
        }

    def get_asset_reference_rows(self) -> list[dict[str, Any]]:
        return [
            {
                "asset_name": "Alliance Gateway 34",
                "asset_alias": "GW34",
                "asset_type": "industrial",
                "address": "3202 Alliance Gateway Freeway, Fort Worth, TX 76177",
            }
        ]


def _batch(*payloads: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "extraction_batch.v1",
        "items": [
            {
                "item_kind": "attachment",
                "item_key": f"attachment:{index}",
                "display_name": payload["evidence"]["source_attachments"][0],
                "metadata": {},
                "extraction": payload,
            }
            for index, payload in enumerate(payloads, start=1)
        ],
    }


class FakePropertyMatchAssistant:
    def __init__(self, asset_id: str) -> None:
        self.asset_id = asset_id

    def suggest(self, extraction, alias_mappings):
        from ap_automation.agents.property_match_assistant import PropertyMatchSuggestion
        from ap_automation.models.llm_interpretation import validate_llm_interpretation

        raw = {
            "schema_version": "llm_interpretation.v1",
            "candidate_property_matches": [
                {
                    "asset_id": self.asset_id,
                    "asset_alias": "HW1",
                    "confidence": 0.93,
                    "evidence": [{"source": "fixture", "page": None, "text": "Gateway 51"}],
                }
            ],
            "candidate_rule_matches": [],
            "ambiguity_flags": [],
            "recommended_outcome": "AUTO",
            "reason": "Test suggestion",
        }

        return PropertyMatchSuggestion(
            candidate_asset_ids=(self.asset_id,),
            confidence=0.93,
            reason="Test suggestion",
            interpretation=validate_llm_interpretation(raw, allowed_asset_ids={self.asset_id}),
            raw_response=json.dumps(raw),
            prompt="test prompt",
        )


class FakeGraphMailboxClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def route_message(
        self,
        message_id: str,
        existing_categories: tuple[str, ...],
        parent_folder: str | None,
        label: str | None,
        destination_display_name: str | None = None,
        destination_folder_path: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "message_id": message_id,
                "existing_categories": existing_categories,
                "parent_folder": parent_folder,
                "label": label,
                "destination_display_name": destination_display_name,
                "destination_folder_path": destination_folder_path,
            }
        )
        return {
            "moved": True,
            "message_id": f"moved-{message_id}",
            "office_web_link": f"https://outlook.office.com/mail/{parent_folder}/id/moved-{message_id}",
        }


class FakeGraphMessageEnvelope:
    def __init__(
        self,
        message_id: str,
        categories: tuple[str, ...],
        internet_message_id: str | None,
        web_link: str | None = "https://outlook.office.com/mail/inbox/id/graph-msg-1",
    ) -> None:
        self.message_id = message_id
        self.categories = categories
        self.internet_message_id = internet_message_id
        self.web_link = web_link
        self.parsed_msg = None


class FakeTeamsNotifier:
    def __init__(self) -> None:
        self.notifications: list[Any] = []

    def send_review_notification(self, notification):
        self.notifications.append(notification)
        return {"team": "Properties AP", "channel": "Properties AP", "status_code": 200}
