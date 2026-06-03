from __future__ import annotations

import unittest

from ap_automation.models.extraction import ExtractionValidationError, validate_extraction, validate_extraction_batch
from ap_automation.services.azure_openai_extractor import _prompt, contract_repair_prompt, lint_extraction_contract
from ap_automation.services.msg_parser import ParsedMsg


class ExtractionValidationTests(unittest.TestCase):
    def test_validates_required_contract(self) -> None:
        extraction = validate_extraction(_base_payload())

        self.assertEqual(extraction.schema_version, "extraction.v1")
        self.assertEqual(extraction.document.document_type, "invoice")
        self.assertEqual(extraction.confidence.overall, 0.95)
        self.assertEqual(extraction.invoice.property_code, "hw1")
        self.assertEqual(extraction.property_lookup.address, ())

    def test_single_invoice_backup_observed_fact_does_not_derive_separate_lien_waiver_flag(self) -> None:
        payload = _base_payload()
        payload["observed_facts"]["mentions_separate_backup_document"] = True

        extraction = validate_extraction(payload)

        self.assertNotIn("separate_lien_waiver", extraction.document.document_flags)

    def test_lien_waiver_mention_alone_does_not_derive_separate_lien_waiver_flag(self) -> None:
        payload = _base_payload()
        payload["observed_facts"]["mentions_lien_waiver_or_release"] = True

        extraction = validate_extraction(payload)

        self.assertIn("lien_release_related", extraction.document.document_flags)
        self.assertNotIn("separate_lien_waiver", extraction.document.document_flags)
        self.assertNotIn("invoice_plus_lien_waiver", extraction.document.document_flags)

    def test_validates_extraction_batch_with_attachment_items(self) -> None:
        first = _base_payload()
        first["evidence"]["source_attachments"] = ["a.pdf"]
        second = _base_payload()
        second["invoice"]["invoice_number"] = "200"
        second["evidence"]["source_attachments"] = ["b.pdf"]

        batch = validate_extraction_batch(
            {
                "schema_version": "extraction_batch.v1",
                "items": [
                    {"item_kind": "attachment", "item_key": "attachment:a", "display_name": "a.pdf", "metadata": {}, "extraction": first},
                    {"item_kind": "attachment", "item_key": "attachment:b", "display_name": "b.pdf", "metadata": {}, "extraction": second},
                ],
            }
        )

        self.assertEqual(len(batch.items), 2)
        self.assertEqual(batch.items[0].item_kind, "attachment")
        self.assertEqual(batch.items[1].extraction.invoice.invoice_number, "200")

    def test_batch_invoice_with_distinct_supporting_document_derives_separate_lien_waiver_flag(self) -> None:
        invoice = _base_payload()
        invoice["evidence"]["source_attachments"] = ["invoice.pdf"]
        invoice["observed_facts"]["mentions_separate_backup_document"] = False
        ticket = _base_payload()
        ticket["document"]["document_type"] = "unknown"
        ticket["document"]["has_invoice_attachment"] = False
        ticket["observed_facts"]["mentions_lien_waiver_or_release"] = False
        ticket["invoice"]["invoice_number"] = None
        ticket["invoice"]["vendor_name"] = "Vendor"
        ticket["invoice"]["property_code"] = "hw1"
        ticket["evidence"]["source_attachments"] = ["ticket.pdf"]
        ticket["evidence"]["summary"] = "Signed field ticket backup for Vendor at HW1."

        batch = validate_extraction_batch(
            {
                "schema_version": "extraction_batch.v1",
                "items": [
                    {"item_kind": "attachment", "item_key": "attachment:invoice", "display_name": "invoice.pdf", "metadata": {}, "extraction": invoice},
                    {"item_kind": "attachment", "item_key": "attachment:ticket", "display_name": "ticket.pdf", "metadata": {}, "extraction": ticket},
                ],
            }
        )

        self.assertTrue(batch.items[0].extraction.observed_facts.mentions_separate_backup_document)
        self.assertIn("separate_lien_waiver", batch.items[0].extraction.document.document_flags)
        self.assertNotIn("separate_lien_waiver", batch.items[1].extraction.document.document_flags)

    def test_batch_invoice_with_staffing_hours_support_derives_separate_lien_waiver_flag(self) -> None:
        invoice = _base_payload()
        invoice["invoice"]["invoice_number"] = "1069"
        invoice["invoice"]["vendor_name"] = "Blue Moon Event Staffing LLC"
        invoice["evidence"]["source_attachments"] = ["Invoice_1069_from_Blue_Moon_Event_Staffing_LLC.pdf"]
        invoice["observed_facts"]["mentions_separate_backup_document"] = False
        support = _base_payload()
        support["document"]["document_type"] = "unknown"
        support["document"]["has_invoice_attachment"] = False
        support["observed_facts"]["mentions_lien_waiver_or_release"] = False
        support["invoice"]["invoice_number"] = None
        support["invoice"]["vendor_name"] = None
        support["invoice"]["property_code"] = None
        support["property_lookup"]["property_code"] = None
        support["evidence"]["source_attachments"] = ["05 21 CT Kitchen Shift Rpt and Actual Hours Worked.pdf"]
        support["evidence"]["summary"] = "Supporting labor shift report/actual hours worked."

        batch = validate_extraction_batch(
            {
                "schema_version": "extraction_batch.v1",
                "items": [
                    {
                        "item_kind": "attachment",
                        "item_key": "attachment:invoice",
                        "display_name": "Invoice_1069_from_Blue_Moon_Event_Staffing_LLC.pdf",
                        "metadata": {},
                        "extraction": invoice,
                    },
                    {
                        "item_kind": "attachment",
                        "item_key": "attachment:support",
                        "display_name": "05 21 CT Kitchen Shift Rpt and Actual Hours Worked.pdf",
                        "metadata": {},
                        "extraction": support,
                    },
                ],
            }
        )

        self.assertTrue(batch.items[0].extraction.observed_facts.mentions_separate_backup_document)
        self.assertIn("separate_lien_waiver", batch.items[0].extraction.document.document_flags)
        self.assertNotIn("separate_lien_waiver", batch.items[1].extraction.document.document_flags)

    def test_batch_invoice_without_distinct_supporting_item_clears_backup_signal(self) -> None:
        invoice = _base_payload()
        invoice["observed_facts"]["mentions_separate_backup_document"] = True
        invoice["evidence"]["summary"] = "Invoice includes embedded work detail and references job photos on the invoice."

        batch = validate_extraction_batch(
            {
                "schema_version": "extraction_batch.v1",
                "excluded_attachments": [
                    {
                        "file_name": "logo.png",
                        "reason_code": "irrelevant_to_ap_workflow",
                        "reason": "Inline logo contains no AP workflow facts.",
                    }
                ],
                "items": [
                    {"item_kind": "attachment", "item_key": "attachment:invoice", "display_name": "invoice.pdf", "metadata": {}, "extraction": invoice},
                ],
            }
        )

        self.assertFalse(batch.items[0].extraction.observed_facts.mentions_separate_backup_document)
        self.assertNotIn("separate_lien_waiver", batch.items[0].extraction.document.document_flags)

    def test_validates_extraction_batch_with_excluded_attachments(self) -> None:
        payload = _base_payload()
        payload["evidence"]["source_attachments"] = ["invoice.pdf"]

        batch = validate_extraction_batch(
            {
                "schema_version": "extraction_batch.v1",
                "excluded_attachments": [
                    {
                        "file_name": "photo.jpg",
                        "reason_code": "irrelevant_to_ap_workflow",
                        "reason": "Photo contains no invoice or AP workflow facts.",
                        "source": "document_intelligence",
                    }
                ],
                "items": [
                    {
                        "item_kind": "attachment",
                        "item_key": "attachment:invoice",
                        "display_name": "invoice.pdf",
                        "metadata": {},
                        "extraction": payload,
                    }
                ],
            }
        )

        self.assertEqual(len(batch.items), 1)
        self.assertEqual(batch.excluded_attachments[0].file_name, "photo.jpg")
        self.assertEqual(batch.excluded_attachments[0].reason_code, "irrelevant_to_ap_workflow")

    def test_validates_extraction_batch_with_payment_instruction_support_exclusion(self) -> None:
        payload = _base_payload()
        payload["evidence"]["source_attachments"] = ["invoice.pdf"]

        batch = validate_extraction_batch(
            {
                "schema_version": "extraction_batch.v1",
                "excluded_attachments": [
                    {
                        "file_name": "wire-instructions.pdf",
                        "reason_code": "payment_instruction_support",
                        "reason": "Standalone wire instructions attached with a separate invoice.",
                        "source": "pymupdf",
                    }
                ],
                "items": [
                    {
                        "item_kind": "attachment",
                        "item_key": "attachment:invoice",
                        "display_name": "invoice.pdf",
                        "metadata": {},
                        "extraction": payload,
                    }
                ],
            }
        )

        self.assertEqual(batch.excluded_attachments[0].file_name, "wire-instructions.pdf")
        self.assertEqual(batch.excluded_attachments[0].reason_code, "payment_instruction_support")
        self.assertEqual(batch.excluded_attachments[0].reason, "Standalone wire instructions attached with a separate invoice.")
        self.assertEqual(batch.excluded_attachments[0].source, "pymupdf")

    def test_extraction_batch_rejects_invalid_excluded_attachments(self) -> None:
        with self.assertRaises(ExtractionValidationError) as exc:
            validate_extraction_batch(
                {
                    "schema_version": "extraction_batch.v1",
                    "excluded_attachments": [
                        {"file_name": "photo.jpg", "reason_code": "decorative", "reason": "", "source": "guess"}
                    ],
                    "items": [
                        {
                            "item_kind": "attachment",
                            "item_key": "attachment:invoice",
                            "display_name": "invoice.pdf",
                            "metadata": {},
                            "extraction": _base_payload(),
                        }
                    ],
                }
            )

        self.assertIn("excluded_attachments[0].reason_code", str(exc.exception))
        self.assertIn("excluded_attachments[0].reason", str(exc.exception))
        self.assertIn("excluded_attachments[0].source", str(exc.exception))

    def test_extraction_batch_accepts_item_evidence_that_cites_excluded_attachment(self) -> None:
        payload = _base_payload()
        payload["evidence"]["source_attachments"] = ["invoice.pdf", "photo.jpg"]

        batch = validate_extraction_batch(
            {
                "schema_version": "extraction_batch.v1",
                "excluded_attachments": [
                    {
                        "file_name": "photo.jpg",
                        "reason_code": "irrelevant_to_ap_workflow",
                        "reason": "Photo contains no AP workflow facts.",
                    }
                ],
                "items": [
                    {
                        "item_kind": "attachment",
                        "item_key": "attachment:invoice",
                        "display_name": "invoice.pdf",
                        "metadata": {},
                        "extraction": payload,
                    }
                ],
            }
        )

        self.assertEqual(batch.excluded_attachments[0].file_name, "photo.jpg")
        self.assertEqual(batch.items[0].extraction.evidence.source_attachments, ("invoice.pdf", "photo.jpg"))

    def test_extraction_batch_accepts_item_evidence_that_cites_payment_instruction_support(self) -> None:
        payload = _base_payload()
        payload["evidence"]["source_attachments"] = ["invoice.pdf"]
        payload["evidence"]["source_refs"] = [{"attachment": "wire-instructions.pdf", "page": 1}]

        batch = validate_extraction_batch(
            {
                "schema_version": "extraction_batch.v1",
                "excluded_attachments": [
                    {
                        "file_name": "wire-instructions.pdf",
                        "reason_code": "payment_instruction_support",
                        "reason": "Standalone payment instructions attached with a separate invoice.",
                    }
                ],
                "items": [
                    {
                        "item_kind": "attachment",
                        "item_key": "attachment:invoice",
                        "display_name": "invoice.pdf",
                        "metadata": {},
                        "extraction": payload,
                    }
                ],
            }
        )

        self.assertEqual(batch.excluded_attachments[0].file_name, "wire-instructions.pdf")
        self.assertEqual(batch.items[0].extraction.evidence.source_refs[0].attachment, "wire-instructions.pdf")

    def test_extraction_batch_rejects_invalid_nested_item(self) -> None:
        payload = _base_payload()
        del payload["confidence"]["overall"]

        with self.assertRaises(ExtractionValidationError) as exc:
            validate_extraction_batch(
                {
                    "schema_version": "extraction_batch.v1",
                    "items": [
                        {"item_kind": "attachment", "item_key": "attachment:a", "display_name": "a.pdf", "extraction": payload}
                    ],
                }
            )

        self.assertIn("items[0].extraction.confidence.overall", str(exc.exception))

    def test_accepts_normalized_property_lookup_fields(self) -> None:
        payload = _base_payload()
        payload["property_lookup"] = {
            "property_code": ["hwc1"],
            "property_name": ["hillwood commons i"],
            "tenant": ["hillwood"],
            "address": ["9800 hillwood parkway"],
            "suite": ["300"],
            "city": ["fort worth"],
            "state": ["tx"],
            "zipcode": ["76177"],
        }

        extraction = validate_extraction(payload)

        self.assertEqual(extraction.property_lookup.address, ("9800 hillwood parkway",))
        self.assertEqual(extraction.property_lookup.suite, ("300",))

    def test_accepts_ranked_address_candidates_and_flattens_for_legacy_lookup(self) -> None:
        payload = _base_payload()
        payload["invoice"]["bill_to"] = "Hillwood Alliance Group, 9800 Hillwood Parkway, Fort Worth TX 76177"
        payload["property_lookup"] = {
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
                    "confidence": 0.93,
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
                    "confidence": 0.82,
                    "evidence_text": "Bill To Hillwood Alliance Group",
                },
            ],
        }

        extraction = validate_extraction(payload)

        self.assertEqual([candidate.label for candidate in extraction.property_lookup.address_candidates], ["deliver_to", "bill_to"])
        self.assertEqual(extraction.property_lookup.address[0], "2451 westlake parkway")
        self.assertEqual(extraction.property_lookup.address[1], "2451 westlake parkway westlake tx 76262")
        self.assertEqual(extraction.property_lookup.address[2], "9800 hillwood parkway")
        self.assertEqual(extraction.property_lookup.city, ("westlake", "fort worth"))
        self.assertEqual(extraction.property_lookup.zipcode, ("76262", "76177"))

    def test_bill_to_components_are_mirrored_to_property_lookup_when_candidate_missing(self) -> None:
        payload = _base_payload()
        payload["invoice"]["property_code"] = None
        payload["invoice"]["property_name"] = "Frisco Station WMP"
        payload["invoice"]["bill_to"] = "Kim Cole, Hillwood Properties, 9800 Hillwood Parkway, Suite #300, Fort Worth, TX 76177"
        payload["invoice"]["bill_to_name_line_1"] = "Kim Cole"
        payload["invoice"]["bill_to_name_line_2"] = "Hillwood Properties"
        payload["invoice"]["bill_to_street_address"] = "9800 Hillwood Parkway"
        payload["invoice"]["bill_to_suite"] = "300"
        payload["invoice"]["bill_to_city"] = "Fort Worth"
        payload["invoice"]["bill_to_state"] = "TX"
        payload["invoice"]["bill_to_zip_code"] = "76177"
        payload["property_lookup"] = {
            "property_code": [],
            "property_name": ["frisco station"],
            "tenant": ["hillwood properties"],
            "address": [],
            "suite": [],
            "city": [],
            "state": [],
            "zipcode": [],
            "address_candidates": [],
        }

        extraction = validate_extraction(payload)

        self.assertEqual([candidate.label for candidate in extraction.property_lookup.address_candidates], ["bill_to"])
        self.assertEqual(extraction.property_lookup.address, ("9800 hillwood parkway", "9800 hillwood parkway fort worth tx 76177"))
        self.assertEqual(extraction.property_lookup.city, ("fort worth",))
        self.assertEqual(extraction.property_lookup.state, ("tx",))
        self.assertEqual(extraction.property_lookup.zipcode, ("76177",))

    def test_accepts_structured_evidence_source_refs(self) -> None:
        payload = _base_payload()
        payload["evidence"]["source_refs"] = [
            {"attachment": "Invoice SSAC-4389073 for HIL - Hillwood Retail; Justin, TX.pdf", "page": 1},
            {"attachment": "HIL - Hillwood Retail; Justin, TX Hourly Detail Report.pdf", "page": 2},
        ]

        extraction = validate_extraction(payload)

        self.assertEqual(extraction.evidence.source_pages, ())
        self.assertEqual(
            [(ref.attachment, ref.page) for ref in extraction.evidence.source_refs],
            [
                ("Invoice SSAC-4389073 for HIL - Hillwood Retail; Justin, TX.pdf", 1),
                ("HIL - Hillwood Retail; Justin, TX Hourly Detail Report.pdf", 2),
            ],
        )

    def test_normalizes_legacy_filename_page_strings_in_source_pages(self) -> None:
        payload = _base_payload()
        payload["evidence"]["source_pages"] = [
            "Invoice SSAC-4389073 for HIL - Hillwood Retail; Justin, TX.pdf:page1",
            "HIL - Hillwood Retail; Justin, TX Hourly Detail Report.pdf:page1",
        ]

        extraction = validate_extraction(payload)

        self.assertEqual(extraction.evidence.source_pages, (1,))
        self.assertEqual(
            [(ref.attachment, ref.page) for ref in extraction.evidence.source_refs],
            [
                ("Invoice SSAC-4389073 for HIL - Hillwood Retail; Justin, TX.pdf", 1),
                ("HIL - Hillwood Retail; Justin, TX Hourly Detail Report.pdf", 1),
            ],
        )

    def test_rejects_unknown_address_candidate_label(self) -> None:
        payload = _base_payload()
        payload["property_lookup"]["address_candidates"] = [
            {"rank": 1, "label": "remit_to", "street": "1 vendor road", "confidence": 0.99}
        ]

        with self.assertRaises(ExtractionValidationError) as exc:
            validate_extraction(payload)

        self.assertIn("property_lookup.address_candidates[0].label", str(exc.exception))

    def test_derives_internal_flags_from_observed_facts(self) -> None:
        payload = _base_payload()
        payload["observed_facts"]["current_invoice_is_past_due"] = True
        payload["observed_facts"]["mentions_merge_or_combine_required"] = True

        extraction = validate_extraction(payload)

        self.assertIn("past_due", extraction.document.document_flags)
        self.assertTrue(extraction.document.requires_merge)

    def test_account_aging_past_due_balance_does_not_derive_past_due(self) -> None:
        payload = _base_payload()
        payload["observed_facts"]["contains_aging_summary"] = True
        payload["observed_facts"]["account_has_past_due_aging_balance"] = True
        payload["observed_facts"]["current_invoice_is_past_due"] = False
        payload["invoice"]["invoice_number"] = "3576"
        payload["invoice"]["amount"] = 731.77
        payload["evidence"]["summary"] = (
            "Run a06116ee-a9c5-4f81-981a-49e92f14fdf4 invoice 3576 shows amount due "
            "$731.77 in Current aging bucket and separate 1-30 Days Past Due $6,530.41."
        )

        extraction = validate_extraction(payload)

        self.assertNotIn("past_due", extraction.document.document_flags)

    def test_invoice_due_before_received_date_without_explicit_label_does_not_derive_past_due(self) -> None:
        payload = _base_payload()
        payload["email"]["received_at"] = "2026-05-20T09:15:00-05:00"
        payload["invoice"]["due_date"] = "2026-05-10"
        payload["invoice"]["amount"] = 120.50
        payload["observed_facts"]["current_invoice_is_past_due"] = False
        payload["evidence"]["summary"] = "Invoice includes terms payable upon receipt and a copied invoice date."

        extraction = validate_extraction(payload)

        self.assertNotIn("past_due", extraction.document.document_flags)

    def test_explicit_due_date_before_received_date_derives_past_due(self) -> None:
        payload = _base_payload()
        payload["email"]["received_at"] = "2026-05-20T09:15:00-05:00"
        payload["invoice"]["due_date"] = "2026-05-10"
        payload["invoice"]["amount"] = 120.50
        payload["observed_facts"]["current_invoice_is_past_due"] = False
        payload["evidence"]["summary"] = "Invoice has explicit Due Date: 2026-05-10 and payment due balance."

        extraction = validate_extraction(payload)

        self.assertIn("past_due", extraction.document.document_flags)

    def test_past_due_language_derives_past_due_without_due_date(self) -> None:
        payload = _base_payload()
        payload["invoice"]["due_date"] = None
        payload["observed_facts"]["current_invoice_is_past_due"] = True
        payload["evidence"]["summary"] = "Invoice includes explicit overdue notice language for the current invoice."

        extraction = validate_extraction(payload)

        self.assertIn("past_due", extraction.document.document_flags)

    def test_payable_upon_receipt_invoice_date_copied_to_due_date_does_not_derive_past_due(self) -> None:
        payload = _base_payload()
        payload["email"]["received_at"] = "2026-05-22T09:15:00-05:00"
        payload["invoice"]["invoice_date"] = "2026-05-19"
        payload["invoice"]["due_date"] = "2026-05-19"
        payload["invoice"]["amount"] = 120.50
        payload["observed_facts"]["current_invoice_is_past_due"] = False
        payload["evidence"]["summary"] = "Invoice Date: 2026-05-19; terms say payable upon receipt, with no separate payment deadline label."

        extraction = validate_extraction(payload)

        self.assertNotIn("past_due", extraction.document.document_flags)

    def test_due_on_receipt_terms_do_not_populate_due_date_or_derive_past_due(self) -> None:
        payload = _base_payload()
        payload["email"]["received_at"] = "2026-05-28T09:15:00-05:00"
        payload["invoice"]["invoice_date"] = "2026-05-06"
        payload["invoice"]["due_date"] = None
        payload["invoice"]["amount"] = 1300.00
        payload["observed_facts"]["current_invoice_is_past_due"] = False
        payload["evidence"]["summary"] = "Invoice Date: 2026-05-06; Activity Date: 2026-05-06; Payment Due: Due On Receipt."

        extraction = validate_extraction(payload)

        self.assertIsNone(extraction.invoice.due_date)
        self.assertNotIn("past_due", extraction.document.document_flags)

    def test_invoice_due_on_or_after_received_date_does_not_derive_past_due(self) -> None:
        for due_date in ("2026-05-20", "2026-05-21"):
            with self.subTest(due_date=due_date):
                payload = _base_payload()
                payload["email"]["received_at"] = "2026-05-20T09:15:00-05:00"
                payload["invoice"]["due_date"] = due_date
                payload["invoice"]["amount"] = 120.50
                payload["observed_facts"]["current_invoice_is_past_due"] = False

                extraction = validate_extraction(payload)

                self.assertNotIn("past_due", extraction.document.document_flags)

    def test_past_due_notice_document_type_derives_past_due(self) -> None:
        payload = _base_payload()
        payload["document"]["document_type"] = "past_due_notice"
        payload["observed_facts"]["current_invoice_is_past_due"] = False

        extraction = validate_extraction(payload)

        self.assertIn("past_due", extraction.document.document_flags)

    def test_multi_invoice_flag_requires_single_attachment_invoice_context(self) -> None:
        payload = _base_payload()
        payload["observed_facts"]["indicates_multiple_invoices"] = True
        payload["document"]["multi_invoice"] = True
        payload["evidence"]["source_attachments"] = ["a.pdf", "b.pdf"]

        extraction = validate_extraction(payload)

        self.assertNotIn("multi_invoice_pdf", extraction.document.document_flags)

    def test_link_only_flag_excluded_for_auto_draft_notice(self) -> None:
        payload = _base_payload()
        payload["document"]["document_type"] = "auto_draft_notice"
        payload["document"]["has_invoice_attachment"] = False
        payload["document"]["link_only"] = True
        payload["observed_facts"]["mentions_payment_link_only"] = True

        extraction = validate_extraction(payload)

        self.assertNotIn("link_only_invoice", extraction.document.document_flags)

    def test_link_only_flag_applies_to_non_notice_without_attachment(self) -> None:
        payload = _base_payload()
        payload["document"]["document_type"] = "account_summary"
        payload["document"]["has_invoice_attachment"] = False
        payload["document"]["link_only"] = True
        payload["observed_facts"]["mentions_payment_link_only"] = True

        extraction = validate_extraction(payload)

        self.assertIn("link_only_invoice", extraction.document.document_flags)

    def test_link_only_portal_bill_with_body_facts_still_derives_link_only_invoice(self) -> None:
        payload = _base_payload()
        payload["document"]["document_type"] = "invoice"
        payload["document"]["has_invoice_attachment"] = False
        payload["document"]["link_only"] = True
        payload["invoice"]["invoice_number"] = None
        payload["invoice"]["amount"] = 193.98
        payload["invoice"]["vendor_name"] = "Utility Account Center"
        payload["invoice"]["property_code"] = "hw1"
        payload["invoice"]["property_name"] = "Hillwood One"
        payload["invoice"]["service_address"] = "3101 Example Road"
        payload["observed_facts"]["mentions_payment_link_only"] = True
        payload["evidence"]["source_attachments"] = []
        payload["evidence"]["summary"] = (
            "Account Center bill notice includes current bill amount, service address, "
            "and a portal login link to retrieve or pay the bill."
        )

        extraction = validate_extraction(payload)

        self.assertIn("link_only_invoice", extraction.document.document_flags)

    def test_attached_complete_invoice_payment_nudge_does_not_become_vendor_inquiry(self) -> None:
        payload = _base_payload()
        payload["observed_facts"]["indicates_vendor_question_or_payment_inquiry"] = True
        payload["evidence"]["summary"] = "Attached invoice is due and email asks when payment can be expected."

        extraction = validate_extraction(payload)

        self.assertEqual(extraction.document.document_type, "invoice")
        self.assertNotIn("vendor_inquiry", extraction.document.document_flags)

    def test_payment_research_case_still_becomes_vendor_inquiry(self) -> None:
        payload = _base_payload()
        payload["invoice"]["invoice_number"] = None
        payload["invoice"]["amount"] = 0.0
        payload["observed_facts"]["indicates_vendor_question_or_payment_inquiry"] = True
        payload["observed_facts"]["indicates_ach_or_auto_draft"] = True
        payload["observed_facts"]["has_conflicting_signals"] = True
        payload["evidence"]["summary"] = "ACH payment received with no remittance and two open invoices for the same amount."

        extraction = validate_extraction(payload)

        self.assertIn("vendor_inquiry", extraction.document.document_flags)

    def test_account_summary_with_invoice_like_fields_preserves_llm_classification(self) -> None:
        payload = _base_payload()
        payload["document"]["document_type"] = "account_summary"
        payload["invoice"]["invoice_date"] = "2026-05-01"
        payload["invoice"]["due_date"] = "2026-05-31"
        payload["evidence"]["source_attachments"] = ["Receipt.pdf"]
        payload["evidence"]["summary"] = "Receipt.pdf shows Invoice #: 1599669, Invoice Date, NET 30, line items, tax, total, and balance due."

        extraction = validate_extraction(payload)

        self.assertEqual(extraction.document.document_type, "account_summary")
        self.assertIn("statement_or_account_summary", extraction.document.document_flags)
        self.assertNotIn("_classification_normalization", extraction.raw)

    def test_non_payable_receipt_remains_account_summary(self) -> None:
        payload = _base_payload()
        payload["document"]["document_type"] = "account_summary"
        payload["document"]["has_invoice_attachment"] = False
        payload["invoice"]["invoice_number"] = None
        payload["invoice"]["invoice_date"] = None
        payload["invoice"]["due_date"] = None
        payload["invoice"]["amount"] = 0.0
        payload["evidence"]["source_attachments"] = ["Receipt.pdf"]
        payload["evidence"]["summary"] = "Payment confirmation receipt for a completed card payment."

        extraction = validate_extraction(payload)

        self.assertEqual(extraction.document.document_type, "account_summary")
        self.assertIn("statement_or_account_summary", extraction.document.document_flags)

    def test_statement_with_aging_and_open_items_remains_statement(self) -> None:
        payload = _base_payload()
        payload["document"]["document_type"] = "statement"
        payload["observed_facts"]["indicates_statement_or_account_summary"] = True
        payload["observed_facts"]["contains_aging_summary"] = True
        payload["invoice"]["invoice_date"] = None
        payload["invoice"]["due_date"] = None
        payload["evidence"]["summary"] = "Customer statement with aging summary and multiple open items."

        extraction = validate_extraction(payload)

        self.assertEqual(extraction.document.document_type, "statement")
        self.assertIn("statement_or_account_summary", extraction.document.document_flags)

    def test_mixed_receipt_invoice_preserves_account_summary_classification(self) -> None:
        payload = _base_payload()
        payload["document"]["document_type"] = "account_summary"
        payload["observed_facts"]["indicates_statement_or_account_summary"] = True
        payload["invoice"]["invoice_date"] = "2026-05-01"
        payload["evidence"]["source_attachments"] = ["Receipt.pdf"]
        payload["evidence"]["summary"] = "Receipt.pdf contains Invoice # 1599669, terms, subtotal, tax, and total."

        extraction = validate_extraction(payload)

        self.assertEqual(extraction.document.document_type, "account_summary")
        self.assertFalse(extraction.observed_facts.has_conflicting_signals)
        self.assertEqual(extraction.confidence.document_type, 0.95)
        self.assertIn("statement_or_account_summary", extraction.document.document_flags)

    def test_statement_with_aging_invoice_fields_and_due_date_remains_statement(self) -> None:
        payload = _base_payload()
        payload["document"]["document_type"] = "statement"
        payload["observed_facts"]["indicates_statement_or_account_summary"] = True
        payload["observed_facts"]["contains_aging_summary"] = True
        payload["observed_facts"]["account_has_past_due_aging_balance"] = True
        payload["observed_facts"]["current_invoice_is_past_due"] = True
        payload["invoice"]["invoice_number"] = "3576"
        payload["invoice"]["invoice_date"] = "2026-04-30"
        payload["invoice"]["due_date"] = "2026-05-10"
        payload["invoice"]["amount"] = 731.77
        payload["invoice"]["bill_to"] = "Hillwood Properties"
        payload["email"]["received_at"] = "2026-05-20T09:15:00-05:00"
        payload["evidence"]["summary"] = (
            "Customer statement with aging summary, invoice 3576, due date, bill-to, "
            "current amount due, and separate past-due aging buckets."
        )

        extraction = validate_extraction(payload)

        self.assertEqual(extraction.document.document_type, "statement")
        self.assertTrue(extraction.observed_facts.current_invoice_is_past_due)
        self.assertIn("statement_or_account_summary", extraction.document.document_flags)
        self.assertNotIn("past_due", extraction.document.document_flags)

    def test_wrong_destination_observed_fact_becomes_internal_flag(self) -> None:
        payload = _base_payload()
        payload["observed_facts"]["indicates_wrong_destination"] = True
        payload["evidence"]["summary"] = "Recipient replied that they should not have received this invoice and AP should escalate it."

        extraction = validate_extraction(payload)

        self.assertIn("wrong_destination", extraction.document.document_flags)

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

    def test_accepts_check_request_document_type(self) -> None:
        payload = _base_payload()
        payload["document"]["document_type"] = "check_request"
        payload["document"]["requires_attachment"] = False
        payload["document"]["has_invoice_attachment"] = False

        extraction = validate_extraction(payload)

        self.assertEqual(extraction.document.document_type, "check_request")

    def test_rejects_missing_confidence(self) -> None:
        payload = _base_payload()
        del payload["confidence"]["overall"]

        with self.assertRaises(ExtractionValidationError) as exc:
            validate_extraction(payload)

        self.assertIn("confidence.overall", str(exc.exception))

    def test_validation_errors_include_paths_for_bad_contract_types(self) -> None:
        cases = [
            ("invoice.property_code", ["gw34"], "invoice.property_code expected string or null, got list"),
            ("property_lookup.property_code", {"bad": "gw34"}, "property_lookup.property_code expected string, list of strings, or null, got dict"),
            ("observed_facts.current_invoice_is_past_due", "false", "observed_facts.current_invoice_is_past_due expected boolean, got str"),
            ("confidence.overall", "0.95", "confidence.overall expected number, got str"),
            ("invoice.invoice_date", 20260131, "invoice.invoice_date expected ISO date string or null, got int"),
            ("email.received_at", ["2026-01-31T10:00:00Z"], "email.received_at expected ISO datetime string or null, got list"),
            ("evidence.source_pages", "1", "evidence.source_pages expected list of integers or null, got str"),
            ("evidence.source_pages", ["1"], "evidence.source_pages[0] expected integer page or legacy attachment:page reference, got str"),
            ("evidence.source_refs", ["invoice.pdf:1"], "evidence.source_refs[0] expected object, got str"),
            ("evidence.source_refs", [{"attachment": "invoice.pdf", "page": "1"}], "evidence.source_refs[0].page expected positive integer or null, got str"),
        ]
        for dotted_path, bad_value, expected in cases:
            with self.subTest(dotted_path=dotted_path):
                payload = _base_payload()
                _set_path(payload, dotted_path, bad_value)

                with self.assertRaises(ExtractionValidationError) as exc:
                    validate_extraction(payload)

                self.assertIn(expected, exc.exception.errors)

    def test_batch_validation_prefixes_nested_path_aware_errors_once(self) -> None:
        payload = _base_payload()
        payload["invoice"]["property_code"] = ["gw34"]

        with self.assertRaises(ExtractionValidationError) as exc:
            validate_extraction_batch(
                {
                    "schema_version": "extraction_batch.v1",
                    "items": [{"item_kind": "attachment", "item_key": "attachment:invoice.pdf", "extraction": payload}],
                }
            )

        self.assertIn(
            "items[0].extraction.invoice.property_code expected string or null, got list",
            exc.exception.errors,
        )

    def test_contract_lint_is_advisory_for_harmless_extra_keys(self) -> None:
        payload = _base_payload()
        payload["observed_facts"]["extra_observed_note"] = "visible but not contractual"

        extraction = validate_extraction(payload)
        lint = lint_extraction_contract(payload)

        self.assertEqual(extraction.document.document_type, "invoice")
        self.assertIn("extraction.observed_facts.extra_observed_note", lint["unknown_keys"])

    def test_contract_lint_reports_close_required_key_spellings(self) -> None:
        payload = _base_payload()
        payload["observed_facts"]["indicates_ben_e_kieth"] = payload["observed_facts"].pop("indicates_ben_e_keith")

        with self.assertRaises(ExtractionValidationError):
            validate_extraction(payload)
        lint = lint_extraction_contract(payload)

        self.assertIn("extraction.observed_facts.indicates_ben_e_keith", lint["missing_required_keys"])
        self.assertIn(
            {"path": "extraction.observed_facts.indicates_ben_e_kieth", "did_you_mean": "indicates_ben_e_keith"},
            lint["close_key_matches"],
        )

    def test_contract_lint_reports_missing_latest_reply_no_action_observed_fact(self) -> None:
        payload = _base_payload()
        del payload["observed_facts"]["latest_reply_indicates_no_ap_action"]

        with self.assertRaises(ExtractionValidationError):
            validate_extraction(payload)
        lint = lint_extraction_contract(payload)

        self.assertIn("extraction.observed_facts.latest_reply_indicates_no_ap_action", lint["missing_required_keys"])

    def test_azure_openai_prompt_includes_validator_required_field_names(self) -> None:
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
            "Extraction Batch Contract Checklist",
            "Return exactly one extraction_batch.v1 JSON object",
            "Every items[].extraction must be a complete extraction.v1 object with all required sections present",
            "extractor, email, document, invoice, property_lookup, business_signals, observed_facts, confidence, and evidence",
            "Required fields must be present even when the value is null, false, 0.0, or []",
            "invoice scalar fields are string-or-null",
            "invoice.amount and all confidence fields are JSON numbers, never quoted strings",
            "Confidence keys are overall, document_type, invoice_fields, property_identity, and business_unit",
            "All observed_facts fields are JSON booleans, never strings or omitted keys",
            "property_lookup.property_code, property_lookup.property_name, tenant, address, suite, city, state, zipcode, and address_candidates are arrays",
            "Allowed document.document_type values are invoice, check_request, statement, account_summary, contract, pay_application, vendor_question, payment_inquiry, past_due_notice, ach_notice, auto_draft_notice, ben_e_keith_notice, lien_release, and unknown",
            "Allowed address candidate labels are deliver_to, ship_to, service_location, site, property, bill_to, and customer_account",
            '"link_only"',
            '"multi_invoice"',
            '"observed_facts"',
            "one item per AP workflow-relevant non-inline attachment",
            "Omit clearly irrelevant attachments from items",
            '"excluded_attachments"',
            '"reason_code": "irrelevant_to_ap_workflow"',
            'reason_code="payment_instruction_support"',
            "Standalone payment-instruction support PDFs",
            "wire instructions, ACH instructions, remittance instructions, and payment portal instructions",
            "Do not emit those standalone support PDFs as actionable items",
            "Do not include standalone payment-instruction support PDFs in any invoice item's evidence.source_attachments or evidence.source_refs",
            "Keep payment instructions embedded inside the invoice PDF as normal invoice evidence",
            "Excluded attachments must not appear in any item's evidence.source_attachments or evidence.source_refs",
            "unsupported, unreadable, image, Office, spreadsheet, or other non-PDF attachment",
            "generic sign photo, legal notice image unrelated to the invoice",
            "Do not include irrelevant attachment filenames in a valid invoice item's evidence.source_attachments",
            "If an unsupported, unreadable, or non-PDF attachment is the claimed invoice evidence",
            "receipt-only",
            "Filename, attachment title, and subject are weak metadata",
            "Receipt.pdf with invoice number, invoice date, terms, line items, tax, and total",
            "Invoice-positive signals include invoice number, INV:, Invoice #",
            "single current payable bill or invoice is present with invoice number, due date, total/current amount due",
            "Do not classify as statement or account_summary solely because labels say Statement Date, Summary of Charges, Previous Balance, or Balance Forward",
            "Set observed_facts.indicates_statement_or_account_summary=true only when statement/account-summary structure dominates over payable invoice structure",
            "If both are present but a single payable invoice is complete, keep document_type=\"invoice\" and mention the conflicting statement labels in evidence.summary",
            "A FiberFirst-style utility/service bill with Statement Date, Summary of Charges, Previous Balance, or Balance Forward labels is still document_type=\"invoice\"",
            "Do not use account_summary solely because a filename or title says Receipt",
            "non-payable receipts, customer statements, aging summaries, balance recaps",
            "payment confirmation, paid receipt, receipt of payment, payment received, paid by, paid on",
            "balance after payment 0.00",
            "no current request to pay, view, download, or retrieve a bill or invoice and no current amount due",
            "generic Receipt.pdf filenames or receipt-labeled documents that contain payable invoice structure",
            "current bills or invoices, link-only bill notices, statements, transaction histories, aging summaries, or balance recaps",
            "If completed-payment evidence and current payable-bill evidence both appear, keep document_type=\"invoice\"",
            "Disambiguate invoices from pay applications",
            "Progress-billing columns or labels such as Contract Amount, Percent Complete, Total Billed, Prior Billed, and Current Billed do not by themselves make a document a pay_application",
            "Westwood-style and other professional-services progress billing documents as document_type=\"invoice\"",
            "visible INVOICE title, Invoice No, invoice date, Total This Invoice, payable amount, remittance copy, remit/payment instructions",
            "observed_facts.indicates_contract_or_pay_application=false",
            "Do not infer pay_application from project billing, progress billing, percent complete, contract amount, prior billed, or current billed terminology alone",
            "Reserve document_type=\"pay_application\" for explicit pay-application or draw-request evidence",
            "Application for Payment, Pay Application, AIA-style payment applications, draw requests",
            "For Ben E Keith related invoice/payment notice emails",
            '"ben_e_keith_notice"',
            '"Ben E. Keith invoice attached"',
            "must not be classified as ordinary invoices",
            "payment-link call-to-action detection as first-pass extraction work",
            "Postgres property lookup will use this output directly",
            "address_candidates",
            '"address_candidates": []',
            "Use address_candidates: [] as the default",
            "Only include address_candidates entries when at least one address component is visible",
            "Never put project names, property names, asset names",
            "leave address_candidates empty unless an actual address is visible",
            "Project, property, tenant, account, alias, or job names without address components must not be address candidates",
            "rank, label, street, city, state, zipcode, normalized_address, source, confidence, and evidence_text",
            "DELIVER TO, SHIP TO, SERVICE LOCATION, SITE, JOB, and LOCATION",
            "deliver_to|ship_to|service_location|site|property|bill_to|customer_account",
            "one compressed display line",
            "source_pages must contain page numbers as integers only",
            "evidence.source_refs",
            '{"attachment":"invoice.pdf","page":1}',
            "Do not include sender, vendor, remit-to, or email signature addresses",
            "Asset codes are short building aliases",
            "GW 31 -> gw31",
            "Asset names are building names",
            "GW 31 / US Conec",
            "Final source-support check before returning JSON",
            "The asset_reference list is not source evidence for this check",
            "Tenant-only source text such as Pei Wei/Chipotle",
            "Return only the final corrected JSON; do not include the self-check",
            "complete normalized address",
            "5201 alliance gateway freeway fort worth tx 76177",
            "SQL treats earlier property_lookup.address values as stronger",
            "Do not set indicates_vendor_question_or_payment_inquiry for routine invoice-payment collection language",
            "no-attachment vendor payment or account questions",
            "answer, confirm, research, reconcile, or explain invoice, payment, or account facts",
            "duplicate payment confirmation",
            "service appointment reminders, maintenance reminders, inspection notices, access notices",
            "generic customer portals, account portals",
            "current bill or invoice action language",
            "can you please confirm",
            "please advise",
            "payment-to-invoice matching",
            "missing backup/support questions",
            "duplicate payments were received",
            "Can you please Confirm?",
            "indicates_wrong_destination",
            '"latest_reply_indicates_no_ap_action": false',
            "email.latest_body_text itself indicates a non-actionable acknowledgement",
            "does not ask a question, report a wrong destination, introduce a new invoice/payment/link action",
            "wrong-recipient escalation",
            "Do not copy invoice_date into invoice.due_date unless the document explicitly presents that date as the payment due date",
            "Extract invoice.due_date only when the source text explicitly labels a concrete calendar date as the due date",
            "Do not populate invoice.due_date for due-on-receipt, due upon receipt, payable upon receipt, net due upon receipt",
            "Do not infer invoice.due_date from invoice date, service date, activity date, posting date, email received date",
            "Do not set observed_facts.current_invoice_is_past_due=true merely because an invoice says payable upon receipt",
            "past due, overdue, due date, payment due, please remit by",
            "current_invoice_is_past_due=true",
            "document_intelligence summaries are Azure Document Intelligence evidence",
            "Prefer successful document_intelligence text",
            "Do not treat Document Intelligence as a routing decision source",
            '"account_summary"',
            '"mentions_merge_or_combine_required"',
            '"amount"',
            '"business_unit_code"',
            '"invoice_fields"',
            '"property_identity"',
            '"summary"',
            "Type Contract Rules",
            "invoice.property_code and invoice.property_name are string or null, never arrays",
            "property_lookup.property_code and property_lookup.property_name are arrays of strings",
            '{"invoice":{"property_code":["gw34"]}}',
            '{"invoice":{"property_code":"gw34"}}',
            '{"confidence":{"overall":"0.92"}}',
            '{"confidence":{"overall":0.92}}',
        ]
        for field_name in required_field_names:
            self.assertIn(field_name, prompt)

        self.assertLess(prompt.index("Extraction Batch Contract Checklist"), prompt.index("Thread-aware email body handling"))

    def test_azure_openai_prompt_includes_asset_reference_for_normalization_only(self) -> None:
        prompt = _prompt(
            ParsedMsg(
                subject="Invoice for 3202 Alliance Gateway 34 Shell Bldg.",
                sender_email="vendor@example.com",
                sender_name="Vendor",
                received_at=None,
                body_text="Please see invoice for 3202 Alliance Gateway 34 Shell Bldg.",
                transport_headers=None,
                attachments=(),
                metadata={},
            ),
            [],
            asset_reference_rows=[
                {
                    "asset_name": "Alliance Gateway 34",
                    "asset_alias": "GW34",
                    "asset_type": "industrial",
                    "address": "3202 Alliance Gateway Freeway, Fort Worth, TX 76177",
                }
            ],
        )

        self.assertIn('"asset_reference"', prompt)
        self.assertIn('"asset_name": "Alliance Gateway 34"', prompt)
        self.assertIn('"asset_alias": "GW34"', prompt)
        self.assertIn('"asset_type": "industrial"', prompt)
        self.assertIn('"address": "3202 Alliance Gateway Freeway, Fort Worth, TX 76177"', prompt)
        self.assertIn("read-only normalization context", prompt)
        self.assertIn("not routing authority", prompt)
        self.assertIn("Use asset_type only to normalize source-visible property text", prompt)
        self.assertIn("Prefer Project, Job, Site, Service Location", prompt)
        self.assertIn('property_lookup.property_name=["alliance gateway 34"]', prompt)
        self.assertIn('property_lookup.property_code=["gw34"]', prompt)
        self.assertIn("If visible source text does not match asset_reference", prompt)
        self.assertIn("If asset_reference contains Hillwood Commons II / HWC2", prompt)
        self.assertIn("Do not convert visible Hillwood Commons II to Heritage Commons II / HC2", prompt)
        self.assertIn("Alliance Gateway shorthand such as AG31, AG 31, or AG-31", prompt)
        self.assertIn("normalize to the listed asset_reference asset_name and configured asset_alias", prompt)
        self.assertIn("Final source-support check before returning JSON", prompt)
        self.assertIn("verify every invoice.property_code, invoice.property_name, property_lookup.property_code, and property_lookup.property_name", prompt)
        self.assertIn("email subject, email body, selected attachment text, or attachment metadata", prompt)
        self.assertIn("The asset_reference list is not source evidence for this check", prompt)
        self.assertIn("only inferred from asset_reference", prompt)
        self.assertIn("remove it from property_lookup arrays and set invoice.property_code or invoice.property_name to null", prompt)
        self.assertIn("Tenant-only source text such as Pei Wei/Chipotle", prompt)
        self.assertIn("property_lookup.tenant or business_signals.possible_property_aliases", prompt)
        self.assertIn("source text says Pei Wei/Chipotle 2901 Heritage Trace Pkwy", prompt)
        self.assertIn("asset_reference contains GW31 / Alliance Gateway 31", prompt)
        self.assertIn("must not include gw31 or alliance gateway 31", prompt)
        self.assertIn("keep visible tenant and address evidence only", prompt)
        self.assertIn("source text visibly says GW31, GW 31, AG31, or Alliance Gateway 31", prompt)
        self.assertIn('property_lookup.property_code=["gw31"]', prompt)
        self.assertIn('property_lookup.property_name=["alliance gateway 31"]', prompt)
        self.assertIn("lower confidence.property_identity and set observed_facts.has_conflicting_signals=true", prompt)
        self.assertIn("Return only the final corrected JSON; do not include the self-check", prompt)

    def test_contract_repair_prompt_tells_llm_to_remove_non_address_candidates(self) -> None:
        repair_prompt = contract_repair_prompt(
            original_prompt="Original extraction prompt",
            invalid_response='{"property_lookup":{"address_candidates":[{"rank":1,"label":"property","evidence_text":"Project Paloma Villas"}]}}',
            errors=["items[0].extraction.property_lookup.address_candidates[0] must include at least one address component"],
            contract_name="extraction_batch.v1",
            lint_findings={"unknown_keys": ["items[0].extraction.observed_facts.indicates_ben_e_kieth"]},
        )

        self.assertIn("remove that address_candidates object", repair_prompt)
        self.assertIn("Move visible non-address identity text", repair_prompt)
        self.assertIn("Do not invent address components", repair_prompt)
        self.assertIn("Canonical field checklist", repair_prompt)
        self.assertIn("observed_facts keys", repair_prompt)
        self.assertIn("latest_reply_indicates_no_ap_action", repair_prompt)
        self.assertIn("confidence keys", repair_prompt)
        self.assertIn("Advisory contract lint findings", repair_prompt)
        self.assertIn("indicates_ben_e_kieth", repair_prompt)
        self.assertIn("Type Contract Rules", repair_prompt)
        self.assertIn("invoice.property_code and invoice.property_name are string or null, never arrays", repair_prompt)
        self.assertIn("property_lookup.property_code and property_lookup.property_name are arrays of strings", repair_prompt)
        self.assertIn("items[0].extraction.property_lookup.address_candidates[0] must include at least one address component", repair_prompt)
        self.assertIn("Compact extraction_batch.v1 skeleton", repair_prompt)
        self.assertIn('"schema_version": "extraction_batch.v1"', repair_prompt)
        self.assertIn('"extraction": {"schema_version": "extraction.v1"', repair_prompt)
        self.assertIn("Every items[].extraction must be a complete extraction.v1 object with all required sections present", repair_prompt)
        self.assertIn("invoice.amount and all confidence fields are JSON numbers, never quoted strings", repair_prompt)
        self.assertIn("All observed_facts fields are JSON booleans, never strings or omitted keys", repair_prompt)
        self.assertIn("Allowed address candidate labels are deliver_to, ship_to, service_location, site, property, bill_to, and customer_account", repair_prompt)

    def test_azure_openai_prompt_can_normalize_visible_retail_project_text(self) -> None:
        prompt = _prompt(
            ParsedMsg(
                subject="Invoice for Project Harvest Retail Building A",
                sender_email="vendor@example.com",
                sender_name="Vendor",
                received_at=None,
                body_text="Project: Harvest Retail Building A\nBill To: Hillwood",
                transport_headers=None,
                attachments=(),
                metadata={},
            ),
            [],
            asset_reference_rows=[
                {"asset_name": "Harvest Town Center", "asset_alias": "HTC", "asset_type": "Retail", "address": None},
                {"asset_name": "Harvest House", "asset_alias": "HH", "asset_type": "Multifamily", "address": None},
            ],
        )

        self.assertIn('"asset_name": "Harvest Town Center"', prompt)
        self.assertIn('"asset_type": "Retail"', prompt)
        self.assertIn("Project Harvest Retail Building A", prompt)
        self.assertIn("Use asset_type only to normalize source-visible property text", prompt)
        self.assertIn("Prefer Project, Job, Site, Service Location", prompt)

    def test_azure_openai_prompt_preserves_visible_hillwood_commons_name(self) -> None:
        prompt = _prompt(
            ParsedMsg(
                subject="Invoice for Hillwood Commons II",
                sender_email="vendor@example.com",
                sender_name="Vendor",
                received_at=None,
                body_text="Property: Hillwood Commons II\nBill To: Heritage Commons II",
                transport_headers=None,
                attachments=(),
                metadata={},
            ),
            [],
            asset_reference_rows=[
                {"asset_name": "Hillwood Commons II", "asset_alias": "HWC2", "asset_type": "industrial", "address": None},
                {"asset_name": "Heritage Commons II", "asset_alias": "HC2", "asset_type": "industrial", "address": None},
            ],
        )

        self.assertIn('"asset_name": "Hillwood Commons II"', prompt)
        self.assertIn('"asset_alias": "HWC2"', prompt)
        self.assertIn('"asset_name": "Heritage Commons II"', prompt)
        self.assertIn('"asset_alias": "HC2"', prompt)
        self.assertIn("If asset_reference contains Hillwood Commons II / HWC2", prompt)
        self.assertIn("Do not convert visible Hillwood Commons II to Heritage Commons II / HC2", prompt)
        self.assertIn('property_lookup.property_name=["hillwood commons ii"]', prompt)
        self.assertIn('property_lookup.property_code=["hwc2"]', prompt)

    def test_azure_openai_prompt_keeps_hc2_exact_code_behavior_when_visible(self) -> None:
        prompt = _prompt(
            ParsedMsg(
                subject="Invoice for HC-2",
                sender_email="vendor@example.com",
                sender_name="Vendor",
                received_at=None,
                body_text="Property Code: HC-2\nProperty: Heritage Commons II",
                transport_headers=None,
                attachments=(),
                metadata={},
            ),
            [],
            asset_reference_rows=[
                {"asset_name": "Heritage Commons II", "asset_alias": "HC2", "asset_type": "industrial", "address": None}
            ],
        )

        self.assertIn('"asset_name": "Heritage Commons II"', prompt)
        self.assertIn('"asset_alias": "HC2"', prompt)
        self.assertIn("compact property-code formatting such as HC-2 or HC 2 to hc2", prompt)
        self.assertIn("unless the source visibly says Heritage Commons II or HC2", prompt)

    def test_azure_openai_prompt_includes_separate_backup_guidance(self) -> None:
        prompt = _prompt(
            ParsedMsg(
                subject="Invoice with ticket",
                sender_email="vendor@example.com",
                sender_name="Vendor",
                received_at=None,
                body_text="Invoice attached with signed ticket backup.",
                transport_headers=None,
                attachments=(),
                metadata={},
            ),
            [],
        )

        self.assertIn("mentions_separate_backup_document=true", prompt)
        self.assertIn("lien waivers", prompt)
        self.assertIn("work orders", prompt)
        self.assertIn("field tickets", prompt)
        self.assertIn("time-entry detail reports", prompt)
        self.assertIn("The supporting document does not need to say \"invoice\"", prompt)
        self.assertIn("shared vendor, project/job, customer, location, invoice number, work order number", prompt)
        self.assertIn("This is not a duplicate-invoice scenario", prompt)
        self.assertIn("does not require explicit merge or combine instructions", prompt)
        self.assertIn("Return separate AP-relevant supporting documents as their own batch items", prompt)
        self.assertIn("only when there is a distinct supporting-document item tied to the invoice", prompt)
        self.assertIn("Do not set mentions_separate_backup_document=true for embedded invoice pages", prompt)
        self.assertIn("inline images, logos, decorative images, or attachments excluded as irrelevant", prompt)


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
            "bill_to_name_line_1": None,
            "bill_to_name_line_2": None,
            "bill_to_street_address": None,
            "bill_to_suite": None,
            "bill_to_city": None,
            "bill_to_state": None,
            "bill_to_zip_code": None,
            "property_code": "hw1",
            "property_name": None,
            "service_address": None,
        },
        "property_lookup": {
            "property_code": None,
            "property_name": None,
            "tenant": None,
            "address": None,
            "suite": None,
            "city": None,
            "state": None,
            "zipcode": None,
        },
        "business_signals": {"business_unit_code": "PROP", "possible_property_aliases": [], "subject_instruction_hint": None},
        "observed_facts": {
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
            "indicates_ach_or_auto_draft": False,
            "indicates_ben_e_keith": False,
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
        "evidence": {"summary": "fixture", "source_attachments": ["invoice.pdf"], "source_pages": []},
    }


def _set_path(payload: dict, dotted_path: str, value: object) -> None:
    target = payload
    parts = dotted_path.split(".")
    for part in parts[:-1]:
        target = target[part]
    target[parts[-1]] = value
