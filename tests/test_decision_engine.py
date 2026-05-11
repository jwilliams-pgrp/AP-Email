from __future__ import annotations

import unittest
from dataclasses import replace
from typing import Any

from ap_automation.models.decision import Destination, PropertyMatch, WorkflowRule
from ap_automation.models.extraction import validate_extraction
from ap_automation.repositories.protocols import PolicyRepository
from ap_automation.services.decision_engine import DecisionEngine, MissingWorkflowConfigError


class DecisionEngineGoldenScenarioTests(unittest.TestCase):
    def test_clean_hillwood_owned_invoice_routes_to_medius_prop(self) -> None:
        decision = self._decide(property_code="HW1")

        self.assertEqual(decision.outcome, "AUTO")
        self.assertEqual(decision.destination_code, "MEDIUS_PROP")
        self.assertEqual(decision.matched_rule_code, "property_routing_match")

    def test_clean_external_pm_invoice_routes_to_pm_destination(self) -> None:
        decision = self._decide(property_code="EXT1")

        self.assertEqual(decision.outcome, "AUTO")
        self.assertEqual(decision.destination_code, "PM_TIFFANY_BECK_NUVEEN")

    def test_alc_invoice_routes_to_alc_destination(self) -> None:
        decision = self._decide(business_unit_code="ALC", property_code=None, bill_to="Alliance Landscape Company")

        self.assertEqual(decision.outcome, "AUTO")
        self.assertEqual(decision.destination_code, "MEDIUS_ALC")
        self.assertEqual(decision.matched_rule_code, "bill_to_alc")

    def test_multifamily_invoice_routes_to_multifamily_destination(self) -> None:
        decision = self._decide(business_unit_code="MF", property_code=None, bill_to="Multifamily")

        self.assertEqual(decision.outcome, "AUTO")
        self.assertEqual(decision.destination_code, "MEDIUS_MF")

    def test_multi_invoice_pdf_routes_to_review(self) -> None:
        decision = self._decide(flags=["multi_invoice_pdf"], multi_invoice=True)

        self.assertEqual(decision.outcome, "REVIEW")
        self.assertEqual(decision.matched_rule_code, "hard_multi_invoice_pdf")

    def test_invoice_plus_lien_waiver_routes_to_review(self) -> None:
        decision = self._decide(flags=["invoice_plus_lien_waiver", "lien_release_related"], requires_merge=True)

        self.assertEqual(decision.outcome, "REVIEW")
        self.assertEqual(decision.matched_rule_code, "hard_invoice_plus_lien_waiver")

    def test_link_only_invoice_routes_to_review(self) -> None:
        decision = self._decide(flags=["link_only_invoice"], link_only=True, has_invoice_attachment=False)

        self.assertEqual(decision.outcome, "REVIEW")
        self.assertEqual(decision.matched_rule_code, "hard_link_only_invoice")

    def test_contract_routes_to_review(self) -> None:
        decision = self._decide(document_type="contract")

        self.assertEqual(decision.outcome, "REVIEW")
        self.assertEqual(decision.matched_rule_code, "hard_contract_or_pay_app")

    def test_high_dollar_invoice_files_to_lien_release(self) -> None:
        decision = self._decide(amount=15000)

        self.assertEqual(decision.outcome, "FILE")
        self.assertEqual(decision.destination_code, "FOLDER_LIEN_RELEASE")
        self.assertEqual(decision.matched_rule_code, "amount_over_threshold")

    def test_duplicate_invoice_routes_to_review(self) -> None:
        decision = self._decide(duplicate_status="candidate")

        self.assertEqual(decision.outcome, "REVIEW")
        self.assertEqual(decision.matched_rule_code, "duplicate_candidate")

    def test_statement_files_to_statement_folder(self) -> None:
        decision = self._decide(document_type="statement")

        self.assertEqual(decision.outcome, "FILE")
        self.assertEqual(decision.destination_code, "FOLDER_STATEMENTS")

    def test_ach_notice_files_to_ach_folder(self) -> None:
        decision = self._decide(document_type="ach_notice")

        self.assertEqual(decision.outcome, "FILE")
        self.assertEqual(decision.destination_code, "FOLDER_ACH")

    def test_ben_e_keith_notice_files_to_ben_e_keith_folder(self) -> None:
        decision = self._decide(document_type="ben_e_keith_notice")

        self.assertEqual(decision.outcome, "FILE")
        self.assertEqual(decision.destination_code, "FOLDER_BEN_E_KEITH")

    def test_sold_property_flags(self) -> None:
        decision = self._decide(property_code="SOLD1")

        self.assertEqual(decision.outcome, "FLAG")
        self.assertEqual(decision.matched_rule_code, "sold_property")

    def test_unknown_building_routes_to_review(self) -> None:
        decision = self._decide(property_code="UNKNOWN")

        self.assertEqual(decision.outcome, "REVIEW")
        self.assertEqual(decision.matched_rule_code, "fallback_review")

    def test_low_confidence_routes_to_review(self) -> None:
        decision = self._decide(confidence=0.50)

        self.assertEqual(decision.outcome, "REVIEW")
        self.assertEqual(decision.matched_rule_code, "confidence_below_threshold")

    def test_missing_automatic_route_fields_routes_to_review(self) -> None:
        decision = self._decide(vendor_name=None, property_code=None, bill_to=None, business_unit_code=None)

        self.assertEqual(decision.outcome, "REVIEW")
        self.assertIn("Missing required automatic-routing fields", decision.reason)

    def test_missing_required_workflow_config_raises(self) -> None:
        repository = InMemoryPolicyRepository()
        repository.config.pop("confidence_threshold")
        engine = DecisionEngine(repository)

        with self.assertRaises(MissingWorkflowConfigError):
            engine.decide(validate_extraction(_payload()), "key")

    def _decide(self, **overrides: Any):
        repository = InMemoryPolicyRepository(duplicate_status=overrides.pop("duplicate_status", None))
        extraction = validate_extraction(_payload(**overrides))
        return DecisionEngine(repository).decide(extraction, "idempotency-key").decision


class InMemoryPolicyRepository(PolicyRepository):
    def __init__(self, duplicate_status: str | None = None) -> None:
        self.config: dict[str, Any] = {
            "dry_run": True,
            "confidence_threshold": 0.90,
            "amount_review_threshold": 10000,
            "default_review_destination": "REVIEW_QUEUE",
        }
        self.duplicate_status = duplicate_status
        self.destinations = {
            "MEDIUS_PROP": Destination("MEDIUS_PROP", "email", "Medius PROP", "medius.prop@example.com", None, None),
            "MEDIUS_ALC": Destination("MEDIUS_ALC", "email", "Medius ALC", "medius.alc@example.com", None, None),
            "MEDIUS_MF": Destination("MEDIUS_MF", "email", "Medius MF", "medius.mf@example.com", None, None),
            "PM_TIFFANY_BECK_NUVEEN": Destination("PM_TIFFANY_BECK_NUVEEN", "email", "Tiffany Beck", "tiffany@example.com", None, None),
            "FOLDER_STATEMENTS": Destination("FOLDER_STATEMENTS", "folder", "Statements", None, "local/outbound/review-statements", None),
            "FOLDER_ACH": Destination("FOLDER_ACH", "folder", "ACH", None, "local/outbound/ach", None),
            "FOLDER_BEN_E_KEITH": Destination("FOLDER_BEN_E_KEITH", "folder", "Ben E Keith", None, "local/outbound/ben-e-keith", None),
            "FOLDER_LIEN_RELEASE": Destination("FOLDER_LIEN_RELEASE", "folder", "Lien Release", None, "local/outbound/lien-release", None),
            "REVIEW_QUEUE": Destination("REVIEW_QUEUE", "review_queue", "Review", None, None, None),
        }
        self.properties = {
            "HW1": PropertyMatch("HW1", "Hillwood One", "hillwood_owned", "internal", "PROP", "MEDIUS_PROP", "MEDIUS_PROP", False),
            "EXT1": PropertyMatch("EXT1", "External One", "investor_managed", "external_pm", "PROP", "PM_TIFFANY_BECK_NUVEEN", "PM_TIFFANY_BECK_NUVEEN", False),
            "SOLD1": PropertyMatch("SOLD1", "Sold One", "sold", "sold", "PROP", "MEDIUS_PROP", "MEDIUS_PROP", True),
        }

    def get_runtime_config(self) -> dict[str, Any]:
        return self.config

    def get_active_workflow_rules(self) -> list[WorkflowRule]:
        return _rules()

    def get_destination(self, destination_code: str) -> Destination:
        return self.destinations[destination_code]

    def match_property(self, extraction):
        code = extraction.invoice.property_code
        return self.properties.get(code) if code else None

    def find_duplicate_status(self, extraction, idempotency_key: str) -> str | None:
        return self.duplicate_status


def _rules() -> list[WorkflowRule]:
    return [
        _rule("hard_multi_invoice_pdf", 100, "document_flag", "REVIEW", "REVIEW_QUEUE", {"flag": "multi_invoice_pdf"}),
        _rule("hard_invoice_plus_lien_waiver", 110, "document_flag", "REVIEW", "REVIEW_QUEUE", {"flag": "invoice_plus_lien_waiver"}),
        _rule("hard_link_only_invoice", 120, "document_flag", "REVIEW", "REVIEW_QUEUE", {"flag": "link_only_invoice"}),
        _rule("hard_contract_or_pay_app", 130, "document_type", "REVIEW", "REVIEW_QUEUE", {"document_types": ["contract", "pay_application"]}),
        _rule("hard_vendor_inquiry", 140, "document_type", "REVIEW", "REVIEW_QUEUE", {"document_types": ["vendor_question", "payment_inquiry", "past_due_notice"]}),
        _rule("duplicate_candidate", 200, "duplicate_check", "REVIEW", "REVIEW_QUEUE", {"duplicate_statuses": ["candidate", "suspected"]}),
        _rule("sold_property", 300, "property_status", "FLAG", "REVIEW_QUEUE", {"is_sold": True}),
        _rule("amount_over_threshold", 400, "amount_threshold", "FILE", "FOLDER_LIEN_RELEASE", {"runtime_config_key": "amount_review_threshold"}),
        _rule("statement_file", 500, "document_type", "FILE", "FOLDER_STATEMENTS", {"document_types": ["statement", "account_summary"]}),
        _rule("ach_notice_file", 520, "document_type", "FILE", "FOLDER_ACH", {"document_types": ["ach_notice", "auto_draft_notice"]}),
        _rule("ben_e_keith_notice_file", 530, "document_type", "FILE", "FOLDER_BEN_E_KEITH", {"document_types": ["ben_e_keith_notice"]}),
        _rule("bill_to_alc", 600, "bill_to_business_unit", "AUTO", "MEDIUS_ALC", {"business_unit_code": "ALC"}),
        _rule("bill_to_mf", 610, "bill_to_business_unit", "AUTO", "MEDIUS_MF", {"business_unit_code": "MF"}),
        _rule("property_routing_match", 700, "property_routing_match", "AUTO", None, {"requires_property_route": True}),
        _rule("confidence_below_threshold", 800, "confidence_threshold", "REVIEW", "REVIEW_QUEUE", {"runtime_config_key": "confidence_threshold"}),
        _rule("fallback_review", 900, "fallback", "REVIEW", "REVIEW_QUEUE", {"always": True}),
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
    if overrides.get("has_invoice_attachment") is False:
        observed_facts["mentions_missing_invoice_attachment"] = True
    payload = {
        "schema_version": "extraction.v1",
        "extractor": {"type": "fixture", "name": "local_fixture", "model": None, "prompt_version": None},
        "email": {"subject": "Invoice 100", "sender_email": "vendor@example.com", "received_at": None},
        "document": {
            "document_type": overrides.get("document_type", "invoice"),
            "requires_attachment": True,
            "has_invoice_attachment": overrides.get("has_invoice_attachment", True),
            "link_only": overrides.get("link_only", False),
            "multi_invoice": overrides.get("multi_invoice", False),
        },
        "invoice": {
            "invoice_number": "INV-100",
            "invoice_date": None,
            "due_date": None,
            "amount": overrides.get("amount", 1000),
            "currency": "USD",
            "vendor_name": overrides.get("vendor_name", "Vendor"),
            "vendor_email": None,
            "bill_to": overrides.get("bill_to", "Hillwood One"),
            "property_code": overrides.get("property_code", "HW1"),
            "property_name": None,
            "service_address": None,
        },
        "business_signals": {
            "business_unit_code": overrides.get("business_unit_code", "PROP"),
            "possible_property_aliases": [],
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
        "evidence": {"summary": "fixture", "source_attachments": [], "source_pages": []},
    }
    return payload


def _observed_facts(flags: list[str]) -> dict[str, bool]:
    observed = {
        "mentions_past_due": False,
        "mentions_separate_backup_document": False,
        "mentions_merge_or_combine_required": False,
        "mentions_lien_waiver_or_release": False,
        "mentions_payment_link_only": False,
        "mentions_missing_invoice_attachment": False,
        "indicates_multiple_invoices": False,
        "indicates_statement_or_account_summary": False,
        "indicates_contract_or_pay_application": False,
        "indicates_vendor_question_or_payment_inquiry": False,
        "indicates_ach_or_auto_draft": False,
        "indicates_ben_e_keith": False,
        "indicates_sold_property": False,
        "has_conflicting_signals": False,
        "has_low_text_quality": False,
    }
    for flag in flags:
        if flag == "multi_invoice_pdf":
            observed["indicates_multiple_invoices"] = True
        elif flag == "invoice_plus_lien_waiver":
            observed["mentions_lien_waiver_or_release"] = True
        elif flag == "link_only_invoice":
            observed["mentions_payment_link_only"] = True
        elif flag == "missing_invoice_attachment":
            observed["mentions_missing_invoice_attachment"] = True
        elif flag == "contract_or_pay_application":
            observed["indicates_contract_or_pay_application"] = True
        elif flag == "vendor_inquiry":
            observed["indicates_vendor_question_or_payment_inquiry"] = True
        elif flag == "past_due":
            observed["mentions_past_due"] = True
        elif flag == "statement_or_account_summary":
            observed["indicates_statement_or_account_summary"] = True
        elif flag == "ach_or_auto_draft":
            observed["indicates_ach_or_auto_draft"] = True
        elif flag == "ben_e_keith":
            observed["indicates_ben_e_keith"] = True
        elif flag == "lien_release_related":
            observed["mentions_lien_waiver_or_release"] = True
        elif flag == "sold_property_candidate":
            observed["indicates_sold_property"] = True
        elif flag == "conflicting_signals":
            observed["has_conflicting_signals"] = True
        elif flag == "low_text_quality":
            observed["has_low_text_quality"] = True
    return observed
