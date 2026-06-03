from __future__ import annotations

import unittest

from ap_automation.models.llm_interpretation import LlmInterpretationValidationError, validate_llm_interpretation


class LlmInterpretationValidationTests(unittest.TestCase):
    def test_validates_advisory_property_candidate_with_evidence(self) -> None:
        interpretation = validate_llm_interpretation(
            _payload(),
            allowed_asset_ids={"asset-HC2"},
            allowed_rule_codes={"property_routing_match"},
        )

        self.assertEqual(interpretation.schema_version, "llm_interpretation.v1")
        self.assertEqual(interpretation.candidate_property_matches[0].asset_id, "asset-HC2")
        self.assertEqual(interpretation.recommended_outcome, "AUTO")

    def test_rejects_invented_asset_id(self) -> None:
        payload = _payload()
        payload["candidate_property_matches"][0]["asset_id"] = "asset-MADE-UP"

        with self.assertRaises(LlmInterpretationValidationError) as exc:
            validate_llm_interpretation(payload, allowed_asset_ids={"asset-HC2"})

        self.assertIn("provided candidate dataset", str(exc.exception))

    def test_rejects_candidate_without_evidence(self) -> None:
        payload = _payload()
        payload["candidate_property_matches"][0]["evidence"] = []

        with self.assertRaises(LlmInterpretationValidationError) as exc:
            validate_llm_interpretation(payload, allowed_asset_ids={"asset-HC2"})

        self.assertIn("evidence must be a non-empty list", str(exc.exception))

    def test_rejects_unknown_recommended_outcome(self) -> None:
        payload = _payload()
        payload["recommended_outcome"] = "SEND_TO_VENDOR"

        with self.assertRaises(LlmInterpretationValidationError) as exc:
            validate_llm_interpretation(payload, allowed_asset_ids={"asset-HC2"})

        self.assertIn("recommended_outcome", str(exc.exception))


def _payload() -> dict:
    return {
        "schema_version": "llm_interpretation.v1",
        "candidate_property_matches": [
            {
                "asset_id": "asset-HC2",
                "asset_alias": "HC2",
                "confidence": 0.91,
                "evidence": [{"source": "attachment:invoice.pdf", "page": 1, "text": "Bill To: HC 2"}],
            }
        ],
        "candidate_rule_matches": [{"rule_code": "property_routing_match", "confidence": 0.82, "evidence": [{"source": "attachment:invoice.pdf", "text": "Bill To: HC 2"}]}],
        "ambiguity_flags": [],
        "recommended_outcome": "AUTO",
        "reason": "HC 2 matches configured property HC2.",
    }
