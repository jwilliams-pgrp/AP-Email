from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ap_automation.models.extraction import ExtractionPayload
from ap_automation.models.llm_interpretation import LlmInterpretation, LlmInterpretationValidationError, validate_llm_interpretation
from ap_automation.services.azure_openai_extractor import AzureOpenAIExtractionError, AzureOpenAIExtractor, contract_repair_prompt


@dataclass(frozen=True)
class PropertyMatchSuggestion:
    candidate_asset_ids: tuple[str, ...]
    confidence: float
    reason: str
    interpretation: LlmInterpretation
    raw_response: str | None
    prompt: str | None

    @property
    def candidate_property_codes(self) -> tuple[str, ...]:
        return tuple(
            match.asset_alias
            for match in self.interpretation.candidate_property_matches
            if match.asset_id in self.candidate_asset_ids and match.asset_alias
        )


@dataclass(frozen=True)
class PropertyMatchAssistant:
    llm_extractor: AzureOpenAIExtractor

    def suggest(
        self,
        extraction: ExtractionPayload,
        alias_mappings: list[dict[str, Any]],
    ) -> PropertyMatchSuggestion | None:
        if extraction.document.document_type not in {"invoice", "check_request"}:
            return None
        if not alias_mappings:
            return None

        prompt = _prompt(extraction, alias_mappings)
        allowed_asset_ids = {
            str(row.get("asset_id")).strip()
            for row in alias_mappings
            if str(row.get("asset_id") or "").strip()
        }
        allowed_property_codes = {
            str(row.get("asset_alias") or row.get("property_code")).strip().upper()
            for row in alias_mappings
            if str(row.get("asset_alias") or row.get("property_code") or "").strip()
        }
        try:
            raw_response, parsed = self.llm_extractor.run_json_prompt(prompt)
        except AzureOpenAIExtractionError:
            return None
        try:
            interpretation = validate_llm_interpretation(parsed, allowed_asset_ids=allowed_asset_ids, allowed_property_codes=allowed_property_codes)
        except LlmInterpretationValidationError as exc:
            if not _retryable_interpretation_errors(exc.errors):
                return None
            try:
                repair_prompt = contract_repair_prompt(
                    original_prompt=prompt,
                    invalid_response=raw_response,
                    errors=exc.errors,
                    contract_name="llm_interpretation.v1",
                )
                raw_response, parsed = self.llm_extractor.run_json_prompt(repair_prompt)
                interpretation = validate_llm_interpretation(
                    parsed,
                    allowed_asset_ids=allowed_asset_ids,
                    allowed_property_codes=allowed_property_codes,
                )
            except (AzureOpenAIExtractionError, LlmInterpretationValidationError):
                return None

        usable_matches = [
            match
            for match in interpretation.candidate_property_matches
            if match.asset_id in allowed_asset_ids and match.evidence
        ]
        normalized_asset_ids = tuple(sorted({match.asset_id for match in usable_matches}))
        if not normalized_asset_ids:
            return None

        confidence = min(match.confidence for match in usable_matches if match.asset_id in normalized_asset_ids)
        return PropertyMatchSuggestion(
            candidate_asset_ids=normalized_asset_ids,
            confidence=max(0.0, min(1.0, confidence)),
            reason=interpretation.reason,
            interpretation=interpretation,
            raw_response=raw_response,
            prompt=prompt,
        )

    def suggest_batch(
        self,
        requests: list[tuple[str, ExtractionPayload, list[dict[str, Any]]]],
    ) -> dict[str, PropertyMatchSuggestion]:
        eligible = [
            (item_key, extraction, alias_mappings)
            for item_key, extraction, alias_mappings in requests
            if extraction.document.document_type in {"invoice", "check_request"} and alias_mappings
        ]
        if not eligible:
            return {}

        prompt = _batch_prompt(eligible)
        try:
            raw_response, parsed = self.llm_extractor.run_json_prompt(prompt)
        except AzureOpenAIExtractionError:
            return {}

        if parsed.get("schema_version") != "llm_property_match_batch.v1":
            try:
                repair_prompt = contract_repair_prompt(
                    original_prompt=prompt,
                    invalid_response=raw_response,
                    errors=["schema_version must be llm_property_match_batch.v1"],
                    contract_name="llm_property_match_batch.v1",
                )
                raw_response, parsed = self.llm_extractor.run_json_prompt(repair_prompt)
            except AzureOpenAIExtractionError:
                return {}
            if parsed.get("schema_version") != "llm_property_match_batch.v1":
                return {}
        raw_items = parsed.get("items")
        if not isinstance(raw_items, list):
            try:
                repair_prompt = contract_repair_prompt(
                    original_prompt=prompt,
                    invalid_response=raw_response,
                    errors=["items must be a list"],
                    contract_name="llm_property_match_batch.v1",
                )
                raw_response, parsed = self.llm_extractor.run_json_prompt(repair_prompt)
                raw_items = parsed.get("items")
            except AzureOpenAIExtractionError:
                return {}
            if not isinstance(raw_items, list):
                return {}

        context_by_key = {
            item_key: _allowed_candidate_context(alias_mappings)
            for item_key, _extraction, alias_mappings in eligible
        }
        suggestions: dict[str, PropertyMatchSuggestion] = {}
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                continue
            item_key = raw_item.get("item_key")
            if not isinstance(item_key, str) or item_key not in context_by_key:
                continue
            interpretation_payload = raw_item.get("interpretation")
            if not isinstance(interpretation_payload, dict):
                continue
            allowed_asset_ids, allowed_property_codes = context_by_key[item_key]
            try:
                interpretation = validate_llm_interpretation(
                    interpretation_payload,
                    allowed_asset_ids=allowed_asset_ids,
                    allowed_property_codes=allowed_property_codes,
                )
            except LlmInterpretationValidationError as exc:
                if not _retryable_interpretation_errors(exc.errors):
                    continue
                try:
                    repair_prompt = contract_repair_prompt(
                        original_prompt=prompt,
                        invalid_response=raw_response,
                        errors=[f"{item_key}: {error}" for error in exc.errors],
                        contract_name="llm_property_match_batch.v1",
                    )
                    raw_response, parsed = self.llm_extractor.run_json_prompt(repair_prompt)
                    raw_items = parsed.get("items") if isinstance(parsed, dict) else None
                    if not isinstance(raw_items, list):
                        return suggestions
                    return self._suggestions_from_batch_items(raw_items, context_by_key, raw_response=raw_response, prompt=prompt)
                except AzureOpenAIExtractionError:
                    continue
            suggestion = _suggestion_from_interpretation(interpretation, raw_response=raw_response, prompt=prompt)
            if suggestion is not None:
                suggestions[item_key] = suggestion
        return suggestions

    def _suggestions_from_batch_items(
        self,
        raw_items: list[Any],
        context_by_key: dict[str, tuple[set[str], set[str]]],
        *,
        raw_response: str | None,
        prompt: str | None,
    ) -> dict[str, PropertyMatchSuggestion]:
        suggestions: dict[str, PropertyMatchSuggestion] = {}
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                continue
            item_key = raw_item.get("item_key")
            if not isinstance(item_key, str) or item_key not in context_by_key:
                continue
            interpretation_payload = raw_item.get("interpretation")
            if not isinstance(interpretation_payload, dict):
                continue
            allowed_asset_ids, allowed_property_codes = context_by_key[item_key]
            try:
                interpretation = validate_llm_interpretation(
                    interpretation_payload,
                    allowed_asset_ids=allowed_asset_ids,
                    allowed_property_codes=allowed_property_codes,
                )
            except LlmInterpretationValidationError:
                continue
            suggestion = _suggestion_from_interpretation(interpretation, raw_response=raw_response, prompt=prompt)
            if suggestion is not None:
                suggestions[item_key] = suggestion
        return suggestions


@dataclass(frozen=True)
class CachedPropertyMatchReviewer:
    suggestions_by_item_key: dict[str, PropertyMatchSuggestion]
    item_key_by_extraction_id: dict[int, str]

    def suggest(
        self,
        extraction: ExtractionPayload,
        alias_mappings: list[dict[str, Any]],
    ) -> PropertyMatchSuggestion | None:
        item_key = self.item_key_by_extraction_id.get(id(extraction))
        if item_key is None:
            return None
        return self.suggestions_by_item_key.get(item_key)


def _suggestion_from_interpretation(
    interpretation: LlmInterpretation,
    *,
    raw_response: str | None,
    prompt: str | None,
) -> PropertyMatchSuggestion | None:
    usable_matches = [match for match in interpretation.candidate_property_matches if match.evidence]
    normalized_asset_ids = tuple(sorted({match.asset_id for match in usable_matches}))
    if not normalized_asset_ids:
        return None
    confidence = min(match.confidence for match in usable_matches if match.asset_id in normalized_asset_ids)
    return PropertyMatchSuggestion(
        candidate_asset_ids=normalized_asset_ids,
        confidence=max(0.0, min(1.0, confidence)),
        reason=interpretation.reason,
        interpretation=interpretation,
        raw_response=raw_response,
        prompt=prompt,
    )


def _retryable_interpretation_errors(errors: list[str]) -> bool:
    non_retryable_fragments = (
        "provided candidate dataset",
        "provided workflow rules",
    )
    return not any(fragment in error for error in errors for fragment in non_retryable_fragments)


def _allowed_candidate_context(alias_mappings: list[dict[str, Any]]) -> tuple[set[str], set[str]]:
    allowed_asset_ids = {
        str(row.get("asset_id")).strip()
        for row in alias_mappings
        if str(row.get("asset_id") or "").strip()
    }
    allowed_property_codes = {
        str(row.get("asset_alias") or row.get("property_code")).strip().upper()
        for row in alias_mappings
        if str(row.get("asset_alias") or row.get("property_code") or "").strip()
    }
    return allowed_asset_ids, allowed_property_codes


def _prompt(extraction: ExtractionPayload, alias_mappings: list[dict[str, Any]]) -> str:
    compact_dataset = []
    for row in alias_mappings:
        compact_dataset.append(
            {
                "asset_id": row.get("asset_id"),
                "asset_alias": row.get("asset_alias") or row.get("property_code"),
                "asset_name": row.get("asset_name") or row.get("property_name"),
                "asset_type": row.get("asset_type"),
                "business_unit_code": row.get("business_unit_code"),
                "destination_code": row.get("destination_code") or row.get("default_destination_code"),
                "matched_text": row.get("matched_text") or row.get("alias_value"),
                "matched_column": row.get("matched_column") or row.get("alias_type"),
                "similarity_score": row.get("similarity_score"),
            }
        )

    payload = {
        "invoice": {
            "vendor_name": extraction.invoice.vendor_name,
            "bill_to": extraction.invoice.bill_to,
            "property_name": extraction.invoice.property_name,
            "property_code": extraction.invoice.property_code,
            "service_address": extraction.invoice.service_address,
        },
        "property_lookup": {
            "property_code": list(extraction.property_lookup.property_code),
            "property_name": list(extraction.property_lookup.property_name),
            "tenant": list(extraction.property_lookup.tenant),
            "address": list(extraction.property_lookup.address),
            "suite": list(extraction.property_lookup.suite),
            "city": list(extraction.property_lookup.city),
            "state": list(extraction.property_lookup.state),
            "zipcode": list(extraction.property_lookup.zipcode),
            "address_candidates": [_address_candidate_payload(candidate) for candidate in extraction.property_lookup.address_candidates],
        },
        "business_signals": {
            "business_unit_code": extraction.business_signals.business_unit_code,
            "possible_property_aliases": list(extraction.business_signals.possible_property_aliases),
        },
        "evidence": {
            "summary": extraction.evidence.summary,
            "source_attachments": list(extraction.evidence.source_attachments),
            "source_pages": list(extraction.evidence.source_pages),
        },
        "dataset": compact_dataset,
    }
    return (
        "You are assisting deterministic property matching for AP invoice routing.\n"
        "Return advisory interpretation only. Deterministic code makes the final AP routing decision.\n"
        "Choose candidate asset_id values from the provided dataset only, and cite source evidence for every candidate.\n"
        "Return one JSON object with this exact shape:\n"
        "{\n"
        "  \"schema_version\":\"llm_interpretation.v1\",\n"
        "  \"candidate_property_matches\":[{\"asset_id\":\"ASSET_ID\",\"asset_alias\":\"ASSET_ALIAS_OR_NULL\",\"confidence\":0.0,\"evidence\":[{\"source\":\"invoice\",\"page\":null,\"text\":\"quoted evidence\"}]}],\n"
        "  \"candidate_rule_matches\":[],\n"
        "  \"ambiguity_flags\":[],\n"
        "  \"recommended_outcome\":null,\n"
        "  \"reason\":\"short reason\"\n"
        "}\n"
        "Rules:\n"
        "- Never invent asset IDs or aliases.\n"
        "- Never invent destinations.\n"
        "- recommended_outcome is non-authoritative and may be null.\n"
        "- Return exactly one candidate_property_matches item when the invoice evidence clearly selects one candidate.\n"
        "- Use an empty candidate list when evidence is weak or ambiguous after comparing all candidates.\n"
        "- Use an empty candidate list when you cannot cite evidence.\n"
        "- Compare extracted property code, property name, tenant, possible aliases, evidence.summary, address_candidates[].evidence_text, service/property/site address, bill-to, and each candidate asset_alias, asset_name, asset_type, matched_text, matched_column, and similarity_score.\n"
        "- Prefer explicit asset-code, building-name, tenant, and service/property/site evidence over inferred vendor hints.\n"
        "- Prefer explicit visible asset or property name evidence over conflicting bill-to address evidence when the name selects a candidate and the bill-to address points elsewhere.\n"
        "- Do not convert visible Hillwood Commons II evidence into Heritage Commons II / HC2 unless the source visibly says Heritage Commons II or HC2; when Hillwood Commons II / HWC2 is a candidate, prefer it for visible Hillwood Commons II evidence.\n"
        "- Treat visible Alliance Gateway shorthand such as AG31, AG 31, or AG-31 as evidence for the corresponding Alliance Gateway 31 candidate when that candidate is present in the dataset; use the candidate's configured asset_alias and never invent a missing candidate.\n"
        "- Preserve visible asset-code families exactly. WP9, GW9, HC2, HWC2, ACC 14, and ACN5 are distinct alias families unless the source visibly provides an accepted alias variant.\n"
        "- Do not convert visible Westport/WP evidence into Alliance Gateway/GW evidence, or visible Gateway/GW evidence into Westport/WP evidence. Westport and Gateway are distinct asset families even when the building number overlaps. If source evidence says WP9 and a candidate is GW9 / Alliance Gateway 9, do not select GW9 unless the source also visibly supports GW9 or Alliance Gateway 9. If source evidence says Westport 14 & 15 and candidates include GW14 / Alliance Gateway 14 or GW15 / Alliance Gateway 15, do not select the Gateway candidates unless the source also visibly supports Gateway/GW evidence.\n"
        "- If extracted property_code and property_name conflict, prefer the source-visible exact code family and return an empty candidate list when the conflict prevents confident candidate selection.\n"
        "- Treat clear semantic near-name evidence as disambiguating when exactly one provided candidate fits the visible phrase, including missing common prefixes, suffix variants, number-format variants, and configured alias variants such as Gateway 15 -> Alliance Gateway 15 / GW15, Circle T Golf Course -> Circle T Golf / CTG, and Heritage Commons 2 -> Heritage Commons II / HC2. The Gateway 15 example applies only when source evidence visibly says Gateway, Alliance Gateway, GW, or allowed AG shorthand; it does not apply to Westport evidence.\n"
        "- Do not use vague family names or ambiguous partial names such as Gateway, Circle T, Commons, or Westport to select a candidate when multiple provided candidates could fit.\n"
        "- Prefer Project, Job, Site, Service Location, Location, Deliver To, Ship To, or Property evidence over Bill To when identifying the serviced property.\n"
        "- Bill To or customer-account address evidence may select exactly one active candidate when no property code, property name, tenant, service/site/delivery/shipping/property address, or other stronger evidence identifies a different asset.\n"
        "- Accept a bill-to or customer-account address candidate when its matched_column is address-based, its score is clearly highest, and visible project/property text does not match any provided candidate.\n"
        "- Do not reject a bill-to-only address match solely because it is Bill To; cite the bill-to address evidence when it is the only clear active property candidate above the provided score threshold.\n"
        "- Treat unresolved project, job, or property text as non-conflicting unless it identifies a different configured asset, address, property name, or property code.\n"
        "- Do not return an empty candidate list merely because unresolved project or property text exists. For example, when a project/property label is present, no provided candidate matches that label, and Bill To exactly matches one active asset address such as 9800 Hillwood Parkway, select that address candidate.\n"
        "- Use candidate asset_type only to interpret visible source words such as Retail, Multifamily, Ground Lease, Project, Job, Site, or Service Location; never use it as routing authority.\n"
        "- If multiple candidates share the same address, use explicit extracted evidence such as asset name, property name, tenant/occupant, possible alias, address candidate evidence_text, evidence.summary, or asset-code evidence to choose the one best candidate.\n"
        "- If shared-address candidates are supported only by the same address and no extracted name/code/tenant/alias evidence distinguishes one candidate, return an empty candidate list.\n"
        "- Do not choose a shared-address candidate based only on score tie order, asset_type, destination, or candidate list order.\n"
        "- Do not return multiple candidates just because they share an address; return multiple candidates only if the evidence truly supports multiple assets equally, which deterministic code will treat as ambiguous.\n"
        f"Input:\n{json.dumps(payload, indent=2, sort_keys=True)}\n"
    )


def _batch_prompt(requests: list[tuple[str, ExtractionPayload, list[dict[str, Any]]]]) -> str:
    items = []
    for item_key, extraction, alias_mappings in requests:
        items.append(
            {
                "item_key": item_key,
                "invoice": {
                    "vendor_name": extraction.invoice.vendor_name,
                    "bill_to": extraction.invoice.bill_to,
                    "property_name": extraction.invoice.property_name,
                    "property_code": extraction.invoice.property_code,
                    "service_address": extraction.invoice.service_address,
                },
                "property_lookup": {
                    "property_code": list(extraction.property_lookup.property_code),
                    "property_name": list(extraction.property_lookup.property_name),
                    "tenant": list(extraction.property_lookup.tenant),
                    "address": list(extraction.property_lookup.address),
                    "suite": list(extraction.property_lookup.suite),
                    "city": list(extraction.property_lookup.city),
                    "state": list(extraction.property_lookup.state),
                    "zipcode": list(extraction.property_lookup.zipcode),
                    "address_candidates": [_address_candidate_payload(candidate) for candidate in extraction.property_lookup.address_candidates],
                },
                "business_signals": {
                    "business_unit_code": extraction.business_signals.business_unit_code,
                    "possible_property_aliases": list(extraction.business_signals.possible_property_aliases),
                },
                "evidence": {
                    "summary": extraction.evidence.summary,
                    "source_attachments": list(extraction.evidence.source_attachments),
                    "source_pages": list(extraction.evidence.source_pages),
                },
                "dataset": [_candidate_payload(row) for row in alias_mappings],
            }
        )
    return (
        "You are assisting deterministic property matching for AP invoice routing.\n"
        "Return advisory interpretation only. Deterministic code makes the final AP routing decision.\n"
        "For each input item, choose candidate asset_id values from that item's provided dataset only, and cite source evidence for every candidate.\n"
        "Return one JSON object with this exact shape:\n"
        "{\n"
        "  \"schema_version\":\"llm_property_match_batch.v1\",\n"
        "  \"items\":[{\"item_key\":\"INPUT_ITEM_KEY\",\"interpretation\":{\n"
        "    \"schema_version\":\"llm_interpretation.v1\",\n"
        "    \"candidate_property_matches\":[{\"asset_id\":\"ASSET_ID\",\"asset_alias\":\"ASSET_ALIAS_OR_NULL\",\"confidence\":0.0,\"evidence\":[{\"source\":\"invoice\",\"page\":null,\"text\":\"quoted evidence\"}]}],\n"
        "    \"candidate_rule_matches\":[],\"ambiguity_flags\":[],\"recommended_outcome\":null,\"reason\":\"short reason\"\n"
        "  }}]\n"
        "}\n"
        "Rules:\n"
        "- Return exactly one output item for each input item_key.\n"
        "- Never invent asset IDs, aliases, destinations, workflow decisions, or item keys.\n"
        "- recommended_outcome is non-authoritative and may be null.\n"
        "- Return exactly one candidate_property_matches item when the invoice evidence clearly selects one candidate.\n"
        "- Use an empty candidate list when evidence is weak, ambiguous, or cannot be cited.\n"
        "- Compare candidate asset_type when visible source property text includes words such as Retail, Multifamily, or Ground Lease, but never use asset_type as routing authority.\n"
        "- Prefer explicit visible asset or property name evidence over conflicting bill-to address evidence when the name selects a candidate and the bill-to address points elsewhere.\n"
        "- Do not convert visible Hillwood Commons II evidence into Heritage Commons II / HC2 unless the source visibly says Heritage Commons II or HC2; when Hillwood Commons II / HWC2 is a candidate, prefer it for visible Hillwood Commons II evidence.\n"
        "- Treat visible Alliance Gateway shorthand such as AG31, AG 31, or AG-31 as evidence for the corresponding Alliance Gateway 31 candidate when that candidate is present in the dataset; use the candidate's configured asset_alias and never invent a missing candidate.\n"
        "- Preserve visible asset-code families exactly. WP9, GW9, HC2, HWC2, ACC 14, and ACN5 are distinct alias families unless the source visibly provides an accepted alias variant.\n"
        "- Do not convert visible Westport/WP evidence into Alliance Gateway/GW evidence, or visible Gateway/GW evidence into Westport/WP evidence. Westport and Gateway are distinct asset families even when the building number overlaps. If source evidence says WP9 and a candidate is GW9 / Alliance Gateway 9, do not select GW9 unless the source also visibly supports GW9 or Alliance Gateway 9. If source evidence says Westport 14 & 15 and candidates include GW14 / Alliance Gateway 14 or GW15 / Alliance Gateway 15, do not select the Gateway candidates unless the source also visibly supports Gateway/GW evidence.\n"
        "- If extracted property_code and property_name conflict, prefer the source-visible exact code family and return an empty candidate list when the conflict prevents confident candidate selection.\n"
        "- Treat clear semantic near-name evidence as disambiguating when exactly one provided candidate fits the visible phrase, including missing common prefixes, suffix variants, number-format variants, and configured alias variants such as Gateway 15 -> Alliance Gateway 15 / GW15, Circle T Golf Course -> Circle T Golf / CTG, and Heritage Commons 2 -> Heritage Commons II / HC2. The Gateway 15 example applies only when source evidence visibly says Gateway, Alliance Gateway, GW, or allowed AG shorthand; it does not apply to Westport evidence.\n"
        "- Do not use vague family names or ambiguous partial names such as Gateway, Circle T, Commons, or Westport to select a candidate when multiple provided candidates could fit.\n"
        "- Prefer Project, Job, Site, Service Location, Location, Deliver To, Ship To, or Property evidence over Bill To when identifying the serviced property.\n"
        "- Bill To or customer-account address evidence may select exactly one active candidate when no property code, property name, tenant, service/site/delivery/shipping/property address, or other stronger evidence identifies a different asset.\n"
        "- Accept a bill-to or customer-account address candidate when its matched_column is address-based, its score is clearly highest, and visible project/property text does not match any provided candidate.\n"
        "- Do not reject a bill-to-only address match solely because it is Bill To; cite the bill-to address evidence when it is the only clear active property candidate above the provided score threshold.\n"
        "- Treat unresolved project, job, or property text as non-conflicting unless it identifies a different configured asset, address, property name, or property code.\n"
        "- Do not return an empty candidate list merely because unresolved project or property text exists. For example, when a project/property label is present, no provided candidate matches that label, and Bill To exactly matches one active asset address such as 9800 Hillwood Parkway, select that address candidate.\n"
        "- Compare extracted property code, property name, tenant, possible aliases, evidence.summary, address_candidates[].evidence_text, service/property/site address, bill-to, and every candidate.\n"
        "- If multiple candidates share the same address, use explicit extracted evidence such as asset name, property name, tenant/occupant, possible alias, address candidate evidence_text, evidence.summary, or asset-code evidence to choose the one best candidate.\n"
        "- If shared-address candidates are supported only by the same address and no extracted name/code/tenant/alias evidence distinguishes one candidate, return an empty candidate list.\n"
        "- Do not choose a shared-address candidate based only on score tie order, asset_type, destination, or candidate list order.\n"
        "- Do not return multiple candidates just because they share an address; return multiple candidates only if the evidence truly supports multiple assets equally.\n"
        f"Input:\n{json.dumps({'items': items}, indent=2, sort_keys=True)}\n"
    )


def _candidate_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "asset_id": row.get("asset_id"),
        "asset_alias": row.get("asset_alias") or row.get("property_code"),
        "asset_name": row.get("asset_name") or row.get("property_name"),
        "asset_type": row.get("asset_type"),
        "business_unit_code": row.get("business_unit_code"),
        "destination_code": row.get("destination_code") or row.get("default_destination_code"),
        "matched_text": row.get("matched_text") or row.get("alias_value"),
        "matched_column": row.get("matched_column") or row.get("alias_type"),
        "similarity_score": row.get("similarity_score"),
    }


def _address_candidate_payload(candidate: Any) -> dict[str, Any]:
    return {
        "rank": candidate.rank,
        "label": candidate.label,
        "street": candidate.street,
        "city": candidate.city,
        "state": candidate.state,
        "zipcode": candidate.zipcode,
        "normalized_address": candidate.normalized_address,
        "source": candidate.source,
        "confidence": candidate.confidence,
        "evidence_text": candidate.evidence_text,
    }
