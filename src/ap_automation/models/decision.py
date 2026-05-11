from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

DecisionOutcome = Literal["AUTO", "REVIEW", "FILE", "FLAG", "DISCARD"]


@dataclass(frozen=True)
class Destination:
    destination_code: str
    destination_type: str
    display_name: str
    email_address: str | None
    folder_path: str | None
    subject_instruction: str | None
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
class PropertyMatch:
    property_code: str
    property_name: str | None
    ownership_type: str
    management_type: str
    business_unit_code: str | None
    default_destination_code: str | None
    route_destination_code: str | None
    is_sold: bool
    matched_alias: str | None = None

    @property
    def destination_code(self) -> str | None:
        return self.route_destination_code or self.default_destination_code

    def to_audit_dict(self) -> dict[str, Any]:
        return {
            "property_code": self.property_code,
            "property_name": self.property_name,
            "ownership_type": self.ownership_type,
            "management_type": self.management_type,
            "business_unit_code": self.business_unit_code,
            "default_destination_code": self.default_destination_code,
            "route_destination_code": self.route_destination_code,
            "is_sold": self.is_sold,
            "matched_alias": self.matched_alias,
        }


@dataclass(frozen=True)
class RoutingContext:
    duplicate_status: str | None
    property_match: PropertyMatch | None
    dry_run: bool


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
    dry_run: bool
