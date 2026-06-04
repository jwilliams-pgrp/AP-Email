from __future__ import annotations

import unittest
import json
from decimal import Decimal
from pathlib import Path

from ap_automation.repositories.postgres import (
    PostgresRepository,
    _normalize_decision_outcome,
    _normalize_property_value,
    _normalize_suite_value,
    _normalize_zipcode_value,
    _property_query_signals,
)
from ap_automation.models.extraction import validate_extraction


class PropertyValueNormalizationTests(unittest.TestCase):
    def test_decision_outcome_normalization_keeps_canonical_value(self) -> None:
        self.assertEqual(_normalize_decision_outcome("ESCALATE"), "ESCALATE")
        self.assertEqual(_normalize_decision_outcome("AUTO"), "AUTO")

    def test_normalizes_parkway_variants_to_same_value(self) -> None:
        a = _normalize_property_value("14372 Heritage Parkway, Fort Worth, TX")
        b = _normalize_property_value("14372 Heritage PWKY Fort Worth TX")
        c = _normalize_property_value("14372 Heritage Pkwy Fort Worth TX")

        self.assertEqual(a, b)
        self.assertEqual(a, c)

    def test_normalizes_property_code_variants_to_same_value(self) -> None:
        a = _normalize_property_value("HC2")
        b = _normalize_property_value("HC-2")
        c = _normalize_property_value("HC 2")

        self.assertEqual(a, b)
        self.assertEqual(a, c)

    def test_expands_address_directional_and_city_abbreviations(self) -> None:
        self.assertEqual(
            _normalize_property_value(" 9800 Hillwood Pkwy, Ft Worth "),
            "9800 hillwood parkway fort worth",
        )
        self.assertEqual(_normalize_property_value("308 E. 6th St."), "308 east 6th street")

    def test_normalizes_suite_and_zipcode_values(self) -> None:
        self.assertEqual(_normalize_suite_value("STE #300"), "300")
        self.assertEqual(_normalize_suite_value("Suite 1200"), "1200")
        self.assertEqual(_normalize_zipcode_value("76177-1234"), "76177")

    def test_property_query_uses_extracted_normalized_candidates_directly(self) -> None:
        payload = _base_payload()
        payload["invoice"].update(
            {
                "bill_to": "HILLWOOD ALLIANCE GROUP 9800 HILLWOOD PWKY STE 300 FT WORTH, TX 76177",
                "bill_to_street_address": "9800 HILLWOOD PWKY",
                "bill_to_suite": "STE 300",
                "bill_to_zip_code": "76177-1234",
                "service_address": "308 E. 6th St Fort Worth, TX 76102",
            }
        )
        payload["property_lookup"] = {
            "property_code": ["hc2"],
            "property_name": [
                "hillwood alliance group",
                "hillwood alliance group 9800 hillwood parkway 300 fort worth tx 76177",
            ],
            "tenant": ["hillwood alliance group"],
            "address": [
                "308 east 6th street",
                "308 east 6th street fort worth tx 76102",
                "9800 hillwood parkway",
                "hillwood alliance group 9800 hillwood parkway 300 fort worth tx 76177",
            ],
            "suite": ["300"],
            "city": ["fort worth"],
            "state": ["tx"],
            "zipcode": ["76102", "76177"],
        }

        raw_signals, query_values = _property_query_signals(validate_extraction(payload))

        self.assertEqual(raw_signals["property_lookup"]["address"][0], "308 east 6th street")
        self.assertEqual(query_values["property_codes"], ["hc2"])
        self.assertEqual(
            query_values["property_names"],
            ["hillwood alliance group", "hillwood alliance group 9800 hillwood parkway 300 fort worth tx 76177"],
        )
        self.assertEqual(query_values["tenants"], ["hillwood alliance group"])
        self.assertEqual(
            query_values["addresses"],
            [
                "308 east 6th street",
                "308 east 6th street fort worth tx 76102",
                "9800 hillwood parkway",
                "hillwood alliance group 9800 hillwood parkway 300 fort worth tx 76177",
            ],
        )
        self.assertEqual(query_values["suites"], ["300"])
        self.assertEqual(query_values["zipcodes"], ["76102", "76177"])

    def test_property_candidate_sql_scores_address_components(self) -> None:
        repo = PostgresRepository.__new__(PostgresRepository)
        row = {
            "asset_id": "00000000-0000-0000-0000-000000000001",
            "asset_alias": "HW1",
            "asset_name": "Hillwood Alliance Group",
            "ownership": "hillwood_owned",
            "asset_type": "industrial",
            "market_name": "Fort Worth",
            "market_area": "North Fort Worth",
            "destination_code": "MICHELE_FELLERS",
            "destination_active": True,
            "tenants": ["Hillwood Alliance Group"],
            "address": "9800 Hillwood Parkway",
            "full_address_line": "9800 hillwood parkway fort worth tx 76177",
            "candidate_text": "9800 hillwood parkway fort worth tx 76177",
            "matched_column": "address_components",
            "property_code_score": 0.0,
            "name_score": 0.6,
            "street_score": 1.0,
            "suite_score": 1.0,
            "city_score": 1.0,
            "state_score": 1.0,
            "zipcode_score": 1.0,
            "address_score": 1.0,
            "score": 1.0,
        }
        connection = _FakeConnection([row])
        repo._connect = lambda: connection  # type: ignore[attr-defined]

        candidates, audit = repo._retrieve_property_candidates(
            {
                "property_codes": [],
                "property_names": ["hillwood alliance group"],
                "tenants": [],
                "addresses": ["9800 hillwood parkway"],
                "suites": ["300"],
                "cities": ["fort worth"],
                "states": ["tx"],
                "zipcodes": ["76177"],
            },
            top_n=5,
        )

        self.assertEqual(candidates[0].matched_column, "address_components")
        self.assertEqual(candidates[0].similarity_score, 1.0)
        self.assertIn("street_score * 8.0", connection.sql)
        self.assertIn("with ordinality as raw(value, ord)", connection.sql)
        self.assertIn("when raw.ord = 1 then 1.0", connection.sql)
        self.assertIn("when raw.ord = 2 then 0.75", connection.sql)
        self.assertNotIn("suite_score * 1.5", connection.sql)
        self.assertNotIn("or norm_suite = ''", connection.sql)
        self.assertIn("from vw_asset_lookup a", connection.sql)
        self.assertNotIn("a.business_unit_code", connection.sql)
        self.assertIn("null::text as business_unit_code", connection.sql)
        self.assertIn("a.asset_lookup_id as asset_id", connection.sql)
        self.assertIn("a.asset_source", connection.sql)
        self.assertIn("parsed_candidates as", connection.sql)
        self.assertIn("split_part(c.address, ',', 1)", connection.sql)
        self.assertIn("when 'texas' then 'tx'", connection.sql)
        self.assertIn("substring(c.address from '([0-9]{5})(?:-[0-9]{4})?\\D*$')", connection.sql)
        self.assertIn("where street_score >= 0.45", connection.sql.lower())
        self.assertNotIn("full_address_score", connection.sql)
        returned = audit["returned_payload"][0]
        self.assertEqual(returned["street_score"], 1.0)
        self.assertEqual(returned["address_score"], 1.0)

    def test_property_candidate_sql_treats_address_order_as_priority(self) -> None:
        repo = PostgresRepository.__new__(PostgresRepository)
        connection = _FakeConnection([])
        repo._connect = lambda: connection  # type: ignore[attr-defined]

        repo._retrieve_property_candidates(
            {
                "property_codes": [],
                "property_names": ["gateway 62"],
                "tenants": [],
                "addresses": [
                    "400 patriot parkway roanoke tx 76262",
                    "9800 hillwood parkway fort worth tx 76177",
                ],
                "suites": [],
                "cities": ["roanoke", "fort worth"],
                "states": ["tx"],
                "zipcodes": ["76262", "76177"],
            },
            top_n=5,
        )

        self.assertEqual(connection.params[3][0], "400 patriot parkway roanoke tx 76262")
        self.assertIn("order by x.ord", connection.sql)
        self.assertNotIn("%(addresses)s", connection.sql)

    def test_property_candidate_sql_downweights_non_primary_addresses(self) -> None:
        repo = PostgresRepository.__new__(PostgresRepository)
        connection = _FakeConnection([])
        repo._connect = lambda: connection  # type: ignore[attr-defined]

        repo._retrieve_property_candidates(
            {
                "property_codes": ["gw62"],
                "property_names": ["gateway 62"],
                "tenants": [],
                "addresses": [
                    "400 patriot parkway",
                    "9800 hillwood parkway",
                ],
                "suites": ["300"],
                "cities": ["roanoke", "fort worth"],
                "states": ["tx"],
                "zipcodes": ["76262", "76177"],
            },
            top_n=5,
        )

        self.assertIn("when raw.ord = 2 then 0.75", connection.sql)
        self.assertIn("else 0.65", connection.sql)

    def test_property_candidate_sql_scores_structured_address_candidates_as_unit(self) -> None:
        repo = PostgresRepository.__new__(PostgresRepository)
        connection = _FakeConnection([])
        repo._connect = lambda: connection  # type: ignore[attr-defined]

        repo._retrieve_property_candidates(
            {
                "property_codes": [],
                "property_names": [],
                "tenants": [],
                "addresses": [
                    "2451 westlake parkway",
                    "2451 westlake parkway westlake tx 76262",
                    "9800 hillwood parkway",
                    "9800 hillwood parkway fort worth tx 76177",
                ],
                "suites": [],
                "cities": ["westlake", "fort worth"],
                "states": ["tx"],
                "zipcodes": ["76262", "76177"],
                "address_candidates": [
                    {
                        "rank": 1,
                        "label": "deliver_to",
                        "street": "2451 westlake parkway",
                        "city": "westlake",
                        "state": "tx",
                        "zipcode": "76262",
                        "normalized_address": "2451 westlake parkway westlake tx 76262",
                    },
                    {
                        "rank": 2,
                        "label": "bill_to",
                        "street": "9800 hillwood parkway",
                        "city": "fort worth",
                        "state": "tx",
                        "zipcode": "76177",
                        "normalized_address": "9800 hillwood parkway fort worth tx 76177",
                    },
                ],
            },
            top_n=5,
        )

        self.assertIn("%s::jsonb as address_candidates", connection.sql)
        self.assertIn("normalized_address_candidates as", connection.sql)
        self.assertIn("structured_address_score", connection.sql)
        self.assertIn("when lower(coalesce(candidate->>'label', '')) = 'bill_to' then 0.70", connection.sql)
        self.assertIn("when structured_address_score > 0 then structured_address_score * 11.25", connection.sql)
        address_candidate_payload = json.loads(connection.params[8])
        self.assertEqual(address_candidate_payload[0]["rank"], 1)
        self.assertEqual(address_candidate_payload[1]["label"], "bill_to")

    def test_duplicate_status_matches_vendor_invoice_number_and_invoice_date(self) -> None:
        repo = PostgresRepository.__new__(PostgresRepository)
        connection = _FakeConnection([{"exists": 1}])
        repo._connect = lambda: connection  # type: ignore[attr-defined]
        payload = _base_payload()
        payload["invoice"].update({"vendor_name": "  Test   Vendor  ", "invoice_number": " INV   100 ", "invoice_date": "2026-05-01", "amount": 120.50})
        extraction = validate_extraction(payload)

        status = repo.find_duplicate_status(extraction, "current-key")

        self.assertEqual(status, "suspected")
        self.assertIn("em.idempotency_key <> %s", connection.sql)
        self.assertIn("regexp_replace(lower(coalesce(i.vendor_name, ''))", connection.sql)
        self.assertIn("regexp_replace(lower(coalesce(i.invoice_number, ''))", connection.sql)
        self.assertIn("i.invoice_date = %s", connection.sql)
        self.assertNotIn("i.amount", connection.sql)
        self.assertEqual(connection.params, ("current-key", "test vendor", "inv 100", extraction.invoice.invoice_date))

    def test_duplicate_status_returns_none_when_invoice_date_does_not_match(self) -> None:
        repo = PostgresRepository.__new__(PostgresRepository)
        connection = _FakeConnection([])
        repo._connect = lambda: connection  # type: ignore[attr-defined]
        payload = _base_payload()
        payload["invoice"].update({"vendor_name": "Vendor", "invoice_number": "100", "invoice_date": "2026-05-02"})
        extraction = validate_extraction(payload)

        self.assertIsNone(repo.find_duplicate_status(extraction, "current-key"))
        self.assertEqual(connection.params[3], extraction.invoice.invoice_date)

    def test_duplicate_status_requires_invoice_number(self) -> None:
        repo = PostgresRepository.__new__(PostgresRepository)
        connection = _FakeConnection([{"exists": 1}])
        repo._connect = lambda: connection  # type: ignore[attr-defined]
        payload = _base_payload()
        payload["invoice"].update({"vendor_name": "Vendor", "invoice_number": None, "invoice_date": "2026-05-01"})

        self.assertIsNone(repo.find_duplicate_status(validate_extraction(payload), "current-key"))
        self.assertEqual(connection.sql, "")

    def test_duplicate_status_excludes_same_idempotency_key(self) -> None:
        repo = PostgresRepository.__new__(PostgresRepository)
        connection = _FakeConnection([])
        repo._connect = lambda: connection  # type: ignore[attr-defined]
        payload = _base_payload()
        payload["invoice"].update({"vendor_name": "Vendor", "invoice_number": "100", "invoice_date": "2026-05-01"})

        self.assertIsNone(repo.find_duplicate_status(validate_extraction(payload), "same-key"))
        self.assertEqual(connection.params[0], "same-key")

    def test_property_candidate_sql_scores_against_parsed_asset_address(self) -> None:
        repo = PostgresRepository.__new__(PostgresRepository)
        connection = _FakeConnection([])
        repo._connect = lambda: connection  # type: ignore[attr-defined]

        repo._retrieve_property_candidates(
            {
                "property_codes": [],
                "property_names": [],
                "tenants": [],
                "addresses": ["5201 alliance gateway freeway"],
                "suites": [],
                "cities": ["fort worth"],
                "states": ["tx"],
                "zipcodes": ["76177"],
            },
            top_n=5,
        )

        self.assertIn("from parsed_candidates c", connection.sql)
        self.assertIn("when c.norm_address = q.value then 1.0", connection.sql)
        self.assertIn("when c.norm_city = q.value then 1.0", connection.sql)
        self.assertIn("when c.norm_state = q.value then 1.0 else 0.0", connection.sql)
        self.assertIn("when c.norm_zipcode = q.value then 1.0 else 0.0", connection.sql)

    def test_property_candidate_sql_compacts_property_code_variants(self) -> None:
        repo = PostgresRepository.__new__(PostgresRepository)
        connection = _FakeConnection([])
        repo._connect = lambda: connection  # type: ignore[attr-defined]

        repo._retrieve_property_candidates(
            {
                "property_codes": ["gw 31"],
                "property_names": [],
                "tenants": [],
                "addresses": [],
                "suites": [],
                "cities": [],
                "states": [],
                "zipcodes": [],
            },
            top_n=5,
        )

        self.assertEqual(connection.params[0], ["gw 31"])
        self.assertIn("regexp_replace(trim(x.value), '[^a-zA-Z0-9]+', '', 'g')", connection.sql)
        self.assertIn("regexp_replace(trim(coalesce(a.asset_alias, '')), '[^a-zA-Z0-9]+', '', 'g')", connection.sql)
        self.assertIn("when property_code_score = 1.0", connection.sql)

    def test_property_candidate_audit_includes_asset_lookup_identity(self) -> None:
        repo = PostgresRepository.__new__(PostgresRepository)
        row = {
            "asset_id": "asset_custom:7",
            "asset_source": "asset_custom",
            "asset_lookup_id": "asset_custom:7",
            "asset_alias": "SPECIAL",
            "asset_name": "Special Address",
            "ownership": None,
            "asset_type": None,
            "market_name": None,
            "market_area": None,
            "destination_code": "ESCALATE_SPECIAL_ADDRESS",
            "destination_active": True,
            "tenants": None,
            "address": "1 Special Way",
            "full_address_line": "1 special way",
            "candidate_text": "1 special way",
            "matched_column": "property_code",
            "property_code_score": 1.0,
            "name_score": 0.0,
            "street_score": 0.0,
            "suite_score": 0.0,
            "city_score": 0.0,
            "state_score": 0.0,
            "zipcode_score": 0.0,
            "address_score": 0.0,
            "structured_address_score": 0.0,
            "score": 1.0,
        }
        connection = _FakeConnection([row])
        repo._connect = lambda: connection  # type: ignore[attr-defined]

        candidates, _audit = repo._retrieve_property_candidates(
            {
                "property_codes": ["special"],
                "property_names": [],
                "tenants": [],
                "addresses": [],
                "suites": [],
                "cities": [],
                "states": [],
                "zipcodes": [],
            },
            top_n=5,
        )

        audit_row = candidates[0].to_audit_dict()
        self.assertEqual(audit_row["asset_source"], "asset_custom")
        self.assertEqual(audit_row["asset_lookup_id"], "asset_custom:7")
        self.assertEqual(audit_row["destination_code"], "ESCALATE_SPECIAL_ADDRESS")

    def test_audit_step_serializes_decimal_scores(self) -> None:
        repo = PostgresRepository.__new__(PostgresRepository)
        connection = _FakeConnection([])
        repo._connect = lambda: connection  # type: ignore[attr-defined]

        repo.add_audit_step(
            "run-1",
            "ROUTING",
            {},
            {
                "property_lookup": {
                    "returned_payload": [
                        {
                            "property_code": "HW1",
                            "state_score": Decimal("1.0"),
                            "score": Decimal("0.875"),
                        }
                    ]
                }
            },
        )

        output_summary = json.loads(connection.params[4])
        returned = output_summary["property_lookup"]["returned_payload"][0]
        self.assertEqual(returned["state_score"], 1)
        self.assertEqual(returned["score"], 0.875)

    def test_save_llm_interaction_links_latest_audit_step_and_extraction(self) -> None:
        repo = PostgresRepository.__new__(PostgresRepository)
        connection = _FakeConnection([])
        repo._connect = lambda: connection  # type: ignore[attr-defined]

        repo.save_llm_interaction(
            "email-1",
            "run-1",
            {
                "interaction_type": "extraction",
                "provider": "azure_openai",
                "model_name": "gpt-test",
                "deployment_name": "gpt-test",
                "prompt_template_name": "azure_msg_extraction",
                "prompt_version": "azure_msg_extraction.v1",
                "prompt_artifact_path": "local/audit/prompts/run-1.txt",
                "request_parameters": {"response_format": "json_object"},
                "raw_usage": {},
                "status": "completed",
            },
        )

        self.assertIn("insert into llm_interactions", connection.sql)
        self.assertIn("where run_id = %s and step_type = 'LLM_EXTRACTION'", connection.sql)
        self.assertIn("select extraction_id from extractions", connection.sql)
        self.assertEqual(connection.params[0], "email-1")
        self.assertEqual(connection.params[1], "run-1")
        self.assertEqual(connection.params[4], "extraction")
        self.assertEqual(connection.params[5], "azure_openai")

    def test_reload_escalate_folder_items_deletes_queue_and_reloads_current_messages(self) -> None:
        repo = PostgresRepository.__new__(PostgresRepository)
        connection = _FakeConnection([{"email_id": "email-1"}])
        repo._connect = lambda: connection  # type: ignore[attr-defined]

        repo.reload_escalate_folder_items(
            [
                {
                    "email_metadata": {
                        "source_system": "graph_mailbox",
                        "source_message_id": "msg-1",
                        "idempotency_key": "graph_mailbox:msg-1",
                        "subject": "Needs escalation",
                        "sender_email": "sender@example.com",
                        "received_at": None,
                        "raw_storage_path": None,
                        "office_web_link": "https://outlook.office.com/mail/ESCALATE/id/msg-1",
                        "metadata": {"graph_message_id": "msg-1"},
                    },
                    "source_message_id": "msg-1",
                    "reason": "Message is currently in the ESCALATE folder.",
                    "office_web_link": "https://outlook.office.com/mail/ESCALATE/id/msg-1",
                }
            ]
        )

        self.assertEqual(connection.statements[0][0], "delete from escalate_queue")
        self.assertIn("insert into emails", connection.statements[1][0])
        self.assertIn("insert into escalate_queue", connection.statements[2][0])
        self.assertEqual(connection.statements[2][1], ("email-1", "msg-1", "Message is currently in the ESCALATE folder.", "https://outlook.office.com/mail/ESCALATE/id/msg-1"))

    def test_seed_includes_informational_property_notice_rule(self) -> None:
        seed_sql = Path("db/seed.sql").read_text(encoding="utf-8")

        self.assertIn("'appointment_informational_notice'", seed_sql)
        self.assertIn("'appointment_informational_notice', 'fact_key', '\"indicates_informational_appointment_notice\"'::jsonb", seed_sql)
        self.assertIn("'appointment_informational_notice', 'document_types', '[\"unknown\"]'::jsonb", seed_sql)
        self.assertIn("'appointment_informational_notice', 'forbid_source_attachments', 'true'::jsonb", seed_sql)
        self.assertIn("'informational_property_notice'", seed_sql)
        self.assertIn("'informational_property_notice', 'document_types', '[\"unknown\"]'::jsonb", seed_sql)
        self.assertIn("'informational_property_notice', 'blocked_flags'", seed_sql)
        self.assertIn('"link_only_invoice"', seed_sql)
        self.assertIn('"vendor_inquiry"', seed_sql)
        self.assertIn('"past_due"', seed_sql)
        self.assertIn('"contract_or_pay_application"', seed_sql)
        self.assertIn('"conflicting_signals"', seed_sql)
        self.assertIn('"low_text_quality"', seed_sql)
        self.assertIn("'hard_unmatched_building', 'document_types', '[\"invoice\", \"unknown\"]'::jsonb", seed_sql)
        self.assertLess(seed_sql.index("'appointment_informational_notice'"), seed_sql.index("'informational_property_notice'"))
        self.assertLess(seed_sql.index("'informational_property_notice'"), seed_sql.index("'property_routing_match'"))

    def test_standalone_sql_adds_appointment_informational_notice_rule(self) -> None:
        targeted_sql = Path("db/add-appointment-informational-no-action.sql").read_text(encoding="utf-8")

        self.assertIn("'appointment_informational_notice'", targeted_sql)
        self.assertIn("'NO_ACTION'", targeted_sql)
        self.assertIn('"indicates_informational_appointment_notice"', targeted_sql)

    def test_seed_includes_alc_escalation_policy(self) -> None:
        seed_sql = Path("db/seed.sql").read_text(encoding="utf-8")

        self.assertIn("'ESCALATE_ALC', 'ALC', null, 'ESCALATE'", seed_sql)
        self.assertIn("'alc_escalation'", seed_sql)
        self.assertIn("'alc_escalation', 'business_unit_codes', '[\"ALC\"]'::jsonb", seed_sql)
        self.assertNotIn("'alc_escalation', 'asset_types'", seed_sql)
        self.assertIn("'alc_escalation', 'text_phrases', '[\"Alliance Landscape\", \"Alliance Landscaping\"]'::jsonb", seed_sql)
        self.assertIn("'alc_escalation', 'standalone_terms', '[\"ALC\"]'::jsonb", seed_sql)
        self.assertIn("'bill_to_alc', 'ALC bill-to routes to Medius ALC', 600, false", seed_sql)
        self.assertIn("'bill_to_mf', 'Multifamily bill-to routes to Medius MF', 610, false", seed_sql)
        self.assertLess(seed_sql.index("'hard_no_action_email_pattern'"), seed_sql.index("'alc_escalation'"))
        self.assertLess(seed_sql.index("'alc_escalation'"), seed_sql.index("'amount_over_threshold'"))

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
            "indicates_informational_appointment_notice": False,
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


class _FakeConnection:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.sql = ""
        self.params = ()
        self.statements = []

    def __enter__(self) -> "_FakeConnection":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def execute(self, sql: str, params: object = ()) -> "_FakeConnection":
        self.sql = sql
        self.params = params
        self.statements.append((sql.strip(), params))
        return self

    def fetchall(self) -> list[dict]:
        return self.rows

    def fetchone(self) -> dict | None:
        return self.rows[0] if self.rows else None
