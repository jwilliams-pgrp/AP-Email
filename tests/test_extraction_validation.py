from __future__ import annotations

import unittest

from ap_automation.models.extraction import ExtractionValidationError, validate_extraction
from ap_automation.services.codex_extractor import _prompt
from ap_automation.services.msg_parser import ParsedMsg


class ExtractionValidationTests(unittest.TestCase):
    def test_validates_required_contract(self) -> None:
        extraction = validate_extraction(_base_payload())

        self.assertEqual(extraction.schema_version, "extraction.v1")
        self.assertEqual(extraction.document.document_type, "invoice")
        self.assertEqual(extraction.confidence.overall, 0.95)

    def test_derives_internal_flags_from_observed_facts(self) -> None:
        payload = _base_payload()
        payload["observed_facts"]["mentions_past_due"] = True
        payload["observed_facts"]["mentions_merge_or_combine_required"] = True

        extraction = validate_extraction(payload)

        self.assertIn("past_due", extraction.document.document_flags)
        self.assertTrue(extraction.document.requires_merge)

    def test_rejects_llm_returned_document_flags(self) -> None:
        payload = _base_payload()
        payload["document"]["document_flags"] = ["past_due"]

        with self.assertRaises(ExtractionValidationError) as exc:
            validate_extraction(payload)

        self.assertIn("document.document_flags is derived by Python", str(exc.exception))

    def test_rejects_llm_returned_requires_merge(self) -> None:
        payload = _base_payload()
        payload["document"]["requires_merge"] = True

        with self.assertRaises(ExtractionValidationError) as exc:
            validate_extraction(payload)

        self.assertIn("document.requires_merge is derived by Python", str(exc.exception))

    def test_rejects_unknown_document_type(self) -> None:
        payload = _base_payload()
        payload["document"]["document_type"] = "spreadsheet"

        with self.assertRaises(ExtractionValidationError) as exc:
            validate_extraction(payload)

        self.assertIn("document.document_type", str(exc.exception))

    def test_rejects_missing_confidence(self) -> None:
        payload = _base_payload()
        del payload["confidence"]["overall"]

        with self.assertRaises(ExtractionValidationError) as exc:
            validate_extraction(payload)

        self.assertIn("confidence.overall", str(exc.exception))

    def test_codex_prompt_includes_validator_required_field_names(self) -> None:
        prompt = _prompt(
            ParsedMsg(
                subject="Invoice 100",
                sender_email="vendor@example.com",
                sender_name="Vendor",
                received_at=None,
                body_text="Invoice 100 for $120.50",
                transport_headers=None,
                attachments=(),
                metadata={},
            ),
            [],
        )

        required_field_names = [
            '"link_only"',
            '"multi_invoice"',
            '"observed_facts"',
            '"mentions_merge_or_combine_required"',
            '"amount"',
            '"business_unit_code"',
            '"invoice_fields"',
            '"property_identity"',
            '"summary"',
        ]
        for field_name in required_field_names:
            self.assertIn(field_name, prompt)


def _base_payload() -> dict:
    return {
        "schema_version": "extraction.v1",
        "extractor": {"type": "fixture", "name": "local_fixture", "model": None, "prompt_version": None},
        "email": {"subject": "Invoice 100", "sender_email": "vendor@example.com", "received_at": None},
        "document": {
            "document_type": "invoice",
            "requires_attachment": True,
            "has_invoice_attachment": True,
            "link_only": False,
            "multi_invoice": False,
        },
        "invoice": {
            "invoice_number": "100",
            "invoice_date": None,
            "due_date": None,
            "amount": 120.50,
            "currency": "USD",
            "vendor_name": "Vendor",
            "vendor_email": None,
            "bill_to": "Alliance",
            "property_code": "HW1",
            "property_name": None,
            "service_address": None,
        },
        "business_signals": {"business_unit_code": "PROP", "possible_property_aliases": [], "subject_instruction_hint": None},
        "observed_facts": {
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
        },
        "confidence": {
            "overall": 0.95,
            "document_type": 0.95,
            "invoice_fields": 0.95,
            "property_identity": 0.95,
            "business_unit": 0.95,
        },
        "evidence": {"summary": "fixture", "source_attachments": [], "source_pages": []},
    }
