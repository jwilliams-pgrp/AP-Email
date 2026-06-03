from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


ALLOWED_INTERPRETATION_OUTCOMES = {"AUTO", "ESCALATE", "FILE", "FLAG", "DISCARD"}


class LlmInterpretationValidationError(ValueError):
    """Raised when an LLM interpretation payload violates the advisory contract."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("Invalid LLM interpretation payload: " + "; ".join(errors))
        self.errors = errors


@dataclass(frozen=True)
class InterpretationEvidence:
    source: str
    text: str
    page: int | None = None


@dataclass(frozen=True)
class CandidatePropertyMatch:
    asset_id: str
    asset_alias: str | None
    confidence: float
    evidence: tuple[InterpretationEvidence, ...]

    @property
    def property_code(self) -> str | None:
        return self.asset_alias


@dataclass(frozen=True)
class CandidateRuleMatch:
    rule_code: str
    confidence: float
    evidence: tuple[InterpretationEvidence, ...]


@dataclass(frozen=True)
class LlmInterpretation:
    schema_version: Literal["llm_interpretation.v1"]
    candidate_property_matches: tuple[CandidatePropertyMatch, ...]
    candidate_rule_matches: tuple[CandidateRuleMatch, ...]
    ambiguity_flags: tuple[str, ...]
    recommended_outcome: str | None
    reason: str
    raw: dict[str, Any]


def validate_llm_interpretation(
    payload: dict[str, Any],
    *,
    allowed_asset_ids: set[str] | None = None,
    allowed_property_codes: set[str] | None = None,
    allowed_rule_codes: set[str] | None = None,
) -> LlmInterpretation:
    errors: list[str] = []
    allowed_rule_codes = allowed_rule_codes or set()
    allowed_asset_ids = {str(value).strip() for value in (allowed_asset_ids or set()) if str(value).strip()}
    allowed_property_codes = {str(value).strip().upper() for value in (allowed_property_codes or set()) if str(value).strip()}

    if payload.get("schema_version") != "llm_interpretation.v1":
        errors.append("schema_version must be llm_interpretation.v1")

    property_matches_raw = _required_list(payload, "candidate_property_matches", errors)
    property_matches: list[CandidatePropertyMatch] = []
    for index, raw_match in enumerate(property_matches_raw):
        if not isinstance(raw_match, dict):
            errors.append(f"candidate_property_matches[{index}] must be an object")
            continue
        asset_id = _required_str(raw_match, "asset_id", f"candidate_property_matches[{index}].asset_id", errors)
        if asset_id and asset_id not in allowed_asset_ids:
            errors.append(f"candidate_property_matches[{index}].asset_id must be from the provided candidate dataset")
        asset_alias = raw_match.get("asset_alias")
        if asset_alias is None and "property_code" in raw_match:
            asset_alias = raw_match.get("property_code")
        if asset_alias is not None:
            if not isinstance(asset_alias, str) or not asset_alias.strip():
                errors.append(f"candidate_property_matches[{index}].asset_alias must be a non-empty string or null")
                asset_alias = None
            else:
                asset_alias = asset_alias.strip().upper()
                if allowed_property_codes and asset_alias not in allowed_property_codes:
                    errors.append(f"candidate_property_matches[{index}].asset_alias must be from the provided candidate dataset")
        confidence = _required_confidence(raw_match, "confidence", f"candidate_property_matches[{index}].confidence", errors)
        evidence = _evidence(raw_match.get("evidence"), f"candidate_property_matches[{index}].evidence", errors)
        property_matches.append(CandidatePropertyMatch(asset_id=asset_id, asset_alias=asset_alias, confidence=confidence, evidence=tuple(evidence)))

    rule_matches_raw = _optional_list(payload.get("candidate_rule_matches"), "candidate_rule_matches", errors)
    rule_matches: list[CandidateRuleMatch] = []
    for index, raw_match in enumerate(rule_matches_raw):
        if not isinstance(raw_match, dict):
            errors.append(f"candidate_rule_matches[{index}] must be an object")
            continue
        rule_code = _required_str(raw_match, "rule_code", f"candidate_rule_matches[{index}].rule_code", errors)
        if allowed_rule_codes and rule_code not in allowed_rule_codes:
            errors.append(f"candidate_rule_matches[{index}].rule_code must be from the provided workflow rules")
        confidence = _required_confidence(raw_match, "confidence", f"candidate_rule_matches[{index}].confidence", errors)
        evidence = _evidence(raw_match.get("evidence"), f"candidate_rule_matches[{index}].evidence", errors)
        rule_matches.append(CandidateRuleMatch(rule_code=rule_code, confidence=confidence, evidence=tuple(evidence)))

    ambiguity_flags = tuple(_optional_string_list(payload.get("ambiguity_flags"), "ambiguity_flags", errors))
    recommended_outcome = payload.get("recommended_outcome")
    if recommended_outcome is not None:
        if not isinstance(recommended_outcome, str) or recommended_outcome not in ALLOWED_INTERPRETATION_OUTCOMES:
            errors.append("recommended_outcome must be one of AUTO, ESCALATE, FILE, FLAG, DISCARD, or null")
            recommended_outcome = None

    reason = _required_str(payload, "reason", "reason", errors)

    if errors:
        raise LlmInterpretationValidationError(errors)

    return LlmInterpretation(
        schema_version="llm_interpretation.v1",
        candidate_property_matches=tuple(property_matches),
        candidate_rule_matches=tuple(rule_matches),
        ambiguity_flags=ambiguity_flags,
        recommended_outcome=recommended_outcome,
        reason=reason,
        raw=payload,
    )


def _evidence(value: Any, path: str, errors: list[str]) -> list[InterpretationEvidence]:
    if not isinstance(value, list):
        errors.append(f"{path} must be a non-empty list")
        return []
    if not value:
        errors.append(f"{path} must be a non-empty list")
        return []

    evidence: list[InterpretationEvidence] = []
    for index, raw_item in enumerate(value):
        item_path = f"{path}[{index}]"
        if not isinstance(raw_item, dict):
            errors.append(f"{item_path} must be an object")
            continue
        source = _required_str(raw_item, "source", f"{item_path}.source", errors)
        text = _required_str(raw_item, "text", f"{item_path}.text", errors)
        page = raw_item.get("page")
        if page is not None and (not isinstance(page, int) or isinstance(page, bool)):
            errors.append(f"{item_path}.page must be an integer or null")
            page = None
        evidence.append(InterpretationEvidence(source=source, text=text, page=page))
    return evidence


def _required_list(payload: dict[str, Any], key: str, errors: list[str]) -> list[Any]:
    value = payload.get(key)
    if not isinstance(value, list):
        errors.append(f"{key} must be a list")
        return []
    return value


def _optional_list(value: Any, path: str, errors: list[str]) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        errors.append(f"{path} must be a list")
        return []
    return value


def _required_str(payload: dict[str, Any], key: str, path: str, errors: list[str]) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{path} must be a non-empty string")
        return ""
    return value.strip()


def _required_confidence(payload: dict[str, Any], key: str, path: str, errors: list[str]) -> float:
    value = payload.get(key)
    if not isinstance(value, int | float) or isinstance(value, bool):
        errors.append(f"{path} must be a number")
        return 0.0
    result = float(value)
    if result < 0.0 or result > 1.0:
        errors.append(f"{path} must be between 0.0 and 1.0")
        return 0.0
    return result


def _optional_string_list(value: Any, path: str, errors: list[str]) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        errors.append(f"{path} must be a list of strings")
        return []
    return [item.strip() for item in value if item.strip()]
