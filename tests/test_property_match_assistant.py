from __future__ import annotations

import json
import unittest
from typing import Any

from ap_automation.agents.property_match_assistant import PropertyMatchAssistant
from ap_automation.models.extraction import validate_extraction
from test_decision_engine import _payload


class PropertyMatchAssistantTests(unittest.TestCase):
    def test_returns_candidate_from_valid_interpretation(self) -> None:
        assistant = PropertyMatchAssistant(FakeLlmExtractor(_interpretation_payload("asset-HC2")))

        suggestion = assistant.suggest(validate_extraction(_payload(property_code=None)), [_alias("HC2")])

        self.assertIsNotNone(suggestion)
        assert suggestion is not None
        self.assertEqual(suggestion.candidate_asset_ids, ("asset-HC2",))
        self.assertEqual(suggestion.interpretation.schema_version, "llm_interpretation.v1")

    def test_ignores_invented_candidate(self) -> None:
        assistant = PropertyMatchAssistant(FakeLlmExtractor(_interpretation_payload("asset-MADE-UP")))

        suggestion = assistant.suggest(validate_extraction(_payload(property_code=None)), [_alias("HC2")])

        self.assertIsNone(suggestion)

    def test_ignores_candidate_without_evidence(self) -> None:
        payload = _interpretation_payload("asset-HC2")
        payload["candidate_property_matches"][0]["evidence"] = []
        assistant = PropertyMatchAssistant(FakeLlmExtractor(payload))

        suggestion = assistant.suggest(validate_extraction(_payload(property_code=None)), [_alias("HC2")])

        self.assertIsNone(suggestion)

    def test_prompt_instructs_reviewer_to_disambiguate_shared_address_by_name(self) -> None:
        assistant = PropertyMatchAssistant(FakeLlmExtractor(_interpretation_payload("asset-HC2")))

        assistant.suggest(
            validate_extraction(
                _payload(
                    property_code=None,
                    property_name="Heritage Commons II",
                    service_address="13601 North Freeway",
                    possible_property_aliases=["heritage commons ii hillwood properties"],
                )
            ),
            [
                {
                    **_alias("HC2"),
                    "asset_name": "Heritage Commons II",
                    "matched_text": "13601 north freeway fort worth texas 76177",
                    "similarity_score": 1.0,
                },
                {
                    **_alias("HC3"),
                    "asset_name": "Heritage Commons III",
                    "matched_text": "13601 north freeway fort worth texas 76177",
                    "similarity_score": 1.0,
                },
            ],
        )

        prompt = assistant.llm_extractor.prompt
        self.assertIn("If multiple candidates share the same address", prompt)
        self.assertIn("Heritage Commons II", prompt)
        self.assertIn("Heritage Commons III", prompt)
        self.assertIn("possible_property_aliases", prompt)
        self.assertIn("property_lookup", prompt)
        self.assertIn("evidence.summary", prompt)

    def test_prompt_passes_extraction_evidence_for_shared_address_disambiguation(self) -> None:
        assistant = PropertyMatchAssistant(FakeLlmExtractor(_interpretation_payload("asset-CTG", asset_alias="CTG")))

        assistant.suggest(
            validate_extraction(
                _payload(
                    property_code=None,
                    property_name=None,
                    service_address="2451 WESTLAKE PKWY WESTLAKE TX 76262",
                    possible_property_aliases=["circle t golf", "64 acres"],
                    evidence_summary="Visible trailing customer block says Hillwood Alliance Group, LP Circle T Golf at 2451 Westlake Pkwy.",
                    property_lookup={
                        "property_code": [],
                        "property_name": [],
                        "tenant": [],
                        "address": ["2451 westlake parkway", "2451 westlake parkway westlake tx 76262"],
                        "suite": [],
                        "city": ["westlake"],
                        "state": ["tx"],
                        "zipcode": ["76262"],
                        "address_candidates": [
                            {
                                "rank": 1,
                                "label": "ship_to",
                                "street": "2451 westlake parkway",
                                "city": "westlake",
                                "state": "tx",
                                "zipcode": "76262",
                                "normalized_address": "2451 westlake parkway westlake tx 76262",
                                "source": "attachment:siteone.pdf:1",
                                "confidence": 0.92,
                                "evidence_text": "SHIP TO: HILLWOOD ALLIANCE GROUP, LP CIRCLE T GOLF 2451 WESTLAKE PKWY",
                            }
                        ],
                    },
                )
            ),
            [
                {
                    **_alias("CTR"),
                    "asset_id": "asset-CTR",
                    "asset_alias": "CTR",
                    "asset_name": "Circle T Ranch",
                    "matched_column": "address_components",
                    "matched_text": "2451 westlake parkway roanoke texas 76262",
                    "similarity_score": 0.6067,
                },
                {
                    **_alias("CTG"),
                    "asset_id": "asset-CTG",
                    "asset_alias": "CTG",
                    "asset_name": "Circle T Golf Course",
                    "matched_column": "address_components",
                    "matched_text": "2451 westlake parkway roanoke texas 76262",
                    "similarity_score": 0.6067,
                },
            ],
        )

        prompt = assistant.llm_extractor.prompt
        self.assertIn("\"evidence\"", prompt)
        self.assertIn("Circle T Golf", prompt)
        self.assertIn("SHIP TO: HILLWOOD ALLIANCE GROUP, LP CIRCLE T GOLF", prompt)
        self.assertIn("If shared-address candidates are supported only by the same address", prompt)
        self.assertIn("Do not choose a shared-address candidate based only on score tie order", prompt)

    def test_prompt_prefers_visible_hillwood_commons_name_over_conflicting_bill_to(self) -> None:
        assistant = PropertyMatchAssistant(FakeLlmExtractor(_interpretation_payload("asset-HWC2", asset_alias="HWC2")))

        assistant.suggest(
            validate_extraction(
                _payload(
                    property_code=None,
                    property_name="Hillwood Commons II",
                    bill_to="Heritage Commons II, 13601 North Freeway",
                    possible_property_aliases=["hillwood commons ii"],
                    property_lookup={
                        "property_code": [],
                        "property_name": ["hillwood commons ii"],
                        "tenant": [],
                        "address": ["13601 north freeway"],
                        "suite": [],
                        "city": [],
                        "state": [],
                        "zipcode": [],
                    },
                )
            ),
            [
                {**_alias("HWC2"), "asset_id": "asset-HWC2", "asset_alias": "HWC2", "asset_name": "Hillwood Commons II"},
                {**_alias("HC2"), "asset_id": "asset-HC2", "asset_alias": "HC2", "asset_name": "Heritage Commons II"},
            ],
        )

        prompt = assistant.llm_extractor.prompt
        self.assertIn("Prefer explicit visible asset or property name evidence over conflicting bill-to address evidence", prompt)
        self.assertIn("Do not convert visible Hillwood Commons II evidence into Heritage Commons II / HC2", prompt)
        self.assertIn("Hillwood Commons II", prompt)
        self.assertIn("Heritage Commons II", prompt)
        self.assertIn("\"asset_alias\": \"HWC2\"", prompt)
        self.assertIn("\"asset_alias\": \"HC2\"", prompt)

    def test_prompt_treats_ag_shorthand_as_alliance_gateway_evidence(self) -> None:
        assistant = PropertyMatchAssistant(FakeLlmExtractor(_interpretation_payload("asset-GW31", asset_alias="GW31")))

        assistant.suggest(
            validate_extraction(
                _payload(
                    property_code="AG31",
                    property_name=None,
                    property_lookup={
                        "property_code": ["ag31"],
                        "property_name": [],
                        "tenant": [],
                        "address": [],
                        "suite": [],
                        "city": [],
                        "state": [],
                        "zipcode": [],
                    },
                )
            ),
            [{**_alias("GW31"), "asset_id": "asset-GW31", "asset_alias": "GW31", "asset_name": "Alliance Gateway 31"}],
        )

        prompt = assistant.llm_extractor.prompt
        self.assertIn("Alliance Gateway shorthand such as AG31, AG 31, or AG-31", prompt)
        self.assertIn("Alliance Gateway 31", prompt)
        self.assertIn("\"asset_alias\": \"GW31\"", prompt)

    def test_prompt_treats_unique_near_name_as_disambiguating_evidence(self) -> None:
        assistant = PropertyMatchAssistant(FakeLlmExtractor(_interpretation_payload("asset-GW15", asset_alias="GW15")))

        assistant.suggest(
            validate_extraction(
                _payload(
                    property_code=None,
                    property_name=None,
                    possible_property_aliases=["gateway 15"],
                    evidence_summary="Invoice property field says Gateway 15.",
                    property_lookup={
                        "property_code": [],
                        "property_name": [],
                        "tenant": [],
                        "address": [],
                        "suite": [],
                        "city": [],
                        "state": [],
                        "zipcode": [],
                    },
                )
            ),
            [
                {**_alias("GW15"), "asset_id": "asset-GW15", "asset_alias": "GW15", "asset_name": "Alliance Gateway 15"},
                {**_alias("GW14"), "asset_id": "asset-GW14", "asset_alias": "GW14", "asset_name": "Alliance Gateway 14"},
            ],
        )

        prompt = assistant.llm_extractor.prompt
        self.assertIn("clear semantic near-name evidence", prompt)
        self.assertIn("Gateway 15 -> Alliance Gateway 15 / GW15", prompt)
        self.assertIn("Circle T Golf Course -> Circle T Golf / CTG", prompt)
        self.assertIn("Heritage Commons 2 -> Heritage Commons II / HC2", prompt)
        self.assertIn("vague family names or ambiguous partial names", prompt)
        self.assertIn("Gateway 15", prompt)
        self.assertIn("\"asset_name\": \"Alliance Gateway 15\"", prompt)

    def test_property_match_assistant_allows_check_request_review(self) -> None:
        assistant = PropertyMatchAssistant(FakeLlmExtractor(_interpretation_payload("asset-GW31", asset_alias="GW31")))

        suggestion = assistant.suggest(
            validate_extraction(_payload(document_type="check_request", property_code="AG31")),
            [{**_alias("GW31"), "asset_id": "asset-GW31", "asset_alias": "GW31", "asset_name": "Alliance Gateway 31"}],
        )

        self.assertIsNotNone(suggestion)
        assert suggestion is not None
        self.assertEqual(suggestion.candidate_asset_ids, ("asset-GW31",))

    def test_prompt_serializes_structured_address_candidates(self) -> None:
        assistant = PropertyMatchAssistant(FakeLlmExtractor(_interpretation_payload("asset-HC2")))

        assistant.suggest(
            validate_extraction(
                _payload(
                    property_lookup={
                        "property_code": None,
                        "property_name": ["heritage commons ii"],
                        "tenant": None,
                        "address": ["13601 north freeway"],
                        "suite": None,
                        "city": ["fort worth"],
                        "state": ["tx"],
                        "zipcode": ["76177"],
                        "address_candidates": [
                            {
                                "rank": 1,
                                "label": "service_location",
                                "street": "13601 north freeway",
                                "city": "fort worth",
                                "state": "tx",
                                "zipcode": "76177",
                                "normalized_address": "13601 north freeway fort worth tx 76177",
                                "source": "attachment:invoice.pdf:1",
                                "confidence": 0.94,
                                "evidence_text": "Service Location: 13601 North Freeway",
                            }
                        ],
                    }
                )
            ),
            [_alias("HC2")],
        )

        prompt = assistant.llm_extractor.prompt
        self.assertIn("\"address_candidates\"", prompt)
        self.assertIn("\"label\": \"service_location\"", prompt)
        self.assertIn("\"normalized_address\": \"13601 north freeway fort worth tx 76177\"", prompt)

    def test_prompt_allows_bill_to_only_address_fallback_without_stronger_conflict(self) -> None:
        assistant = PropertyMatchAssistant(FakeLlmExtractor(_interpretation_payload("asset-HW1", asset_alias="HW1")))

        assistant.suggest(
            validate_extraction(
                _payload(
                    property_code=None,
                    property_name=None,
                    service_address=None,
                    bill_to="Hillwood Alliance Group, 9800 Hillwood Parkway, Fort Worth TX 76177",
                    possible_property_aliases=["SH114", "Torc Entitlements"],
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
                                "source": "attachment:invoice.pdf:1",
                                "confidence": 0.86,
                                "evidence_text": "Bill To: Hillwood Alliance Group 9800 Hillwood Parkway",
                            }
                        ],
                    },
                )
            ),
            [{**_alias("HW1"), "asset_id": "asset-HW1", "asset_alias": "HW1", "matched_column": "address", "matched_text": "9800 Hillwood Parkway"}],
        )

        prompt = assistant.llm_extractor.prompt
        self.assertIn("Do not reject a bill-to-only address match solely because it is Bill To", prompt)
        self.assertIn("Treat unresolved project, job, or property text as non-conflicting", prompt)
        self.assertIn("\"label\": \"bill_to\"", prompt)

    def test_batch_prompt_allows_unresolved_property_text_bill_to_address_fallback(self) -> None:
        assistant = PropertyMatchAssistant(
            FakeLlmExtractor(
                {
                    "schema_version": "llm_property_match_batch.v1",
                    "items": [{"item_key": "attachment:westwood", "interpretation": _interpretation_payload("asset-HWC1", asset_alias="HWC1")}],
                }
            )
        )

        assistant.suggest_batch(
            [
                (
                    "attachment:westwood",
                    validate_extraction(
                        _payload(
                            property_code=None,
                            property_name="Hillwood - 2026 CTR MIB",
                            bill_to="Hillwood Properties, 9800 Hillwood Parkway, Fort Worth TX 76177",
                            possible_property_aliases=["ctr mib"],
                            property_lookup={
                                "property_code": [],
                                "property_name": ["hillwood 2026 ctr mib"],
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
                                        "source": "attachment:westwood.pdf:1",
                                        "confidence": 0.91,
                                        "evidence_text": "Bill To: Hillwood Properties 9800 Hillwood Parkway",
                                    }
                                ],
                            },
                        )
                    ),
                    [
                        {
                            **_alias("HWC1"),
                            "asset_id": "asset-HWC1",
                            "asset_alias": "HWC1",
                            "asset_name": "Hillwood Commons I",
                            "matched_column": "address",
                            "matched_text": "9800 Hillwood Parkway",
                            "similarity_score": 1.0,
                        }
                    ],
                )
            ]
        )

        prompt = assistant.llm_extractor.prompt
        self.assertIn("Accept a bill-to or customer-account address candidate when its matched_column is address-based", prompt)
        self.assertIn("Do not return an empty candidate list merely because unresolved project or property text exists", prompt)
        self.assertIn("9800 Hillwood Parkway", prompt)
        self.assertIn("Hillwood - 2026 CTR MIB", prompt)

    def test_prompt_serializes_asset_type_for_harvest_retail_disambiguation(self) -> None:
        assistant = PropertyMatchAssistant(FakeLlmExtractor(_interpretation_payload("asset-HTC", asset_alias="HTC")))

        suggestion = assistant.suggest(
            validate_extraction(
                _payload(
                    property_code=None,
                    property_name="harvest retail building a",
                    bill_to="Hillwood",
                    property_lookup={
                        "property_code": [],
                        "property_name": ["harvest retail building a", "harvest town center"],
                        "tenant": [],
                        "address": [],
                        "suite": [],
                        "city": [],
                        "state": [],
                        "zipcode": [],
                    },
                )
            ),
            [
                {**_alias("HTC"), "asset_id": "asset-HTC", "asset_alias": "HTC", "asset_name": "Harvest Town Center", "asset_type": "Retail"},
                {**_alias("HH"), "asset_id": "asset-HH", "asset_alias": "HH", "asset_name": "Harvest House", "asset_type": "Multifamily"},
                {**_alias("HGL"), "asset_id": "asset-HGL", "asset_alias": "HGL", "asset_name": "Harvest Ground Lease", "asset_type": "Ground Lease"},
            ],
        )

        self.assertIsNotNone(suggestion)
        assert suggestion is not None
        self.assertEqual(suggestion.candidate_asset_ids, ("asset-HTC",))
        prompt = assistant.llm_extractor.prompt
        self.assertIn("\"asset_type\": \"Retail\"", prompt)
        self.assertIn("\"asset_type\": \"Multifamily\"", prompt)
        self.assertIn("\"asset_type\": \"Ground Lease\"", prompt)
        self.assertIn("Harvest Town Center", prompt)
        self.assertIn("Harvest House", prompt)
        self.assertIn("Use candidate asset_type only", prompt)
        self.assertIn("Prefer Project, Job, Site, Service Location", prompt)

    def test_batch_property_match_uses_one_prompt_for_multiple_items(self) -> None:
        assistant = PropertyMatchAssistant(
            FakeLlmExtractor(
                {
                    "schema_version": "llm_property_match_batch.v1",
                    "items": [
                        {"item_key": "attachment:1", "interpretation": _interpretation_payload("asset-HC2")},
                        {"item_key": "attachment:2", "interpretation": _interpretation_payload("asset-HC3", asset_alias="HC3")},
                    ],
                }
            )
        )

        suggestions = assistant.suggest_batch(
            [
                ("attachment:1", validate_extraction(_payload(property_code=None)), [_alias("HC2")]),
                ("attachment:2", validate_extraction(_payload(property_code=None)), [_alias("HC3")]),
            ]
        )

        self.assertEqual(assistant.llm_extractor.calls, 1)
        self.assertEqual(suggestions["attachment:1"].candidate_asset_ids, ("asset-HC2",))
        self.assertEqual(suggestions["attachment:2"].candidate_asset_ids, ("asset-HC3",))
        self.assertIn("llm_property_match_batch.v1", assistant.llm_extractor.prompt)

    def test_retries_malformed_interpretation_shape_once(self) -> None:
        assistant = PropertyMatchAssistant(
            SequenceLlmExtractor(
                [
                    {"schema_version": "llm_interpretation.v1", "candidate_property_matches": "bad", "reason": "bad"},
                    _interpretation_payload("asset-HC2"),
                ]
            )
        )

        suggestion = assistant.suggest(validate_extraction(_payload(property_code=None)), [_alias("HC2")])

        self.assertIsNotNone(suggestion)
        self.assertEqual(assistant.llm_extractor.calls, 2)
        self.assertIn("Validation errors", assistant.llm_extractor.prompt)

    def test_does_not_retry_invented_interpretation_candidate(self) -> None:
        assistant = PropertyMatchAssistant(SequenceLlmExtractor([_interpretation_payload("asset-MADE-UP")]))

        suggestion = assistant.suggest(validate_extraction(_payload(property_code=None)), [_alias("HC2")])

        self.assertIsNone(suggestion)
        self.assertEqual(assistant.llm_extractor.calls, 1)


class FakeLlmExtractor:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.prompt = ""
        self.calls = 0

    def run_json_prompt(self, prompt: str):
        self.calls += 1
        self.prompt = prompt
        return json.dumps(self.payload), self.payload


class SequenceLlmExtractor:
    def __init__(self, payloads: list[dict[str, Any]]) -> None:
        self.payloads = payloads
        self.prompt = ""
        self.calls = 0

    def run_json_prompt(self, prompt: str):
        self.calls += 1
        self.prompt = prompt
        payload = self.payloads[min(self.calls - 1, len(self.payloads) - 1)]
        return json.dumps(payload), payload


def _alias(property_code: str) -> dict[str, Any]:
    return {
        "asset_id": f"asset-{property_code}",
        "asset_alias": property_code,
        "asset_name": "HC2 Property",
        "business_unit_code": "PROP",
        "destination_code": "MICHELE_FELLERS",
        "matched_column": "asset_alias",
        "matched_text": "HC 2",
    }


def _interpretation_payload(asset_id: str, asset_alias: str = "HC2") -> dict[str, Any]:
    return {
        "schema_version": "llm_interpretation.v1",
        "candidate_property_matches": [
            {
                "asset_id": asset_id,
                "asset_alias": asset_alias,
                "confidence": 0.91,
                "evidence": [{"source": "attachment:invoice.pdf", "page": 1, "text": f"Bill To: {asset_alias}"}],
            }
        ],
        "candidate_rule_matches": [],
        "ambiguity_flags": [],
        "recommended_outcome": "AUTO",
        "reason": "HC 2 matches configured property.",
    }
