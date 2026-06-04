from __future__ import annotations

import unittest
from dataclasses import replace
from typing import Any

from ap_automation.models.decision import (
    Destination,
    NoActionEmailPattern,
    PropertyMatch,
    PropertyMatchCandidate,
    PropertyMatchEvaluation,
    WorkflowRule,
)
from ap_automation.models.extraction import validate_extraction
from ap_automation.repositories.postgres import _normalize_property_value
from ap_automation.repositories.protocols import PolicyRepository
from ap_automation.services.decision_engine import DecisionContext, DecisionEngine, MissingWorkflowConfigError


class DecisionEngineGoldenScenarioTests(unittest.TestCase):
    def test_clean_hillwood_owned_invoice_routes_to_MEDIUS_PROPERTIES(self) -> None:
        decision = self._decide(property_code="HW1")

        self.assertEqual(decision.outcome, "AUTO")
        self.assertEqual(decision.destination_code, "MEDIUS_PROPERTIES")
        self.assertEqual(decision.matched_rule_code, "property_routing_match")

    def test_clean_external_pm_invoice_routes_to_pm_destination(self) -> None:
        decision = self._decide(property_code="EXT1")

        self.assertEqual(decision.outcome, "AUTO")
        self.assertEqual(decision.destination_code, "TIFFANY_BECK")

    def test_hc2_hyphenated_code_routes_to_michele_destination(self) -> None:
        decision = self._decide(property_code="HC-2", bill_to="Hillwood Properties", business_unit_code="PROP")

        self.assertEqual(decision.outcome, "AUTO")
        self.assertEqual(decision.destination_code, "MICHELE_FELLERS")
        self.assertEqual(decision.matched_rule_code, "property_routing_match")

    def test_deliver_to_address_candidate_ranks_before_bill_to_address(self) -> None:
        decision = self._decide(
            property_code=None,
            bill_to="Hillwood Alliance Group, 9800 Hillwood Parkway, Fort Worth TX 76177",
            property_lookup={
                "property_code": [],
                "property_name": [],
                "tenant": [],
                "address": [],
                "suite": [],
                "city": [],
                "state": [],
                "zipcode": [],
                "address_candidates": [
                    {
                        "rank": 1,
                        "label": "deliver_to",
                        "street": "2451 westlake parkway",
                        "city": "westlake",
                        "state": "tx",
                        "zipcode": "76262",
                        "normalized_address": "2451 westlake parkway westlake tx 76262",
                        "source": "attachment:invoice.pdf:page 1",
                        "confidence": 0.94,
                        "evidence_text": "DELIVER TO 2451 WESTLAKE PKWY",
                    },
                    {
                        "rank": 2,
                        "label": "bill_to",
                        "street": "9800 hillwood parkway",
                        "city": "fort worth",
                        "state": "tx",
                        "zipcode": "76177",
                        "normalized_address": "9800 hillwood parkway fort worth tx 76177",
                        "source": "attachment:invoice.pdf:page 1",
                        "confidence": 0.80,
                        "evidence_text": "Bill To Hillwood Alliance Group",
                    },
                ],
            },
        )

        self.assertEqual(decision.outcome, "AUTO")
        self.assertEqual(decision.destination_code, "TIFFANY_BECK")
        self.assertEqual(decision.routing_match["property_match"]["property_code"], "WEST1")

    def test_bill_to_address_fallback_routes_when_only_active_address_candidate_matches(self) -> None:
        decision = self._decide(
            property_code=None,
            property_name=None,
            service_address=None,
            bill_to="Hillwood Alliance Group, 9800 Hillwood Parkway, Fort Worth TX 76177",
            property_lookup={
                "property_code": [],
                "property_name": [],
                "tenant": [],
                "address": ["9800 hillwood parkway", "9800 hillwood parkway fort worth tx 76177"],
                "suite": [],
                "city": ["fort worth"],
                "state": ["tx"],
                "zipcode": ["76177"],
                "address_candidates": [
                    {
                        "rank": 1,
                        "label": "bill_to",
                        "street": "9800 hillwood parkway",
                        "city": "fort worth",
                        "state": "tx",
                        "zipcode": "76177",
                        "normalized_address": "9800 hillwood parkway fort worth tx 76177",
                        "source": "attachment:invoice.pdf:page 1",
                        "confidence": 0.86,
                        "evidence_text": "Bill To: Hillwood Alliance Group 9800 Hillwood Parkway",
                    }
                ],
            },
            possible_property_aliases=["SH114", "Torc Entitlements"],
        )

        self.assertEqual(decision.outcome, "AUTO")
        self.assertEqual(decision.destination_code, "MEDIUS_PROPERTIES")
        self.assertEqual(decision.routing_match["property_gate"]["reason"], "bill_to_address_fallback_selected")
        self.assertEqual(decision.routing_match["property_llm_advisory"]["reason"], "bill_to_address_fallback_selected")

    def test_bill_to_address_fallback_routes_when_project_text_is_unresolved(self) -> None:
        decision = self._decide(
            property_code=None,
            property_name="Frisco Station WMP",
            service_address=None,
            bill_to="Kim Cole, Hillwood Properties, 9800 Hillwood Parkway, Suite #300, Fort Worth, TX 76177",
            bill_to_name_line_1="Kim Cole",
            bill_to_name_line_2="Hillwood Properties",
            bill_to_street_address="9800 Hillwood Parkway",
            bill_to_suite="300",
            bill_to_city="Fort Worth",
            bill_to_state="TX",
            bill_to_zip_code="76177",
            property_lookup={
                "property_code": [],
                "property_name": ["frisco station"],
                "tenant": [],
                "address": [],
                "suite": [],
                "city": [],
                "state": [],
                "zipcode": [],
                "address_candidates": [],
            },
            possible_property_aliases=["frisco station wmp"],
        )

        self.assertEqual(decision.outcome, "AUTO")
        self.assertEqual(decision.destination_code, "MEDIUS_PROPERTIES")
        self.assertEqual(decision.routing_match["property_gate"]["reason"], "bill_to_address_fallback_selected")

    def test_bill_to_address_fallback_routes_westwood_unresolved_project_to_9800_asset(self) -> None:
        repository = InMemoryPolicyRepository()
        extraction = validate_extraction(
            _payload(
                property_code=None,
                property_name="Hillwood - 2026 CTR MIB",
                service_address=None,
                bill_to="Hillwood Properties, 9800 Hillwood Parkway, Fort Worth TX 76177",
                bill_to_street_address="9800 Hillwood Parkway",
                bill_to_city="Fort Worth",
                bill_to_state="TX",
                bill_to_zip_code="76177",
                property_lookup={
                    "property_code": [],
                    "property_name": ["hillwood 2026 ctr mib"],
                    "tenant": [],
                    "address": ["9800 hillwood parkway", "9800 hillwood parkway fort worth tx 76177"],
                    "suite": [],
                    "city": ["fort worth"],
                    "state": ["tx"],
                    "zipcode": ["76177"],
                    "address_candidates": [
                        {
                            "rank": 1,
                            "label": "bill_to",
                            "street": "9800 hillwood parkway",
                            "city": "fort worth",
                            "state": "tx",
                            "zipcode": "76177",
                            "normalized_address": "9800 hillwood parkway fort worth tx 76177",
                            "source": "attachment:westwood.pdf:page 1",
                            "confidence": 0.91,
                            "evidence_text": "Bill To: 9800 Hillwood Parkway",
                        }
                    ],
                },
                possible_property_aliases=["ctr mib"],
            )
        )
        evaluation = PropertyMatchEvaluation(
            property_match=None,
            standardized_signals={
                "raw_input_signals": {},
                "standardized_query_values": ["hillwood 2026 ctr mib", "ctr mib", "9800 hillwood parkway"],
            },
            candidates=(
                PropertyMatchCandidate(
                    "asset-HWC1",
                    "HWC1",
                    "Hillwood Commons I",
                    "MEDIUS_PROPERTIES",
                    "9800 Hillwood Parkway",
                    "address",
                    1.0,
                ),
            ),
            llm_advisory={},
            gate={"passed": True, "reason": "Property fuzzy gate passed", "top_n": 5, "min_score": 0.45},
        )

        decision = DecisionEngine(repository, property_match_reviewer=FakePropertyMatchReviewer(selected_asset_id="asset-HWC1")).decide(
            extraction,
            "key",
            property_match_evaluation=evaluation,
        ).decision

        self.assertEqual(decision.outcome, "AUTO")
        self.assertEqual(decision.destination_code, "MEDIUS_PROPERTIES")
        self.assertEqual(decision.routing_match["property_match"]["property_code"], "HWC1")
        self.assertEqual(decision.routing_match["property_gate"]["reason"], "bill_to_address_fallback_selected")

    def test_bill_to_address_fallback_escalates_when_property_name_maps_to_different_candidate(self) -> None:
        repository = InMemoryPolicyRepository()
        extraction = validate_extraction(
            _payload(
                property_code=None,
                property_name="Westlake One",
                service_address=None,
                bill_to="Hillwood Properties, 9800 Hillwood Parkway, Fort Worth TX 76177",
                property_lookup={
                    "property_code": [],
                    "property_name": ["westlake one"],
                    "tenant": [],
                    "address": ["9800 hillwood parkway"],
                    "suite": [],
                    "city": ["fort worth"],
                    "state": ["tx"],
                    "zipcode": ["76177"],
                    "address_candidates": [
                        {
                            "rank": 1,
                            "label": "bill_to",
                            "street": "9800 hillwood parkway",
                            "city": "fort worth",
                            "state": "tx",
                            "zipcode": "76177",
                            "normalized_address": "9800 hillwood parkway fort worth tx 76177",
                            "source": "attachment:invoice.pdf:page 1",
                            "confidence": 0.9,
                            "evidence_text": "Bill To: 9800 Hillwood Parkway",
                        }
                    ],
                },
            )
        )
        evaluation = PropertyMatchEvaluation(
            property_match=None,
            standardized_signals={"raw_input_signals": {}, "standardized_query_values": ["westlake one", "9800 hillwood parkway"]},
            candidates=(
                PropertyMatchCandidate("asset-HW1", "HW1", "Hillwood One", "MEDIUS_PROPERTIES", "9800 Hillwood Parkway", "address", 1.0),
                PropertyMatchCandidate("asset-WEST1", "WEST1", "Westlake One", "TIFFANY_BECK", "Westlake One", "property_name", 0.95),
            ),
            llm_advisory={},
            gate={"passed": True, "reason": "Property fuzzy gate passed", "top_n": 5, "min_score": 0.45},
        )

        decision = DecisionEngine(repository, property_match_reviewer=FakePropertyMatchReviewer(selected_asset_id="asset-HW1")).decide(
            extraction,
            "key",
            property_match_evaluation=evaluation,
        ).decision

        self.assertEqual(decision.outcome, "ESCALATE")
        self.assertEqual(decision.destination_code, "ESCALATE_UNMATCHED_BUILDING")
        self.assertEqual(decision.routing_match["property_gate"]["reason"], "Bill-to address fallback blocked by stronger conflicting property evidence")

    def test_bill_to_address_fallback_escalates_when_multiple_active_assets_share_address(self) -> None:
        repository = InMemoryPolicyRepository()
        repository.properties["HW1B"] = PropertyMatch("asset-HW1B", "HW1B", "Hillwood One B", "hillwood_owned", "Hillwood", "industrial", "PROP", "MEDIUS_PROPERTIES")
        extraction = validate_extraction(
            _payload(
                property_code=None,
                property_name=None,
                service_address=None,
                bill_to="Hillwood Alliance Group, 9800 Hillwood Parkway, Fort Worth TX 76177",
                property_lookup={
                    "property_code": [],
                    "property_name": [],
                    "tenant": [],
                    "address": ["9800 hillwood parkway", "9800 hillwood parkway fort worth tx 76177"],
                    "suite": [],
                    "city": ["fort worth"],
                    "state": ["tx"],
                    "zipcode": ["76177"],
                    "address_candidates": [
                        {
                            "rank": 1,
                            "label": "bill_to",
                            "street": "9800 hillwood parkway",
                            "city": "fort worth",
                            "state": "tx",
                            "zipcode": "76177",
                            "normalized_address": "9800 hillwood parkway fort worth tx 76177",
                            "source": "attachment:invoice.pdf:page 1",
                            "confidence": 0.86,
                            "evidence_text": "Bill To: Hillwood Alliance Group 9800 Hillwood Parkway",
                        }
                    ],
                },
            )
        )

        decision = DecisionEngine(repository, property_match_reviewer=FakePropertyMatchReviewer()).decide(extraction, "key").decision

        self.assertEqual(decision.outcome, "ESCALATE")
        self.assertEqual(decision.destination_code, "ESCALATE_UNMATCHED_BUILDING")
        self.assertEqual(decision.routing_match["property_gate"]["reason"], "Bill-to address fallback was ambiguous across multiple active property candidates")

    def test_bill_to_only_candidate_below_score_threshold_escalates_unmatched_building(self) -> None:
        repository = InMemoryPolicyRepository()
        extraction = validate_extraction(
            _payload(
                property_code=None,
                property_name=None,
                service_address=None,
                bill_to="Hillwood Alliance Group, 9800 Hillwood Parkway, Fort Worth TX 76177",
                property_lookup={
                    "property_code": [],
                    "property_name": [],
                    "tenant": [],
                    "address": ["9800 hillwood parkway"],
                    "suite": [],
                    "city": ["fort worth"],
                    "state": ["tx"],
                    "zipcode": ["76177"],
                    "address_candidates": [
                        {
                            "rank": 1,
                            "label": "bill_to",
                            "street": "9800 hillwood parkway",
                            "city": "fort worth",
                            "state": "tx",
                            "zipcode": "76177",
                            "normalized_address": "9800 hillwood parkway fort worth tx 76177",
                            "source": "attachment:invoice.pdf:page 1",
                            "confidence": 0.86,
                            "evidence_text": "Bill To: Hillwood Alliance Group 9800 Hillwood Parkway",
                        }
                    ],
                },
            )
        )
        evaluation = PropertyMatchEvaluation(
            property_match=None,
            standardized_signals={"raw_input_signals": {}, "standardized_query_values": ["9800 hillwood parkway"]},
            candidates=(
                PropertyMatchCandidate("asset-HW1", "HW1", "Hillwood One", "MEDIUS_PROPERTIES", "9800 Hillwood Parkway", "address", 0.44),
            ),
            llm_advisory={},
            gate={"passed": False, "reason": "Property fuzzy gate failed: score requirement not met", "top_n": 5, "min_score": 0.45},
        )

        decision = DecisionEngine(repository, property_match_reviewer=FakePropertyMatchReviewer(selected_asset_id="asset-HW1")).decide(
            extraction,
            "key",
            property_match_evaluation=evaluation,
        ).decision

        self.assertEqual(decision.outcome, "ESCALATE")
        self.assertEqual(decision.destination_code, "ESCALATE_UNMATCHED_BUILDING")

    def test_alc_invoice_routes_to_alc_escalation(self) -> None:
        decision = self._decide(business_unit_code="ALC", property_code=None, bill_to="Alliance Landscape Company")

        self.assertEqual(decision.outcome, "ESCALATE")
        self.assertEqual(decision.destination_code, "ESCALATE_ALC")
        self.assertEqual(decision.matched_rule_code, "alc_escalation")

    def test_multifamily_invoice_does_not_route_to_alc_escalation(self) -> None:
        decision = self._decide(business_unit_code="MF", property_code=None, bill_to="Multifamily")

        self.assertEqual(decision.outcome, "ESCALATE")
        self.assertNotEqual(decision.matched_rule_code, "alc_escalation")
        self.assertNotEqual(decision.destination_code, "ESCALATE_ALC")

    def test_multifamily_asset_type_routes_to_property_destination(self) -> None:
        decision = self._decide(property_code="MF1", business_unit_code=None, bill_to="Garden Property")

        self.assertEqual(decision.outcome, "ESCALATE")
        self.assertEqual(decision.destination_code, "ESCALATE_MULTIFAMILY")
        self.assertEqual(decision.matched_rule_code, "asset_type_multifamily")

    def test_multi_invoice_pdf_routes_to_ESCALATE(self) -> None:
        decision = self._decide(flags=["multi_invoice_pdf"], multi_invoice=True)

        self.assertEqual(decision.outcome, "ESCALATE")
        self.assertEqual(decision.matched_rule_code, "hard_multi_invoice_pdf")
        self.assertEqual(decision.destination_code, "ESCALATE_MULTI_INVOICE_PDF")

    def test_separate_lien_waiver_routes_to_ESCALATE(self) -> None:
        repository = InMemoryPolicyRepository()
        extraction = validate_extraction(_payload(flags=["lien_release_related"]))
        extraction = replace(
            extraction,
            document=replace(
                extraction.document,
                document_flags=(*extraction.document.document_flags, "separate_lien_waiver"),
            ),
        )
        decision = DecisionEngine(repository, property_match_reviewer=FakePropertyMatchReviewer()).decide(
            extraction,
            "idempotency-key",
        ).decision

        self.assertEqual(decision.outcome, "ESCALATE")
        self.assertEqual(decision.matched_rule_code, "hard_separate_lien_waiver")
        self.assertEqual(decision.destination_code, "ESCALATE_LIEN_WAIVER")

    def test_lien_release_related_alone_does_not_route_to_multi_pdf_merge(self) -> None:
        decision = self._decide(flags=["lien_release_related"])

        self.assertNotEqual(decision.matched_rule_code, "hard_invoice_plus_lien_waiver")
        self.assertNotEqual(decision.destination_code, "ESCALATE_MULTI_PDF_MERGE")

    def test_link_only_invoice_routes_to_ESCALATE(self) -> None:
        decision = self._decide(flags=["link_only_invoice"], link_only=True, has_invoice_attachment=False)

        self.assertEqual(decision.outcome, "ESCALATE")
        self.assertEqual(decision.matched_rule_code, "hard_link_only_invoice")
        self.assertEqual(decision.destination_code, "ESCALATE_LINK_ONLY")

    def test_link_only_invoice_without_property_or_business_unit_routes_to_link_only_escalation(self) -> None:
        decision = self._decide(
            flags=["link_only_invoice"],
            link_only=True,
            has_invoice_attachment=False,
            property_code=None,
            property_name=None,
            service_address=None,
            bill_to=None,
            business_unit_code=None,
            property_lookup={
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
            source_attachments=[],
        )

        self.assertEqual(decision.outcome, "ESCALATE")
        self.assertEqual(decision.matched_rule_code, "hard_link_only_invoice")
        self.assertEqual(decision.destination_code, "ESCALATE_LINK_ONLY")

    def test_explicit_link_only_invoice_with_body_facts_derives_link_only_escalation(self) -> None:
        payload = _payload(
            link_only=True,
            has_invoice_attachment=False,
            invoice_number="17222aa6",
            vendor_name="Republic Services",
            amount=517.42,
            property_code=None,
            property_name=None,
            service_address=None,
            bill_to="Hillwood Properties",
            business_unit_code=None,
            property_lookup={
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
            source_attachments=[],
        )
        payload["observed_facts"]["mentions_payment_link_only"] = True
        extraction = validate_extraction(payload)
        decision = DecisionEngine(InMemoryPolicyRepository(), property_match_reviewer=FakePropertyMatchReviewer()).decide(
            extraction,
            "idempotency-key",
        ).decision

        self.assertIn("link_only_invoice", extraction.document.document_flags)
        self.assertEqual(decision.outcome, "ESCALATE")
        self.assertEqual(decision.matched_rule_code, "hard_link_only_invoice")
        self.assertEqual(decision.destination_code, "ESCALATE_LINK_ONLY")

    def test_unreadable_required_pdf_routes_to_ESCALATE(self) -> None:
        repository = InMemoryPolicyRepository()
        extraction = validate_extraction(_payload())
        decision = DecisionEngine(repository, property_match_reviewer=FakePropertyMatchReviewer()).decide(
            extraction,
            "idempotency-key",
            {"pdf_required_but_unreadable": True, "pdf_text_low_quality": False},
        ).decision
        self.assertEqual(decision.outcome, "ESCALATE")
        self.assertEqual(decision.matched_rule_code, "hard_pdf_required_unreadable")

    def test_low_quality_pdf_text_routes_to_ESCALATE(self) -> None:
        repository = InMemoryPolicyRepository()
        extraction = validate_extraction(_payload())
        decision = DecisionEngine(repository, property_match_reviewer=FakePropertyMatchReviewer()).decide(
            extraction,
            "idempotency-key",
            {"pdf_required_but_unreadable": False, "pdf_text_low_quality": True},
        ).decision
        self.assertEqual(decision.outcome, "ESCALATE")
        self.assertEqual(decision.matched_rule_code, "hard_pdf_text_low_quality")

    def test_contract_routes_to_ESCALATE(self) -> None:
        decision = self._decide(document_type="contract")

        self.assertEqual(decision.outcome, "ESCALATE")
        self.assertEqual(decision.matched_rule_code, "hard_contract_or_pay_app")
        self.assertEqual(decision.destination_code, "ESCALATE_CONTRACT_PAY_APP")

    def test_image_attachment_routes_to_wrong_file_type_escalate(self) -> None:
        decision = self._decide(source_attachments=["invoice.jpg"])

        self.assertEqual(decision.outcome, "ESCALATE")
        self.assertEqual(decision.matched_rule_code, "hard_wrong_file_type")
        self.assertEqual(decision.destination_code, "ESCALATE_WRONG_FILE_TYPE")

    def test_excel_attachment_routes_to_wrong_file_type_escalate(self) -> None:
        decision = self._decide(source_attachments=["invoice.xlsx"])

        self.assertEqual(decision.outcome, "ESCALATE")
        self.assertEqual(decision.matched_rule_code, "hard_wrong_file_type")
        self.assertEqual(decision.destination_code, "ESCALATE_WRONG_FILE_TYPE")

    def test_valid_invoice_evidence_routes_when_unrelated_excel_exists_on_email(self) -> None:
        decision = self._decide(source_attachments=["invoice.pdf"])

        self.assertEqual(decision.outcome, "AUTO")
        self.assertEqual(decision.destination_code, "MEDIUS_PROPERTIES")
        self.assertEqual(decision.matched_rule_code, "property_routing_match")

    def test_invoice_item_with_pdf_and_excel_evidence_escalates_wrong_file_type(self) -> None:
        decision = self._decide(source_attachments=["invoice.pdf", "backup.xlsx"])

        self.assertEqual(decision.outcome, "ESCALATE")
        self.assertEqual(decision.matched_rule_code, "hard_wrong_file_type")
        self.assertEqual(decision.destination_code, "ESCALATE_WRONG_FILE_TYPE")

    def test_ach_notice_with_excel_attachment_files_to_ach_folder(self) -> None:
        decision = self._decide(document_type="ach_notice", source_attachments=["752944_Ezpay Actual.xlsx"])

        self.assertEqual(decision.outcome, "FILE")
        self.assertEqual(decision.destination_code, "FOLDER_ACH")
        self.assertEqual(decision.matched_rule_code, "ach_notice_file")

    def test_check_request_for_medius_property_routes_to_medius(self) -> None:
        decision = self._decide(document_type="check_request", property_code="GW70")

        self.assertEqual(decision.outcome, "AUTO")
        self.assertEqual(decision.destination_code, "MEDIUS_PROPERTIES")
        self.assertEqual(decision.matched_rule_code, "check_request_medius_property")

    def test_check_request_for_non_medius_property_escalates(self) -> None:
        decision = self._decide(document_type="check_request", property_code="EXT1")

        self.assertEqual(decision.outcome, "ESCALATE")
        self.assertEqual(decision.destination_code, "ESCALATE_CHECK_REQUEST")
        self.assertEqual(decision.matched_rule_code, "hard_check_request")

    def test_unmatched_check_request_escalates(self) -> None:
        decision = self._decide(document_type="check_request", property_code="UNKNOWN")

        self.assertEqual(decision.outcome, "ESCALATE")
        self.assertEqual(decision.destination_code, "ESCALATE_CHECK_REQUEST")
        self.assertEqual(decision.matched_rule_code, "hard_check_request")

    def test_duplicate_medius_check_request_routes_to_duplicate_escalation(self) -> None:
        decision = self._decide(document_type="check_request", property_code="GW70", duplicate_status="suspected")

        self.assertEqual(decision.outcome, "ESCALATE")
        self.assertEqual(decision.destination_code, "ESCALATE_DUPLICATE_SUSPECTED")
        self.assertEqual(decision.matched_rule_code, "duplicate_candidate")

    def test_vendor_question_routes_to_vendor_question_escalate_label(self) -> None:
        decision = self._decide(document_type="vendor_question")

        self.assertEqual(decision.outcome, "ESCALATE")
        self.assertEqual(decision.matched_rule_code, "hard_vendor_inquiry")
        self.assertEqual(decision.destination_code, "ESCALATE_VENDOR_QUESTION")

    def test_vendor_inquiry_signal_on_invoice_routes_to_vendor_question_escalate_label(self) -> None:
        decision = self._decide(
            document_type="invoice",
            property_code="UNKNOWN",
            bill_to="Unmapped Building",
            amount=0,
            flags=["vendor_inquiry", "ach_or_auto_draft", "conflicting_signals"],
        )

        self.assertEqual(decision.outcome, "ESCALATE")
        self.assertEqual(decision.matched_rule_code, "hard_vendor_inquiry")
        self.assertEqual(decision.destination_code, "ESCALATE_VENDOR_QUESTION")

    def test_wrong_destination_reply_routes_to_wrong_destination_escalate_label(self) -> None:
        decision = self._decide(
            document_type="unknown",
            property_code=None,
            bill_to=None,
            business_unit_code=None,
            has_invoice_attachment=False,
            source_attachments=[],
            flags=["wrong_destination"],
            evidence_summary="Recipient replied that they are the wrong person and should not have received this email.",
        )

        self.assertEqual(decision.outcome, "ESCALATE")
        self.assertEqual(decision.matched_rule_code, "hard_wrong_destination")
        self.assertEqual(decision.destination_code, "ESCALATE_WRONG_DESTINATION")

    def test_complete_invoice_payment_nudge_routes_to_medius_not_vendor_question(self) -> None:
        decision = self._decide(
            document_type="invoice",
            property_code="HW1",
            flags=["vendor_inquiry"],
            evidence_summary="Attached invoice is due and email asks when payment can be expected.",
        )

        self.assertEqual(decision.outcome, "AUTO")
        self.assertEqual(decision.matched_rule_code, "property_routing_match")
        self.assertEqual(decision.destination_code, "MEDIUS_PROPERTIES")

    def test_past_due_invoice_notice_routes_to_past_due_escalate_label(self) -> None:
        decision = self._decide(document_type="past_due_notice", flags=["past_due"])

        self.assertEqual(decision.outcome, "ESCALATE")
        self.assertEqual(decision.matched_rule_code, "hard_past_due_notice")
        self.assertEqual(decision.destination_code, "ESCALATE_PAST_DUE")

    def test_invoice_with_past_due_signal_routes_to_past_due_escalate_label(self) -> None:
        decision = self._decide(document_type="invoice", flags=["past_due"])

        self.assertEqual(decision.outcome, "ESCALATE")
        self.assertEqual(decision.matched_rule_code, "hard_past_due_notice")
        self.assertEqual(decision.destination_code, "ESCALATE_PAST_DUE")

    def test_payable_upon_receipt_invoice_due_before_received_date_routes_normally(self) -> None:
        decision = self._decide(
            document_type="invoice",
            property_code="HW1",
            received_at="2026-05-22T09:15:00-05:00",
            due_date="2026-05-19",
            amount=1000,
            evidence_summary="Legal invoice has Invoice Date 2026-05-19, CURRENT INVOICE DUE, and THIS INVOICE IS PAYABLE UPON RECEIPT.",
        )

        self.assertEqual(decision.outcome, "AUTO")
        self.assertEqual(decision.matched_rule_code, "property_routing_match")
        self.assertEqual(decision.destination_code, "MEDIUS_PROPERTIES")

    def test_explicit_due_date_before_received_date_routes_to_past_due_escalate_label(self) -> None:
        decision = self._decide(
            document_type="invoice",
            property_code="HW1",
            received_at="2026-05-20T09:15:00-05:00",
            due_date="2026-05-10",
            amount=1000,
            evidence_summary="Invoice shows explicit Due Date: 2026-05-10 and payment due balance.",
        )

        self.assertEqual(decision.outcome, "ESCALATE")
        self.assertEqual(decision.matched_rule_code, "hard_past_due_notice")
        self.assertEqual(decision.destination_code, "ESCALATE_PAST_DUE")

    def test_krcl_payable_upon_receipt_pattern_does_not_escalate_past_due(self) -> None:
        decision = self._decide(
            document_type="invoice",
            property_code="HW1",
            received_at="2026-05-22T09:15:00-05:00",
            due_date="2026-05-19",
            amount=1000,
            vendor_name="KRCL",
            evidence_summary=(
                "Legal invoice shows invoice date 2026-05-19, CURRENT INVOICE DUE, prior balance, "
                "and THIS INVOICE IS PAYABLE UPON RECEIPT, with no explicit current-invoice past-due language."
            ),
        )

        self.assertEqual(decision.outcome, "AUTO")
        self.assertNotEqual(decision.matched_rule_code, "hard_past_due_notice")
        self.assertNotEqual(decision.destination_code, "ESCALATE_PAST_DUE")

    def test_due_on_receipt_invoice_without_calendar_due_date_routes_normally(self) -> None:
        decision = self._decide(
            document_type="invoice",
            property_code="HW1",
            received_at="2026-05-28T09:15:00-05:00",
            invoice_date="2026-05-06",
            due_date=None,
            amount=1300,
            vendor_name="Empire Roofing",
            evidence_summary="Empire Roofing invoice shows Invoice Date: 2026-05-06 and Payment Due: Due On Receipt.",
        )

        self.assertEqual(decision.outcome, "AUTO")
        self.assertEqual(decision.matched_rule_code, "property_routing_match")
        self.assertEqual(decision.destination_code, "MEDIUS_PROPERTIES")
        self.assertNotEqual(decision.destination_code, "ESCALATE_PAST_DUE")

    def test_invoice_with_only_account_aging_past_due_balance_does_not_escalate_past_due(self) -> None:
        decision = self._decide(
            document_type="invoice",
            flags=[],
            evidence_summary=(
                "Run a06116ee-a9c5-4f81-981a-49e92f14fdf4 invoice 3576 amount due "
                "$731.77 is in Current; account aging also shows 1-30 Days Past Due $6,530.41."
            ),
        )

        self.assertNotEqual(decision.matched_rule_code, "hard_past_due_notice")
        self.assertNotEqual(decision.destination_code, "ESCALATE_PAST_DUE")

    def test_high_dollar_properties_invoice_with_project_number_routes_to_medius_properties(self) -> None:
        decision = self._decide(amount=15000, project_number="PRJ-100")

        self.assertEqual(decision.outcome, "AUTO")
        self.assertEqual(decision.destination_code, "MEDIUS_PROPERTIES")
        self.assertEqual(decision.matched_rule_code, "property_routing_match")

    def test_high_dollar_properties_invoice_without_project_number_escalates_over_10000(self) -> None:
        decision = self._decide(amount=15000)

        self.assertEqual(decision.outcome, "ESCALATE")
        self.assertEqual(decision.destination_code, "ESCALATE_OVER_10000")
        self.assertEqual(decision.matched_rule_code, "amount_over_threshold")
        self.assertEqual(decision.routing_match["normal_destination_code"], "MEDIUS_PROPERTIES")

    def test_high_dollar_properties_invoice_with_job_number_only_escalates_over_10000(self) -> None:
        decision = self._decide(amount=15000, job_number="JOB-100")

        self.assertEqual(decision.outcome, "ESCALATE")
        self.assertEqual(decision.destination_code, "ESCALATE_OVER_10000")
        self.assertEqual(decision.matched_rule_code, "amount_over_threshold")

    def test_high_dollar_external_pm_invoice_escalates_over_10000(self) -> None:
        decision = self._decide(property_code="EXT1", amount=15000)

        self.assertEqual(decision.outcome, "ESCALATE")
        self.assertEqual(decision.destination_code, "ESCALATE_OVER_10000")
        self.assertEqual(decision.matched_rule_code, "amount_over_threshold")
        self.assertEqual(decision.routing_match["normal_destination_code"], "TIFFANY_BECK")

    def test_high_dollar_alc_invoice_escalates_to_alc(self) -> None:
        decision = self._decide(business_unit_code="ALC", property_code=None, bill_to="Alliance Landscape Company", amount=15000)

        self.assertEqual(decision.outcome, "ESCALATE")
        self.assertEqual(decision.destination_code, "ESCALATE_ALC")
        self.assertEqual(decision.matched_rule_code, "alc_escalation")

    def test_high_dollar_multifamily_invoice_routes_to_multifamily_escalation(self) -> None:
        decision = self._decide(property_code="MF1", business_unit_code=None, bill_to="Garden Property", amount=15000)

        self.assertEqual(decision.outcome, "ESCALATE")
        self.assertEqual(decision.destination_code, "ESCALATE_MULTIFAMILY")
        self.assertEqual(decision.matched_rule_code, "asset_type_multifamily")

    def test_alliance_landscape_text_evidence_escalates_to_alc(self) -> None:
        decision = self._decide(property_code=None, business_unit_code=None, bill_to="Alliance Landscape Company")

        self.assertEqual(decision.outcome, "ESCALATE")
        self.assertEqual(decision.destination_code, "ESCALATE_ALC")

    def test_alliance_landscaping_text_evidence_overrides_property_address_route(self) -> None:
        decision = self._decide(
            property_code=None,
            business_unit_code=None,
            bill_to="Alliance Landscaping, 9800 Hillwood Pkwy, Ste 300, Fort Worth, TX 76177",
            bill_to_name_line_1="Alliance Landscaping",
            bill_to_street_address="9800 Hillwood Pkwy",
            bill_to_suite="Ste 300",
            bill_to_city="Fort Worth",
            bill_to_state="TX",
            bill_to_zip_code="76177",
            property_lookup={
                "property_code": [],
                "property_name": [],
                "tenant": ["alliance landscaping"],
                "address": ["9800 hillwood parkway", "9800 hillwood parkway fort worth tx 76177"],
                "suite": ["300"],
                "city": ["fort worth"],
                "state": ["tx"],
                "zipcode": ["76177"],
                "address_candidates": [
                    {
                        "rank": 1,
                        "label": "ship_to",
                        "street": "9800 hillwood parkway",
                        "city": "fort worth",
                        "state": "tx",
                        "zipcode": "76177",
                        "normalized_address": "9800 hillwood parkway fort worth tx 76177",
                        "source": "attachment:Inv30009657.pdf:page 1",
                        "confidence": 0.93,
                        "evidence_text": "Ship To: Alliance Landscaping 9800 Hillwood Pkwy, Ste 300 Fort Worth, TX 76177",
                    }
                ],
            },
            possible_property_aliases=["alliance landscaping"],
        )

        self.assertEqual(decision.outcome, "ESCALATE")
        self.assertEqual(decision.destination_code, "ESCALATE_ALC")
        self.assertEqual(decision.matched_rule_code, "alc_escalation")

    def test_alliance_landscaping_with_bill_to_9800_only_escalates_to_alc(self) -> None:
        decision = self._decide(
            property_code=None,
            property_name=None,
            service_address=None,
            business_unit_code=None,
            bill_to="Alliance Landscaping, 9800 Hillwood Pkwy, Ste 300, Fort Worth, TX 76177",
            bill_to_name_line_1="Alliance Landscaping",
            bill_to_street_address="9800 Hillwood Pkwy",
            bill_to_suite="Ste 300",
            bill_to_city="Fort Worth",
            bill_to_state="TX",
            bill_to_zip_code="76177",
            property_lookup={
                "property_code": [],
                "property_name": [],
                "tenant": ["alliance landscaping"],
                "address": ["9800 hillwood parkway", "9800 hillwood parkway fort worth tx 76177"],
                "suite": ["300"],
                "city": ["fort worth"],
                "state": ["tx"],
                "zipcode": ["76177"],
                "address_candidates": [
                    {
                        "rank": 1,
                        "label": "bill_to",
                        "street": "9800 hillwood parkway",
                        "city": "fort worth",
                        "state": "tx",
                        "zipcode": "76177",
                        "normalized_address": "9800 hillwood parkway fort worth tx 76177",
                        "source": "attachment:invoice.pdf:page 1",
                        "confidence": 0.93,
                        "evidence_text": "Bill To: Alliance Landscaping 9800 Hillwood Pkwy, Ste 300 Fort Worth, TX 76177",
                    }
                ],
            },
            possible_property_aliases=["alliance landscaping"],
        )

        self.assertEqual(decision.outcome, "ESCALATE")
        self.assertEqual(decision.destination_code, "ESCALATE_ALC")
        self.assertEqual(decision.matched_rule_code, "alc_escalation")

    def test_alliance_landscaping_with_non_9800_property_address_routes_to_property_destination(self) -> None:
        decision = self._decide(
            property_code=None,
            business_unit_code=None,
            bill_to="Alliance Landscaping",
            service_address="2451 Westlake Parkway",
            property_lookup={
                "property_code": [],
                "property_name": [],
                "tenant": ["alliance landscaping"],
                "address": ["2451 westlake parkway"],
                "suite": [],
                "city": ["westlake"],
                "state": ["tx"],
                "zipcode": ["76262"],
                "address_candidates": [
                    {
                        "rank": 1,
                        "label": "service_location",
                        "street": "2451 westlake parkway",
                        "city": "westlake",
                        "state": "tx",
                        "zipcode": "76262",
                        "normalized_address": "2451 westlake parkway westlake tx 76262",
                        "source": "attachment:invoice.pdf:page 1",
                        "confidence": 0.93,
                        "evidence_text": "Service Address: 2451 Westlake Parkway Westlake TX 76262",
                    }
                ],
            },
            possible_property_aliases=["alliance landscaping"],
        )

        self.assertEqual(decision.outcome, "AUTO")
        self.assertEqual(decision.destination_code, "TIFFANY_BECK")
        self.assertEqual(decision.matched_rule_code, "property_routing_match")

    def test_alliance_landscape_with_gateway9_service_address_and_9800_bill_to_routes_to_property_destination(self) -> None:
        decision = self._decide(
            subject="Alliance Landscape invoice",
            vendor_name="Alliance Landscape",
            property_code="GW9",
            property_name=None,
            business_unit_code=None,
            bill_to="Hillwood Alliance Group, 9800 Hillwood Parkway, Fort Worth TX 76177",
            bill_to_street_address="9800 Hillwood Parkway",
            bill_to_city="Fort Worth",
            bill_to_state="TX",
            bill_to_zip_code="76177",
            service_address="5300 Alliance Gateway Freeway",
            property_lookup={
                "property_code": ["gw9"],
                "property_name": [],
                "tenant": [],
                "address": ["5300 alliance gateway freeway", "9800 hillwood parkway"],
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
                        "source": "attachment:invoice.pdf:page 1",
                        "confidence": 0.95,
                        "evidence_text": "Service Location: 5300 Alliance Gateway Freeway Fort Worth TX 76177",
                    },
                    {
                        "rank": 2,
                        "label": "bill_to",
                        "street": "9800 hillwood parkway",
                        "city": "fort worth",
                        "state": "tx",
                        "zipcode": "76177",
                        "normalized_address": "9800 hillwood parkway fort worth tx 76177",
                        "source": "attachment:invoice.pdf:page 1",
                        "confidence": 0.90,
                        "evidence_text": "Bill To: Hillwood Alliance Group 9800 Hillwood Parkway",
                    },
                ],
            },
        )

        self.assertEqual(decision.outcome, "AUTO")
        self.assertEqual(decision.destination_code, "TIFFANY_BECK")
        self.assertEqual(decision.matched_rule_code, "property_routing_match")
        self.assertEqual(decision.routing_match["property_match"]["property_code"], "GW9")

    def test_standalone_alc_text_evidence_escalates_to_alc(self) -> None:
        decision = self._decide(property_code=None, business_unit_code=None, bill_to="ALC")

        self.assertEqual(decision.outcome, "ESCALATE")
        self.assertEqual(decision.destination_code, "ESCALATE_ALC")

    def test_multifamily_text_evidence_does_not_route_to_alc_escalation(self) -> None:
        decision = self._decide(property_code=None, business_unit_code=None, evidence_summary="Invoice for Multifamily property service.")

        self.assertEqual(decision.outcome, "ESCALATE")
        self.assertEqual(decision.destination_code, "ESCALATE_GENERAL")
        self.assertNotEqual(decision.matched_rule_code, "alc_escalation")

    def test_non_standalone_alc_text_does_not_match_alc(self) -> None:
        decision = self._decide(property_code=None, business_unit_code=None, bill_to="Malcolm Services")

        self.assertNotEqual(decision.matched_rule_code, "alc_escalation")
        self.assertEqual(decision.destination_code, "ESCALATE_UNMATCHED_BUILDING")

    def test_suspected_duplicate_invoice_routes_to_ESCALATE(self) -> None:
        decision = self._decide(duplicate_status="suspected")

        self.assertEqual(decision.outcome, "ESCALATE")
        self.assertEqual(decision.matched_rule_code, "duplicate_candidate")
        self.assertEqual(decision.destination_code, "ESCALATE_DUPLICATE_SUSPECTED")

    def test_statement_files_to_statement_folder(self) -> None:
        decision = self._decide(document_type="statement")

        self.assertEqual(decision.outcome, "FILE")
        self.assertEqual(decision.destination_code, "FOLDER_STATEMENTS")

    def test_ach_notice_files_to_ach_folder(self) -> None:
        decision = self._decide(document_type="ach_notice")

        self.assertEqual(decision.outcome, "FILE")
        self.assertEqual(decision.destination_code, "FOLDER_ACH")

    def test_ach_notice_ignores_multi_invoice_text_signal_and_files(self) -> None:
        decision = self._decide(document_type="ach_notice", flags=["multi_invoice_pdf"], multi_invoice=True)

        self.assertEqual(decision.outcome, "FILE")
        self.assertEqual(decision.destination_code, "FOLDER_ACH")
        self.assertEqual(decision.matched_rule_code, "ach_notice_file")

    def test_auto_draft_notice_link_without_attachment_files(self) -> None:
        decision = self._decide(document_type="auto_draft_notice", link_only=True, has_invoice_attachment=False)

        self.assertEqual(decision.outcome, "FILE")
        self.assertEqual(decision.destination_code, "FOLDER_ACH")
        self.assertEqual(decision.matched_rule_code, "ach_notice_file")

    def test_account_summary_link_without_attachment_routes_to_ESCALATE(self) -> None:
        decision = self._decide(document_type="account_summary", link_only=True, has_invoice_attachment=False)

        self.assertEqual(decision.outcome, "ESCALATE")
        self.assertEqual(decision.matched_rule_code, "hard_link_only_invoice")

    def test_multi_invoice_signal_with_multiple_attachments_uses_amount_threshold(self) -> None:
        decision = self._decide(
            flags=["multi_invoice_pdf"],
            multi_invoice=True,
            amount=15000,
            project_number="PRJ-100",
            source_attachments=["a.pdf", "b.pdf"],
        )

        self.assertEqual(decision.outcome, "AUTO")
        self.assertEqual(decision.destination_code, "MEDIUS_PROPERTIES")
        self.assertEqual(decision.matched_rule_code, "property_routing_match")

    def test_ben_e_keith_notice_files_to_ben_e_keith_folder(self) -> None:
        decision = self._decide(document_type="ben_e_keith_notice")

        self.assertEqual(decision.outcome, "FILE")
        self.assertEqual(decision.destination_code, "FOLDER_BEN_E_KEITH")
        self.assertEqual(decision.matched_rule_code, "ben_e_keith_notice_file")

    def test_ben_e_keith_notice_with_excel_attachment_files_to_ben_e_keith_folder(self) -> None:
        decision = self._decide(document_type="ben_e_keith_notice", source_attachments=["752944_Ezpay Actual.xlsx"])

        self.assertEqual(decision.outcome, "FILE")
        self.assertEqual(decision.destination_code, "FOLDER_BEN_E_KEITH")
        self.assertEqual(decision.matched_rule_code, "ben_e_keith_notice_file")

    def test_ben_e_keith_invoice_with_txt_attachment_files_to_ben_e_keith_folder(self) -> None:
        decision = self._decide(document_type="invoice", flags=["ben_e_keith"], source_attachments=["cleointegration.txt"])

        self.assertEqual(decision.outcome, "FILE")
        self.assertEqual(decision.destination_code, "FOLDER_BEN_E_KEITH")
        self.assertEqual(decision.matched_rule_code, "ben_e_keith_notice_file")

    def test_ben_e_keith_invoice_with_unsupported_evidence_files_to_ben_e_keith_folder(self) -> None:
        decision = self._decide(document_type="invoice", flags=["ben_e_keith"], source_attachments=["invoice.pdf", "backup.xlsx"])

        self.assertEqual(decision.outcome, "FILE")
        self.assertEqual(decision.destination_code, "FOLDER_BEN_E_KEITH")
        self.assertEqual(decision.matched_rule_code, "ben_e_keith_notice_file")

    def test_ben_e_keith_notice_with_unreadable_pdf_files_to_ben_e_keith_folder(self) -> None:
        repository = InMemoryPolicyRepository()
        extraction = validate_extraction(_payload(document_type="ben_e_keith_notice"))
        decision = DecisionEngine(repository, property_match_reviewer=FakePropertyMatchReviewer()).decide(
            extraction,
            "idempotency-key",
            {"pdf_required_but_unreadable": True, "pdf_text_low_quality": False},
        ).decision

        self.assertEqual(decision.outcome, "FILE")
        self.assertEqual(decision.destination_code, "FOLDER_BEN_E_KEITH")
        self.assertEqual(decision.matched_rule_code, "ben_e_keith_notice_file")

    def test_ben_e_keith_associated_invoice_files_to_ben_e_keith_folder(self) -> None:
        decision = self._decide(document_type="invoice", flags=["ben_e_keith"])

        self.assertEqual(decision.outcome, "FILE")
        self.assertEqual(decision.destination_code, "FOLDER_BEN_E_KEITH")
        self.assertEqual(decision.matched_rule_code, "ben_e_keith_notice_file")

    def test_unknown_building_routes_to_ESCALATE(self) -> None:
        decision = self._decide(property_code="UNKNOWN")

        self.assertEqual(decision.outcome, "ESCALATE")
        self.assertEqual(decision.matched_rule_code, "hard_unmatched_building")
        self.assertEqual(decision.destination_code, "ESCALATE_UNMATCHED_BUILDING")

    def test_automated_non_ap_digest_routes_to_no_action(self) -> None:
        decision = self._decide(
            document_type="unknown",
            subject="Spam 4U: 1 New Message",
            sender_email="noreply-digest@hillwood.com",
            evidence_summary="Proofpoint end-user digest notification with quarantine links.",
            vendor_name=None,
            property_code=None,
            bill_to=None,
            business_unit_code=None,
        )

        self.assertEqual(decision.outcome, "DISCARD")
        self.assertEqual(decision.destination_code, "NO_ACTION")
        self.assertEqual(decision.matched_rule_code, "hard_no_action_email_pattern")

    def test_automated_non_ap_digest_with_logo_attachment_routes_to_no_action(self) -> None:
        decision = self._decide(
            document_type="unknown",
            subject="Spam 4U: 1 New Message",
            sender_email="noreply-digest@hillwood.com",
            evidence_summary="Proofpoint end-user digest notification with quarantine links.",
            source_attachments=["logo.png"],
            vendor_name=None,
            property_code=None,
            bill_to=None,
            business_unit_code=None,
        )

        self.assertEqual(decision.outcome, "DISCARD")
        self.assertEqual(decision.destination_code, "NO_ACTION")
        self.assertEqual(decision.matched_rule_code, "hard_no_action_email_pattern")

    def test_current_reply_llm_no_action_fact_routes_to_no_action(self) -> None:
        payload = _payload(
            document_type="unknown",
            sender_email="katie@hillwood.com",
            vendor_name=None,
            property_code=None,
            bill_to=None,
            business_unit_code=None,
            has_invoice_attachment=False,
            amount=0,
            source_attachments=[],
            flags=["latest_reply_no_action"],
            evidence_summary="Latest reply says the recipient received the email and will process it for payment.",
        )
        extraction = validate_extraction(payload)

        decision = DecisionEngine(InMemoryPolicyRepository(), property_match_reviewer=FakePropertyMatchReviewer()).decide(
            extraction,
            "idempotency-key",
            decision_context=DecisionContext(
                latest_body_text="Received - will process for payment.",
                quoted_history_text="Prior invoice content.",
                has_quoted_history=True,
            ),
        ).decision

        self.assertEqual(decision.outcome, "DISCARD")
        self.assertEqual(decision.destination_code, "NO_ACTION")
        self.assertEqual(decision.matched_rule_code, "hard_current_reply_no_action")
        self.assertEqual(decision.routing_match["decision_context"]["latest_body_text"], "Received - will process for payment.")

    def test_current_reply_no_action_fact_can_override_llm_invoice_type_guess(self) -> None:
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
        extraction = validate_extraction(payload)

        decision = DecisionEngine(InMemoryPolicyRepository(), property_match_reviewer=FakePropertyMatchReviewer()).decide(
            extraction,
            "idempotency-key",
            decision_context=DecisionContext(
                latest_body_text="Received - will process for payment.",
                quoted_history_text="Prior invoice content.",
                has_quoted_history=True,
            ),
        ).decision

        self.assertEqual(decision.outcome, "DISCARD")
        self.assertEqual(decision.destination_code, "NO_ACTION")
        self.assertEqual(decision.matched_rule_code, "hard_current_reply_no_action")

    def test_current_reply_no_action_rejects_current_attachments(self) -> None:
        decision = self._decide(
            document_type="unknown",
            sender_email="katie@hillwood.com",
            vendor_name=None,
            property_code=None,
            bill_to=None,
            business_unit_code=None,
            has_invoice_attachment=False,
            amount=0,
            source_attachments=["invoice.pdf"],
            flags=["latest_reply_no_action"],
        )

        self.assertNotEqual(decision.matched_rule_code, "hard_current_reply_no_action")

    def test_quoted_vendor_question_without_latest_no_action_fact_still_escalates(self) -> None:
        payload = _payload(
            document_type="vendor_question",
            sender_email="jennifer@hillwood.com",
            vendor_name=None,
            property_code=None,
            bill_to=None,
            business_unit_code=None,
            has_invoice_attachment=False,
            amount=0,
            source_attachments=[],
        )
        extraction = validate_extraction(payload)

        decision = DecisionEngine(InMemoryPolicyRepository(), property_match_reviewer=FakePropertyMatchReviewer()).decide(
            extraction,
            "idempotency-key",
            decision_context=DecisionContext(
                latest_body_text="Thank you!",
                quoted_history_text="Prior vendor asked when invoice would be paid.",
                has_quoted_history=True,
            ),
        ).decision

        self.assertEqual(decision.outcome, "ESCALATE")
        self.assertEqual(decision.matched_rule_code, "hard_vendor_inquiry")

    def test_appointment_informational_notice_routes_to_no_action(self) -> None:
        decision = self._decide(
            document_type="unknown",
            subject="Upcoming Service Appointment on Wednesday, May 13th",
            vendor_name="Forterra Pest Control",
            property_code=None,
            property_name=None,
            service_address="9800 Hillwood Parkway STE 300",
            bill_to="Hillwood Properties",
            business_unit_code=None,
            requires_attachment=False,
            has_invoice_attachment=False,
            amount=0,
            source_attachments=[],
            flags=["informational_appointment_notice"],
            evidence_summary="Service appointment reminder for the matched property address.",
        )

        self.assertEqual(decision.outcome, "DISCARD")
        self.assertEqual(decision.destination_code, "NO_ACTION")
        self.assertEqual(decision.matched_rule_code, "appointment_informational_notice")

    def test_appointment_informational_notice_routes_16501_victory_circle_to_no_action(self) -> None:
        decision = self._decide(
            document_type="unknown",
            subject="Service appointment reminder",
            vendor_name="Service Vendor",
            property_code=None,
            property_name=None,
            service_address="16501 Victory Circle",
            bill_to=None,
            business_unit_code=None,
            requires_attachment=False,
            has_invoice_attachment=False,
            amount=0,
            source_attachments=[],
            flags=["informational_appointment_notice"],
            evidence_summary="Service appointment reminder for 16501 Victory Circle.",
        )

        self.assertEqual(decision.outcome, "DISCARD")
        self.assertEqual(decision.destination_code, "NO_ACTION")
        self.assertEqual(decision.matched_rule_code, "appointment_informational_notice")

    def test_appointment_informational_notice_requires_llm_fact(self) -> None:
        decision = self._decide(
            document_type="unknown",
            subject="Service appointment reminder",
            vendor_name="Service Vendor",
            property_code=None,
            property_name=None,
            service_address="16501 Victory Circle",
            bill_to=None,
            business_unit_code=None,
            has_invoice_attachment=False,
            amount=0,
            source_attachments=[],
            evidence_summary="Service appointment reminder for 16501 Victory Circle.",
        )

        self.assertEqual(decision.outcome, "ESCALATE")
        self.assertEqual(decision.destination_code, "ESCALATE_UNMATCHED_BUILDING")
        self.assertEqual(decision.matched_rule_code, "hard_unmatched_building")

    def test_informational_property_notice_routes_to_property_destination_when_not_appointment(self) -> None:
        decision = self._decide(
            document_type="unknown",
            subject="Property access notice",
            vendor_name="Service Vendor",
            property_code=None,
            property_name=None,
            service_address="9800 Hillwood Parkway STE 300",
            bill_to="Hillwood Properties",
            business_unit_code=None,
            has_invoice_attachment=False,
            amount=0,
            source_attachments=[],
            evidence_summary="Informational property access notice for the matched property address.",
        )

        self.assertEqual(decision.outcome, "AUTO")
        self.assertEqual(decision.destination_code, "MEDIUS_PROPERTIES")
        self.assertEqual(decision.matched_rule_code, "informational_property_notice")

    def test_informational_property_notice_does_not_override_link_only_invoice(self) -> None:
        decision = self._decide(
            document_type="unknown",
            property_code=None,
            service_address="9800 Hillwood Parkway STE 300",
            has_invoice_attachment=False,
            link_only=True,
            source_attachments=[],
        )

        self.assertEqual(decision.outcome, "ESCALATE")
        self.assertEqual(decision.matched_rule_code, "hard_link_only_invoice")

    def test_informational_property_notice_without_property_match_escalates(self) -> None:
        decision = self._decide(
            document_type="unknown",
            property_code=None,
            property_name=None,
            service_address="1 Unknown Plaza",
            bill_to=None,
            business_unit_code=None,
            has_invoice_attachment=False,
            amount=0,
            source_attachments=[],
        )

        self.assertEqual(decision.outcome, "ESCALATE")
        self.assertNotEqual(decision.matched_rule_code, "informational_property_notice")

    def test_informational_property_notice_with_vendor_inquiry_stays_escalate(self) -> None:
        decision = self._decide(
            document_type="unknown",
            property_code=None,
            service_address="9800 Hillwood Parkway STE 300",
            has_invoice_attachment=False,
            amount=0,
            source_attachments=[],
            flags=["vendor_inquiry"],
        )

        self.assertEqual(decision.outcome, "ESCALATE")
        self.assertEqual(decision.matched_rule_code, "hard_vendor_inquiry")

    def test_informational_property_notice_does_not_override_past_due_notice(self) -> None:
        decision = self._decide(
            document_type="past_due_notice",
            property_code=None,
            service_address="9800 Hillwood Parkway STE 300",
            has_invoice_attachment=False,
            amount=0,
            source_attachments=[],
        )

        self.assertEqual(decision.outcome, "ESCALATE")
        self.assertEqual(decision.matched_rule_code, "hard_past_due_notice")

    def test_low_confidence_does_not_force_ESCALATE(self) -> None:
        decision = self._decide(confidence=0.50)

        self.assertEqual(decision.outcome, "AUTO")
        self.assertEqual(decision.destination_code, "MEDIUS_PROPERTIES")
        self.assertEqual(decision.matched_rule_code, "property_routing_match")

    def test_missing_automatic_route_fields_routes_to_ESCALATE(self) -> None:
        decision = self._decide(vendor_name=None, property_code=None, bill_to=None, business_unit_code=None)

        self.assertEqual(decision.outcome, "ESCALATE")
        self.assertIn("Missing required automatic-routing fields", decision.reason)

    def test_missing_required_workflow_config_raises(self) -> None:
        repository = InMemoryPolicyRepository()
        repository.config.pop("confidence_threshold")
        engine = DecisionEngine(repository)

        with self.assertRaises(MissingWorkflowConfigError):
            engine.decide(validate_extraction(_payload()), "key")

    def test_aggregation_only_rules_are_not_evaluated_per_item(self) -> None:
        repository = InMemoryPolicyRepository()
        repository.extra_rules.append(
            _rule(
                "hard_mixed_item_destinations",
                148,
                "aggregation_mixed_destinations",
                "ESCALATE",
                "ESCALATE_GENERAL",
                {"aggregation_reason": "mixed_item_destinations"},
            )
        )

        decision = DecisionEngine(repository, property_match_reviewer=FakePropertyMatchReviewer()).decide(validate_extraction(_payload()), "key").decision

        self.assertEqual(decision.outcome, "AUTO")
        self.assertEqual(decision.matched_rule_code, "property_routing_match")

    def test_property_name_only_match_routes_auto(self) -> None:
        decision = self._decide(property_code=None, property_name="Hillwood One", bill_to=None)
        self.assertEqual(decision.outcome, "AUTO")

    def test_address_only_match_routes_auto(self) -> None:
        decision = self._decide(property_code=None, property_name=None, service_address="9800 Hillwood Parkway STE 300")
        self.assertEqual(decision.outcome, "AUTO")

    def test_property_candidates_require_llm_final_review(self) -> None:
        repository = InMemoryPolicyRepository()
        decision = DecisionEngine(repository).decide(validate_extraction(_payload()), "key").decision

        self.assertEqual(decision.outcome, "ESCALATE")
        self.assertTrue(decision.routing_match["property_llm_advisory"]["required"])
        self.assertEqual(decision.routing_match["property_gate"]["reason"], "LLM final property review required before property routing")

    def test_llm_final_review_selects_correct_same_address_candidate_by_name(self) -> None:
        repository = InMemoryPolicyRepository()
        repository.properties["HC3"] = PropertyMatch("asset-HC3", "HC3", "Heritage Commons III", "hillwood_owned", "Hillwood", "industrial", "PROP", "MEDIUS_PROPERTIES")
        reviewer = FakePropertyMatchReviewer(selected_asset_id="asset-HC2")

        extraction = validate_extraction(_payload(property_code=None, property_name="Heritage Commons II", service_address="13601 N Fwy"))
        decision = DecisionEngine(repository, property_match_reviewer=reviewer).decide(extraction, "key").decision

        self.assertEqual(decision.outcome, "AUTO")
        self.assertEqual(decision.destination_code, "MICHELE_FELLERS")
        self.assertEqual(decision.routing_match["property_gate"]["llm_selected_asset_id"], "asset-HC2")

    def test_project_property_name_disambiguates_shared_address_candidates(self) -> None:
        repository = InMemoryPolicyRepository()
        repository.properties["CTR"] = PropertyMatch("asset-CTR", "CTR", "Circle T Ranch", "hillwood_owned", "Hillwood", "industrial", "PROP", "MEDIUS_PROPERTIES")
        repository.properties["CTG"] = PropertyMatch("asset-CTG", "CTG", "Circle T Golf Course", "hillwood_owned", "Hillwood", "industrial", "PROP", "MEDIUS_PROPERTIES")
        reviewer = FakePropertyMatchReviewer(selected_asset_id="asset-CTR")
        extraction = validate_extraction(
            _payload(
                property_code=None,
                property_name=None,
                service_address="2451 Westlake Parkway, Westlake, TX",
                property_lookup={
                    "property_code": ["ctr"],
                    "property_name": ["circle t ranch"],
                    "tenant": [],
                    "address": ["2451 westlake parkway", "2451 westlake parkway westlake tx"],
                    "suite": [],
                    "city": ["westlake"],
                    "state": ["tx"],
                    "zipcode": [],
                    "address_candidates": [
                        {
                            "rank": 1,
                            "label": "bill_to",
                            "street": "2451 westlake parkway",
                            "city": "westlake",
                            "state": "tx",
                            "zipcode": None,
                            "normalized_address": "2451 westlake parkway westlake tx",
                            "source": "attachment:invoice.pdf:page 1",
                            "confidence": 0.86,
                            "evidence_text": "Bill To 2451 Westlake Parkway Westlake, Tx",
                        }
                    ],
                },
                evidence_summary="Project Circle T Ranch",
            )
        )

        decision = DecisionEngine(repository, property_match_reviewer=reviewer).decide(extraction, "key").decision

        self.assertEqual(decision.outcome, "AUTO")
        self.assertEqual(decision.destination_code, "MEDIUS_PROPERTIES")
        self.assertEqual(decision.routing_match["property_match"]["property_code"], "CTR")

    def test_tenant_assisted_match_routes_auto(self) -> None:
        decision = self._decide(property_code=None, property_name=None, bill_to="Nuveen")
        self.assertEqual(decision.outcome, "AUTO")

    def test_suite_city_state_zip_assisted_match_routes_auto(self) -> None:
        decision = self._decide(property_code=None, property_name=None, bill_to=None, service_address="STE 300 FORT WORTH TX 76177")
        self.assertEqual(decision.outcome, "AUTO")

    def test_low_score_property_match_routes_ESCALATE(self) -> None:
        decision = self._decide(property_code=None, property_name="unknown weak signal", bill_to=None, service_address=None)
        self.assertEqual(decision.outcome, "ESCALATE")

    def test_near_tie_property_match_routes_auto_when_top_score_passes(self) -> None:
        decision = self._decide(property_code=None, property_name="Duplicate Property", bill_to=None, service_address=None)
        self.assertEqual(decision.outcome, "AUTO")
        self.assertEqual(decision.destination_code, "MICHELE_FELLERS")
        self.assertFalse(decision.routing_match["property_gate"]["pass_margin"])
        self.assertTrue(decision.routing_match["property_gate"]["passed"])

    def test_routing_match_audit_contains_signals_candidates_scores_gate(self) -> None:
        decision = self._decide(property_code=None, property_name="Hillwood One", service_address="9800 Hillwood Parkway")
        routing_match = decision.routing_match
        self.assertIn("property_standardized_signals", routing_match)
        self.assertIn("raw_input_signals", routing_match["property_standardized_signals"])
        self.assertIn("standardized_query_values", routing_match["property_standardized_signals"])
        self.assertGreaterEqual(len(routing_match["property_candidates"]), 1)
        self.assertIn("similarity_score", routing_match["property_candidates"][0])
        self.assertIn("reason", routing_match["property_gate"])

    def _decide(self, **overrides: Any):
        repository = InMemoryPolicyRepository(duplicate_status=overrides.pop("duplicate_status", None))
        extraction = validate_extraction(_payload(**overrides))
        return DecisionEngine(repository, property_match_reviewer=FakePropertyMatchReviewer()).decide(extraction, "idempotency-key").decision


class InMemoryPolicyRepository(PolicyRepository):
    def __init__(self, duplicate_status: str | None = None) -> None:
        self.config: dict[str, Any] = {
            "confidence_threshold": 0.90,
            "amount_review_threshold": 10000,
            "default_escalate_destination": "ESCALATE_GENERAL",
        }
        self.duplicate_status = duplicate_status
        self.destinations = {
            "MEDIUS_PROPERTIES": Destination("MEDIUS_PROPERTIES", "Medius PROP", "medius.prop@example.com", "MEDIUS_PROPERTIES", None, send_email=True),
            "MEDIUS_ALC": Destination("MEDIUS_ALC", "Medius ALC", "medius.alc@example.com", "MEDIUS_ALC", None, send_email=True),
            "MEDIUS_MF": Destination("MEDIUS_MF", "Medius MF", "medius.mf@example.com", "MEDIUS_MF", None, send_email=True),
            "TIFFANY_BECK": Destination("TIFFANY_BECK", "Tiffany Beck", "tiffany@example.com", "TIFFANY_BECK", None, send_email=True),
            "MICHELE_FELLERS": Destination("MICHELE_FELLERS", "Michele Fellers", "michele@example.com", "MICHELE_FELLERS", None, send_email=True),
            "FOLDER_STATEMENTS": Destination("FOLDER_STATEMENTS", "ESCALATE Statement", None, "FOLDER_STATEMENTS", None),
            "FOLDER_ACH": Destination("FOLDER_ACH", "ACH", None, "FOLDER_ACH", None),
            "FOLDER_BEN_E_KEITH": Destination("FOLDER_BEN_E_KEITH", "Ben E Keith", None, "FOLDER_BEN_E_KEITH", None),
            "FOLDER_LIEN_RELEASE": Destination("FOLDER_LIEN_RELEASE", "Lien Release", None, "FOLDER_LIEN_RELEASE", None),
            "ESCALATE_OVER_10000": Destination("ESCALATE_OVER_10000", "OVER-10000", None, "ESCALATE", "Over 10000"),
            "ESCALATE_MULTI_INVOICE_PDF": Destination("ESCALATE_MULTI_INVOICE_PDF", "MULTI-INVOICE-PDF", None, "ESCALATE", "Multi PDF Invoice"),
            "ESCALATE_MULTI_PDF_MERGE": Destination("ESCALATE_MULTI_PDF_MERGE", "MULTI-PDF-MERGE", None, "ESCALATE", "Multi PDF Merge"),
            "ESCALATE_LIEN_WAIVER": Destination("ESCALATE_LIEN_WAIVER", "LIEN-WAIVER", None, "ESCALATE", "Lien Waiver"),
            "ESCALATE_LINK_ONLY": Destination("ESCALATE_LINK_ONLY", "LINK-ONLY", None, "ESCALATE", "Link Only", send_teams_message=True),
            "ESCALATE_WRONG_FILE_TYPE": Destination("ESCALATE_WRONG_FILE_TYPE", "WRONG-FILE-TYPE", None, "ESCALATE", "Wrong File Type"),
            "ESCALATE_CONTRACT_PAY_APP": Destination("ESCALATE_CONTRACT_PAY_APP", "CONTRACT-PAY-APP", None, "ESCALATE", "Contract Pay App"),
            "ESCALATE_VENDOR_QUESTION": Destination("ESCALATE_VENDOR_QUESTION", "VENDOR-QUESTION", None, "ESCALATE", "Vendor Question"),
            "ESCALATE_WRONG_DESTINATION": Destination("ESCALATE_WRONG_DESTINATION", "WRONG-DESTINATION", None, "ESCALATE", "Wrong Destination"),
            "ESCALATE_PAST_DUE": Destination("ESCALATE_PAST_DUE", "PAST-DUE", None, "ESCALATE", "Past Due", send_teams_message=True),
            "ESCALATE_DUPLICATE_SUSPECTED": Destination("ESCALATE_DUPLICATE_SUSPECTED", "DUPLICATE-SUSPECTED", None, "ESCALATE", "Duplicate Suspected"),
            "ESCALATE_ALC": Destination("ESCALATE_ALC", "ALC", None, "ESCALATE", "ALC"),
            "ESCALATE_SPLIT_MULTI_PDF": Destination("ESCALATE_SPLIT_MULTI_PDF", "SPLIT-MULTI-PDF", None, "ESCALATE", "SPLIT-MULTI-PDF"),
            "ESCALATE_MULTIFAMILY": Destination("ESCALATE_MULTIFAMILY", "MULTIFAMILY", None, "ESCALATE", "MULTIFAMILY"),
            "ESCALATE_UNMATCHED_BUILDING": Destination("ESCALATE_UNMATCHED_BUILDING", "UNMATCHED-BUILDING", None, "ESCALATE", "Unmatched Building"),
            "ESCALATE_CHECK_REQUEST": Destination("ESCALATE_CHECK_REQUEST", "CHECK-REQUEST", None, "ESCALATE", "Check Request"),
            "ESCALATE_GENERAL": Destination("ESCALATE_GENERAL", "Escalate General", None, "ESCALATE", "General"),
            "NO_ACTION": Destination("NO_ACTION", "No Action", None, "NO_ACTION", None),
        }
        self.extra_rules: list[WorkflowRule] = []
        self.properties = {
            "HW1": PropertyMatch("asset-HW1", "HW1", "Hillwood One", "hillwood_owned", "hillwood_owned", "industrial", "PROP", "MEDIUS_PROPERTIES"),
            "HWC1": PropertyMatch("asset-HWC1", "HWC1", "Hillwood Commons I", "hillwood_owned", "Hillwood", "industrial", "PROP", "MEDIUS_PROPERTIES"),
            "EXT1": PropertyMatch("asset-EXT1", "EXT1", "External One", "investor_managed", "investor_managed", "industrial", "PROP", "TIFFANY_BECK"),
            "HC2": PropertyMatch("asset-HC2", "HC2", "HC2 Property", "investor_managed", "investor_managed", "industrial", "PROP", "MICHELE_FELLERS"),
            "MF1": PropertyMatch("asset-MF1", "MF1", "Multifamily One", "hillwood_owned", "Hillwood", "Multifamily", None, "MEDIUS_PROPERTIES"),
            "GW70": PropertyMatch("asset-GW70", "GW70", "Alliance Gateway 70", "hillwood_owned", "Hillwood", "industrial", "PROP", "MEDIUS_PROPERTIES"),
            "DUP_CODE": PropertyMatch("asset-DUP_CODE", "DUP_CODE", "Duplicate Property A", "investor_managed", "investor_managed", "industrial", "PROP", "MICHELE_FELLERS"),
            "DUP_CODE_B": PropertyMatch("asset-DUP_CODE_B", "DUP_CODE_B", "Duplicate Property B", "investor_managed", "investor_managed", "industrial", "PROP", "TIFFANY_BECK"),
            "WEST1": PropertyMatch("asset-WEST1", "WEST1", "Westlake One", "investor_managed", "investor_managed", "industrial", "PROP", "TIFFANY_BECK"),
            "GW9": PropertyMatch("asset-GW9", "GW9", "Gateway 9", "investor_managed", "investor_managed", "industrial", "PROP", "TIFFANY_BECK"),
        }

    def get_runtime_config(self) -> dict[str, Any]:
        return self.config

    def get_active_workflow_rules(self) -> list[WorkflowRule]:
        return [*_rules(), *self.extra_rules]

    def get_destination(self, destination_code: str) -> Destination:
        return self.destinations[destination_code]

    def evaluate_property_match(self, extraction):
        raw_input = {
            "property_code": extraction.invoice.property_code,
            "property_name": extraction.invoice.property_name,
            "service_address": extraction.invoice.service_address,
            "bill_to": extraction.invoice.bill_to,
            "bill_to_components": {
                "name_line_1": extraction.invoice.bill_to_name_line_1,
                "name_line_2": extraction.invoice.bill_to_name_line_2,
                "street_address": extraction.invoice.bill_to_street_address,
                "suite": extraction.invoice.bill_to_suite,
                "city": extraction.invoice.bill_to_city,
                "state": extraction.invoice.bill_to_state,
                "zip_code": extraction.invoice.bill_to_zip_code,
            },
            "possible_property_aliases": list(extraction.business_signals.possible_property_aliases),
        }
        values = [
            *extraction.property_lookup.property_code,
            *extraction.property_lookup.property_name,
            *extraction.property_lookup.tenant,
            *extraction.property_lookup.address,
            *extraction.property_lookup.city,
            *extraction.property_lookup.state,
            *extraction.property_lookup.zipcode,
            extraction.invoice.property_code,
            extraction.invoice.property_name,
            extraction.invoice.service_address,
            extraction.invoice.bill_to_name_line_1,
            extraction.invoice.bill_to_name_line_2,
            extraction.invoice.bill_to_street_address,
            extraction.invoice.bill_to_suite,
            extraction.invoice.bill_to_city,
            extraction.invoice.bill_to_state,
            extraction.invoice.bill_to_zip_code,
            extraction.invoice.bill_to,
            *extraction.business_signals.possible_property_aliases,
        ]
        normalized_values = [_normalize_property_value(value) for value in values if value]
        property_code_query = _normalize_property_value(extraction.invoice.property_code) if extraction.invoice.property_code else ""
        candidates: list[PropertyMatchCandidate] = []
        for code, match in self.properties.items():
            searchable = [
                code,
                match.property_name or "",
                "Nuveen" if code == "EXT1" else "Hillwood Properties",
                "9800 Hillwood Parkway STE 300" if code in {"HW1", "HW1B"} else ("13601 N Fwy" if code in {"HC2", "HC3"} else ("9700 Hillwood Parkway STE 200" if code == "HC2" else ("2451 Westlake Parkway" if code in {"WEST1", "CTR", "CTG"} else ("5300 Alliance Gateway Freeway" if code == "GW9" else "100 External Blvd")))),
                "STE 300" if code == "HW1" else ("STE 200" if code == "HC2" else ""),
                "Fort Worth" if code in {"HW1", "GW9"} else ("Arlington" if code == "HC2" else ("Westlake" if code in {"WEST1", "CTR", "CTG"} else "Irving")),
                "TX",
                "76177" if code == "HW1" else ("76176" if code == "HC2" else ("76262" if code in {"WEST1", "CTR", "CTG"} else "76155")),
            ]
            score = 0.0
            matched_text = ""
            for query_index, query in enumerate(normalized_values):
                for text in searchable:
                    normalized_text = _normalize_property_value(text)
                    if not query or not normalized_text:
                        continue
                    if len(normalized_text) <= 2 and not (query == property_code_query and normalized_text == _normalize_property_value(code)):
                        continue
                    if query in normalized_text or normalized_text in query:
                        score = 1.0 if (query == property_code_query and normalized_text == _normalize_property_value(code)) else max(0.45, 0.85 - (query_index * 0.001))
                        matched_text = text
                        break
                if score > 0:
                    break
            if extraction.invoice.property_name == "Duplicate Property" and code in {"DUP_CODE", "DUP_CODE_B"}:
                score = 0.92 if code == "DUP_CODE" else 0.89
                matched_text = "Duplicate Property"
            if score > 0:
                candidates.append(
                    PropertyMatchCandidate(
                        asset_id=f"asset-{code}",
                        asset_alias=code,
                        asset_name=match.property_name,
                        destination_code=match.destination_code,
                        matched_text=matched_text or code,
                        matched_column="address" if any(char.isdigit() for char in matched_text) else "property_name",
                        similarity_score=score,
                    )
                )
        candidates = sorted(candidates, key=lambda candidate: candidate.similarity_score, reverse=True)[:5]
        selected_match = None
        gate = {"passed": False, "reason": "No candidates", "top_n": 5, "min_score": 0.45}
        if candidates:
            top = candidates[0]
            runner_up = candidates[1].similarity_score if len(candidates) > 1 else 0.0
            margin = top.similarity_score - runner_up
            pass_margin = margin >= 0.08
            passed = top.similarity_score >= 0.45
            selected_match = self.properties.get(top.property_code) if passed else None
            gate = {
                **gate,
                "top_score": top.similarity_score,
                "runner_up_score": runner_up,
                "score_margin": margin,
                "pass_score": top.similarity_score >= 0.45,
                "pass_margin": pass_margin,
                "has_near_tie": margin < 0.08,
                "passed": bool(selected_match),
                "reason": (
                    "Property fuzzy gate passed"
                    if selected_match
                    else "Property fuzzy gate failed: score requirement not met"
                ),
            }
        return PropertyMatchEvaluation(
            property_match=selected_match,
            standardized_signals={"raw_input_signals": raw_input, "standardized_query_values": normalized_values},
            candidates=tuple(candidates),
            llm_advisory={"candidate_property_codes": []},
            gate=gate,
        )

    def get_property_match_by_asset_id(self, asset_id: str, matched_alias: str | None = None):
        for match in self.properties.values():
            if match.asset_id == asset_id:
                return PropertyMatch(
                    match.asset_id,
                    match.asset_alias,
                    match.asset_name,
                    match.ownership_type,
                    match.ownership,
                    match.asset_type,
                    match.business_unit_code,
                    match.destination_code,
                    matched_alias=matched_alias,
                )
        return None

    def get_asset_reference_rows(self) -> list[dict[str, Any]]:
        return [
            {"asset_name": match.asset_name, "asset_alias": match.asset_alias, "address": None}
            for match in self.properties.values()
        ]

    def find_duplicate_status(self, extraction, idempotency_key: str) -> str | None:
        return self.duplicate_status

    def get_active_no_action_email_patterns(self) -> list[NoActionEmailPattern]:
        return [
            NoActionEmailPattern(
                pattern_id="pattern-1",
                pattern_name="proofpoint_end_user_digest",
                sender_email_equals="noreply-digest@hillwood.com",
                sender_domain_equals="hillwood.com",
                subject_regex=r"^Spam\s+\d+U:",
                body_regex=r"Proofpoint",
                reason_template="Proofpoint end-user digest notification with no AP routing action required",
                priority=100,
            )
        ]


class FakePropertyMatchReviewer:
    def __init__(self, selected_asset_id: str | None = None, confidence: float = 0.93) -> None:
        self.selected_asset_id = selected_asset_id
        self.confidence = confidence

    def suggest(self, extraction, alias_mappings: list[dict[str, Any]]):
        if not alias_mappings:
            return None
        selected_asset_id = self.selected_asset_id or str(alias_mappings[0]["asset_id"])
        return FakePropertyMatchSuggestion((selected_asset_id,), self.confidence)


class FakePropertyMatchSuggestion:
    def __init__(self, candidate_asset_ids: tuple[str, ...], confidence: float) -> None:
        self.candidate_asset_ids = candidate_asset_ids
        self.confidence = confidence
        self.reason = "test reviewer selected a candidate"


def _rules() -> list[WorkflowRule]:
    return [
        _rule("hard_multi_invoice_pdf", 100, "document_flag", "ESCALATE", "ESCALATE_MULTI_INVOICE_PDF", {"flag": "multi_invoice_pdf"}),
        _rule("hard_separate_lien_waiver", 110, "document_flag", "ESCALATE", "ESCALATE_LIEN_WAIVER", {"flag": "separate_lien_waiver"}),
        _rule("hard_no_action_email_pattern", 112, "email_pattern_match", "DISCARD", "NO_ACTION", {"pattern_source": "no_action_email_patterns"}),
        _rule(
            "hard_current_reply_no_action",
            114,
            "current_reply_no_action",
            "DISCARD",
            "NO_ACTION",
            {"require_quoted_history": True, "allowed_sender_domains": ["hillwood.com"]},
        ),
        _rule(
            "appointment_informational_notice",
            116,
            "observed_fact",
            "DISCARD",
            "NO_ACTION",
            {
                "fact_key": "indicates_informational_appointment_notice",
                "expected": True,
                "document_types": ["unknown"],
                "blocked_flags": [
                    "link_only_invoice",
                    "missing_invoice_attachment",
                    "vendor_inquiry",
                    "wrong_destination",
                    "past_due",
                    "statement_or_account_summary",
                    "ach_or_auto_draft",
                    "ben_e_keith",
                    "contract_or_pay_application",
                    "lien_release_related",
                    "conflicting_signals",
                    "low_text_quality",
                ],
                "forbid_source_attachments": True,
            },
        ),
        _rule(
            "hard_wrong_file_type",
            115,
            "attachment_extension",
            "ESCALATE",
            "ESCALATE_WRONG_FILE_TYPE",
            {
                "disallowed_extensions": [".jpg", ".jpeg", ".png", ".doc", ".docx", ".xls", ".xlsx"],
                "exempt_document_types": ["ach_notice", "auto_draft_notice", "ben_e_keith_notice"],
                "exempt_document_flags": ["ach_or_auto_draft", "ben_e_keith"],
            },
        ),
        _rule("hard_pdf_required_unreadable", 117, "pre_decision_fact", "ESCALATE", "ESCALATE_GENERAL", {"fact_key": "pdf_required_but_unreadable", "expected": True}),
        _rule("hard_pdf_text_low_quality", 118, "pre_decision_fact", "ESCALATE", "ESCALATE_GENERAL", {"fact_key": "pdf_text_low_quality", "expected": True}),
        _rule("hard_link_only_invoice", 120, "document_flag", "ESCALATE", "ESCALATE_LINK_ONLY", {"flag": "link_only_invoice"}),
        _rule("hard_contract_or_pay_app", 130, "document_type", "ESCALATE", "ESCALATE_CONTRACT_PAY_APP", {"document_types": ["contract", "pay_application"]}),
        _rule("hard_vendor_inquiry", 140, "document_flag", "ESCALATE", "ESCALATE_VENDOR_QUESTION", {"flag": "vendor_inquiry"}),
        _rule("hard_wrong_destination", 142, "document_flag", "ESCALATE", "ESCALATE_WRONG_DESTINATION", {"flag": "wrong_destination"}),
        _rule("hard_past_due_notice", 145, "document_flag", "ESCALATE", "ESCALATE_PAST_DUE", {"flag": "past_due"}),
        _rule("hard_mixed_item_destinations", 148, "aggregation_mixed_destinations", "ESCALATE", "ESCALATE_SPLIT_MULTI_PDF", {"aggregation_reason": "mixed_item_destinations"}),
        _rule("duplicate_candidate", 200, "duplicate_check", "ESCALATE", "ESCALATE_DUPLICATE_SUSPECTED", {"duplicate_statuses": ["suspected"]}),
        _rule(
            "check_request_medius_property",
            250,
            "check_request_property_routing",
            "AUTO",
            None,
            {"document_types": ["check_request"], "allowed_destination_codes": ["MEDIUS_PROPERTIES"]},
        ),
        _rule("hard_check_request", 260, "document_type", "ESCALATE", "ESCALATE_CHECK_REQUEST", {"document_types": ["check_request"]}),
        _rule(
            "alc_escalation",
            300,
            "alc_signal",
            "ESCALATE",
            "ESCALATE_ALC",
            {
                "business_unit_codes": ["ALC"],
                "text_phrases": ["Alliance Landscape", "Alliance Landscaping"],
                "standalone_terms": ["ALC"],
                "text_signal_exempt_property_addresses": ["9800 Hillwood Pkwy"],
            },
        ),
        _rule(
            "informational_property_notice",
            350,
            "informational_property_notice",
            "AUTO",
            None,
            {
                "document_types": ["unknown"],
                "blocked_flags": [
                    "link_only_invoice",
                    "vendor_inquiry",
                    "past_due",
                    "contract_or_pay_application",
                    "conflicting_signals",
                    "low_text_quality",
                ],
            },
        ),
        _rule("asset_type_multifamily", 375, "property_asset_type", "ESCALATE", "ESCALATE_MULTIFAMILY", {"asset_type": "Multifamily", "document_types": ["invoice"]}),
        _rule(
            "amount_over_threshold",
            400,
            "amount_threshold",
            "ESCALATE",
            "ESCALATE_OVER_10000",
            {
                "runtime_config_key": "amount_review_threshold",
                "exempt_destination": "MEDIUS_PROPERTIES",
                "exempt_requires_project_number": True,
            },
        ),
        _rule("statement_file", 500, "document_type", "FILE", "FOLDER_STATEMENTS", {"document_types": ["statement", "account_summary"]}),
        _rule("ach_notice_file", 520, "document_type", "FILE", "FOLDER_ACH", {"document_types": ["ach_notice", "auto_draft_notice"]}),
        _rule("ben_e_keith_notice_file", 113, "document_flag", "FILE", "FOLDER_BEN_E_KEITH", {"flag": "ben_e_keith"}),
        _rule("property_routing_match", 700, "property_routing_match", "AUTO", None, {"requires_property_route": True}),
        _rule("hard_unmatched_building", 750, "property_unmatched", "ESCALATE", "ESCALATE_UNMATCHED_BUILDING", {"document_types": ["invoice", "unknown"]}),
        _rule("confidence_below_threshold", 800, "confidence_threshold", "ESCALATE", "ESCALATE_GENERAL", {"runtime_config_key": "confidence_threshold"}),
        _rule("fallback_escalate", 900, "fallback", "ESCALATE", "ESCALATE_GENERAL", {"always": True}),
    ]


def _rule(code: str, priority: int, condition_type: str, outcome: str, destination: str | None, conditions: dict[str, Any]) -> WorkflowRule:
    return WorkflowRule(code, code, priority, condition_type, outcome, destination, f"{code} reason", 1, conditions)


def _payload(**overrides: Any) -> dict[str, Any]:
    confidence = overrides.get("confidence", 0.95)
    observed_facts = _observed_facts(overrides.get("flags", []))
    if overrides.get("requires_merge", False):
        observed_facts["mentions_merge_or_combine_required"] = True
    if overrides.get("multi_invoice", False):
        observed_facts["indicates_multiple_invoices"] = True
    if overrides.get("link_only", False):
        observed_facts["mentions_payment_link_only"] = True
    requires_attachment = overrides.get("requires_attachment", True)
    if overrides.get("has_invoice_attachment") is False and requires_attachment is True:
        observed_facts["mentions_missing_invoice_attachment"] = True
    payload = {
        "schema_version": "extraction.v1",
        "extractor": {"type": "fixture", "name": "local_fixture", "model": None, "prompt_version": None},
        "email": {
            "subject": overrides.get("subject", "Invoice 100"),
            "sender_email": overrides.get("sender_email", "vendor@example.com"),
            "received_at": overrides.get("received_at"),
        },
        "document": {
            "document_type": overrides.get("document_type", "invoice"),
            "requires_attachment": requires_attachment,
            "has_invoice_attachment": overrides.get("has_invoice_attachment", True),
            "link_only": overrides.get("link_only", False),
            "multi_invoice": overrides.get("multi_invoice", False),
        },
        "invoice": {
            "invoice_number": overrides.get("invoice_number", "INV-100"),
            "project_number": overrides.get("project_number"),
            "job_number": overrides.get("job_number"),
            "invoice_date": overrides.get("invoice_date"),
            "due_date": overrides.get("due_date"),
            "amount": overrides.get("amount", 1000),
            "currency": "USD",
            "vendor_name": overrides.get("vendor_name", "Vendor"),
            "vendor_email": None,
            "bill_to": overrides.get("bill_to"),
            "bill_to_name_line_1": overrides.get("bill_to_name_line_1"),
            "bill_to_name_line_2": overrides.get("bill_to_name_line_2"),
            "bill_to_street_address": overrides.get("bill_to_street_address"),
            "bill_to_suite": overrides.get("bill_to_suite"),
            "bill_to_city": overrides.get("bill_to_city"),
            "bill_to_state": overrides.get("bill_to_state"),
            "bill_to_zip_code": overrides.get("bill_to_zip_code"),
            "property_code": overrides.get("property_code", "HW1"),
            "property_name": overrides.get("property_name"),
            "service_address": overrides.get("service_address"),
        },
        "property_lookup": overrides.get("property_lookup") or {
            "property_code": _normalize_property_value(overrides.get("property_code", "HW1")) or None,
            "property_name": _normalize_property_value(overrides.get("property_name")) or None,
            "tenant": None,
            "address": _normalize_property_value(
                overrides.get("service_address")
                or overrides.get("bill_to_street_address")
                or overrides.get("bill_to")
            )
            or None,
            "suite": _normalize_property_value(overrides.get("bill_to_suite")) or None,
            "city": _normalize_property_value(overrides.get("bill_to_city")) or None,
            "state": _normalize_property_value(overrides.get("bill_to_state")) or None,
            "zipcode": _normalize_property_value(overrides.get("bill_to_zip_code")) or None,
        },
        "business_signals": {
            "business_unit_code": overrides.get("business_unit_code", "PROP"),
            "possible_property_aliases": overrides.get("possible_property_aliases", []),
            "subject_instruction_hint": None,
        },
        "observed_facts": observed_facts,
        "confidence": {
            "overall": confidence,
            "document_type": confidence,
            "invoice_fields": confidence,
            "property_identity": confidence,
            "business_unit": confidence,
        },
        "evidence": {
            "summary": overrides.get("evidence_summary", "fixture"),
            "source_attachments": overrides.get("source_attachments", ["invoice.pdf"]),
            "source_pages": [],
        },
    }
    return payload


def _observed_facts(flags: list[str]) -> dict[str, bool]:
    observed = {
        "current_invoice_is_past_due": False,
        "account_has_past_due_aging_balance": False,
        "contains_aging_summary": False,
        "mentions_separate_backup_document": False,
        "mentions_merge_or_combine_required": False,
        "mentions_lien_waiver_or_release": False,
        "mentions_payment_link_only": False,
        "mentions_missing_invoice_attachment": False,
        "indicates_multiple_invoices": False,
        "indicates_statement_or_account_summary": False,
        "indicates_contract_or_pay_application": False,
        "indicates_vendor_question_or_payment_inquiry": False,
        "indicates_wrong_destination": False,
        "latest_reply_indicates_no_ap_action": False,
        "indicates_informational_appointment_notice": False,
        "indicates_ach_or_auto_draft": False,
        "indicates_ben_e_keith": False,
        "has_conflicting_signals": False,
        "has_low_text_quality": False,
    }
    for flag in flags:
        if flag == "multi_invoice_pdf":
            observed["indicates_multiple_invoices"] = True
        elif flag == "separate_lien_waiver":
            observed["mentions_separate_backup_document"] = True
        elif flag == "link_only_invoice":
            observed["mentions_payment_link_only"] = True
        elif flag == "missing_invoice_attachment":
            observed["mentions_missing_invoice_attachment"] = True
        elif flag == "contract_or_pay_application":
            observed["indicates_contract_or_pay_application"] = True
        elif flag == "vendor_inquiry":
            observed["indicates_vendor_question_or_payment_inquiry"] = True
        elif flag == "wrong_destination":
            observed["indicates_wrong_destination"] = True
        elif flag == "latest_reply_no_action":
            observed["latest_reply_indicates_no_ap_action"] = True
        elif flag == "informational_appointment_notice":
            observed["indicates_informational_appointment_notice"] = True
        elif flag == "past_due":
            observed["current_invoice_is_past_due"] = True
        elif flag == "statement_or_account_summary":
            observed["indicates_statement_or_account_summary"] = True
        elif flag == "ach_or_auto_draft":
            observed["indicates_ach_or_auto_draft"] = True
        elif flag == "ben_e_keith":
            observed["indicates_ben_e_keith"] = True
        elif flag == "lien_release_related":
            observed["mentions_lien_waiver_or_release"] = True
        elif flag == "conflicting_signals":
            observed["has_conflicting_signals"] = True
        elif flag == "low_text_quality":
            observed["has_low_text_quality"] = True
    return observed
