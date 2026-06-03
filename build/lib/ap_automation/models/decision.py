from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

DecisionOutcome = Literal["AUTO", "ESCALATE", "FILE", "FLAG", "DISCARD"]


@dataclass(frozen=True)
class Destination:
    destination_code: str
    display_name: str
    email_address: str | None
    parent_folder: str | None
    label: str | None
    send_teams_message: bool = False
    send_email: bool = False
    active: bool = True


@dataclass(frozen=True)
class WorkflowRule:
    rule_code: str
    rule_name: str
    priority: int
    condition_type: str
    outcome: DecisionOutcome
    destination_code: str | None
    reason_template: str
    version: int
    conditions: dict[str, Any]


@dataclass(frozen=True)
class NoActionEmailPattern:
    pattern_id: str
    pattern_name: str
    sender_email_equals: str | None
    sender_domain_equals: str | None
    subject_regex: str | None
    body_regex: str | None
    reason_template: str
    priority: int


@dataclass(frozen=True)
class PropertyMatch:
    asset_id: str
    asset_alias: str | None
    asset_name: str | None
    ownership_type: str
    ownership: str | None
    asset_type: str | None
    business_unit_code: str | None
    destination_code_value: str | None
    market_name: str | None = None
    market_area: str | None = None
    matched_alias: str | None = None
    asset_source: str = "asset"
    asset_lookup_id: str | None = None

    @property
    def property_code(self) -> str | None:
        return self.asset_alias

    @property
    def property_name(self) -> str | None:
        return self.asset_name

    @property
    def destination_code(self) -> str | None:
        return self.destination_code_value

    def to_audit_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "asset_source": self.asset_source,
            "asset_lookup_id": self.asset_lookup_id or self.asset_id,
            "asset_alias": self.asset_alias,
            "asset_name": self.asset_name,
            "property_code": self.asset_alias,
            "property_name": self.asset_name,
            "ownership_type": self.ownership_type,
            "ownership": self.ownership,
            "asset_type": self.asset_type,
            "business_unit_code": self.business_unit_code,
            "destination_code": self.destination_code,
            "market_name": self.market_name,
            "market_area": self.market_area,
            "matched_alias": self.matched_alias,
        }


@dataclass(frozen=True)
class PropertyMatchCandidate:
    asset_id: str
    asset_alias: str | None
    asset_name: str | None
    destination_code: str | None
    matched_text: str
    matched_column: str
    similarity_score: float
    ownership: str | None = None
    asset_type: str | None = None
    market_name: str | None = None
    market_area: str | None = None
    asset_source: str = "asset"
    asset_lookup_id: str | None = None

    @property
    def property_id(self) -> str:
        return self.asset_id

    @property
    def property_code(self) -> str | None:
        return self.asset_alias

    @property
    def property_name(self) -> str | None:
        return self.asset_name

    def to_audit_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "asset_source": self.asset_source,
            "asset_lookup_id": self.asset_lookup_id or self.asset_id,
            "asset_alias": self.asset_alias,
            "asset_name": self.asset_name,
            "property_id": self.asset_id,
            "property_code": self.asset_alias,
            "property_name": self.asset_name,
            "ownership": self.ownership,
            "asset_type": self.asset_type,
            "market_name": self.market_name,
            "market_area": self.market_area,
            "destination_code": self.destination_code,
            "matched_text": self.matched_text,
            "matched_column": self.matched_column,
            "similarity_score": self.similarity_score,
        }


@dataclass(frozen=True)
class PropertyMatchEvaluation:
    property_match: PropertyMatch | None
    standardized_signals: dict[str, Any]
    candidates: tuple[PropertyMatchCandidate, ...]
    llm_advisory: dict[str, Any]
    gate: dict[str, Any]
    lookup_audit: dict[str, Any] | None = None


@dataclass(frozen=True)
class RoutingContext:
    duplicate_status: str | None
    property_evaluation: PropertyMatchEvaluation
    normal_destination_code: str | None = None


@dataclass(frozen=True)
class Decision:
    outcome: DecisionOutcome
    destination_code: str | None
    destination_email: str | None
    reason: str
    confidence: float
    matched_rule_code: str
    matched_rule_version: int
    extracted_fields: dict[str, Any]
    routing_match: dict[str, Any]
