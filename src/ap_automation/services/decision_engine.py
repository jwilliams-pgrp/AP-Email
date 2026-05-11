from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ap_automation.models.decision import Decision, Destination, RoutingContext, WorkflowRule
from ap_automation.models.extraction import ExtractionPayload, automatic_route_missing_fields
from ap_automation.repositories.protocols import PolicyRepository


class MissingWorkflowConfigError(RuntimeError):
    """Raised when required table-driven workflow configuration is unavailable."""


@dataclass(frozen=True)
class RuleEvaluation:
    rule_code: str
    condition_type: str
    matched: bool
    reason: str


@dataclass(frozen=True)
class DecisionResult:
    decision: Decision
    evaluations: list[RuleEvaluation]


class DecisionEngine:
    def __init__(self, policy_repository: PolicyRepository) -> None:
        self._policy_repository = policy_repository

    def decide(self, extraction: ExtractionPayload, idempotency_key: str) -> DecisionResult:
        config = self._policy_repository.get_runtime_config()
        dry_run = _required_bool_config(config, "dry_run")

        context = RoutingContext(
            duplicate_status=self._policy_repository.find_duplicate_status(extraction, idempotency_key),
            property_match=self._policy_repository.match_property(extraction),
            dry_run=dry_run,
        )

        rules = sorted(self._policy_repository.get_active_workflow_rules(), key=lambda rule: rule.priority)
        if not rules:
            raise MissingWorkflowConfigError("No active workflow rules are configured.")

        evaluations: list[RuleEvaluation] = []
        missing_auto_fields = automatic_route_missing_fields(extraction)

        for rule in rules:
            matched, reason = self._matches(rule, extraction, context, config, missing_auto_fields)
            evaluations.append(RuleEvaluation(rule.rule_code, rule.condition_type, matched, reason))
            if matched:
                decision = self._build_decision(rule, extraction, context, config, missing_auto_fields)
                return DecisionResult(decision=decision, evaluations=evaluations)

        raise MissingWorkflowConfigError("No fallback workflow rule matched. Configure an enabled fallback rule.")

    def _matches(
        self,
        rule: WorkflowRule,
        extraction: ExtractionPayload,
        context: RoutingContext,
        config: dict[str, Any],
        missing_auto_fields: list[str],
    ) -> tuple[bool, str]:
        condition_type = rule.condition_type

        if condition_type == "document_flag":
            flag = _required_condition(rule, "flag")
            matched = flag in extraction.document.document_flags
            return matched, f"flag {flag} {'matched' if matched else 'not present'}"

        if condition_type == "document_type":
            document_types = _required_condition(rule, "document_types")
            if not isinstance(document_types, list):
                raise MissingWorkflowConfigError(f"{rule.rule_code}.document_types must be a list.")
            matched = extraction.document.document_type in document_types
            return matched, f"document_type {extraction.document.document_type} {'matched' if matched else 'not matched'}"

        if condition_type == "duplicate_check":
            statuses = _required_condition(rule, "duplicate_statuses")
            if not isinstance(statuses, list):
                raise MissingWorkflowConfigError(f"{rule.rule_code}.duplicate_statuses must be a list.")
            matched = context.duplicate_status in statuses
            return matched, f"duplicate_status {context.duplicate_status or 'none'} {'matched' if matched else 'not matched'}"

        if condition_type == "property_status":
            required_sold = bool(_required_condition(rule, "is_sold"))
            matched = context.property_match is not None and context.property_match.is_sold == required_sold
            return matched, f"property sold status {'matched' if matched else 'not matched'}"

        if condition_type == "amount_threshold":
            threshold = _number_config(config, _runtime_key(rule))
            amount = extraction.invoice.amount
            matched = amount is not None and amount > threshold
            return matched, f"amount {amount} {'exceeds' if matched else 'does not exceed'} threshold {threshold}"

        if condition_type == "bill_to_business_unit":
            expected_unit = _required_condition(rule, "business_unit_code")
            actual_unit = extraction.business_signals.business_unit_code
            matched = bool(actual_unit) and actual_unit == expected_unit and not missing_auto_fields and _automatic_confidence_ok(extraction, config)
            return matched, f"business_unit {actual_unit or 'none'} {'matched' if matched else 'not matched'}"

        if condition_type == "property_routing_match":
            required = bool(_required_condition(rule, "requires_property_route"))
            matched = (
                required
                and context.property_match is not None
                and bool(context.property_match.destination_code)
                and not missing_auto_fields
                and _automatic_confidence_ok(extraction, config)
            )
            return matched, "property route matched" if matched else "no usable property route matched"

        if condition_type == "confidence_threshold":
            threshold = _number_config(config, _runtime_key(rule))
            confidence = extraction.confidence.overall
            matched = confidence < threshold
            return matched, f"confidence {confidence} {'below' if matched else 'meets'} threshold {threshold}"

        if condition_type == "fallback":
            return True, "fallback matched"

        raise MissingWorkflowConfigError(f"Unsupported workflow condition_type: {condition_type}")

    def _build_decision(
        self,
        rule: WorkflowRule,
        extraction: ExtractionPayload,
        context: RoutingContext,
        config: dict[str, Any],
        missing_auto_fields: list[str],
    ) -> Decision:
        destination_code = rule.destination_code
        if rule.condition_type == "property_routing_match" and context.property_match:
            destination_code = context.property_match.destination_code

        if rule.condition_type == "fallback" and missing_auto_fields:
            destination_code = _optional_review_destination(config, destination_code)
            reason = f"Missing required automatic-routing fields: {', '.join(missing_auto_fields)} -> REVIEW"
        else:
            reason = self._format_reason(rule, extraction, config)

        destination = self._destination(destination_code) if destination_code else None
        routing_match = {
            "duplicate_status": context.duplicate_status,
            "property_match": context.property_match.to_audit_dict() if context.property_match else None,
        }

        return Decision(
            outcome=rule.outcome,
            destination_code=destination.destination_code if destination else None,
            destination_email=destination.email_address if destination else None,
            reason=reason,
            confidence=extraction.confidence.overall,
            matched_rule_code=rule.rule_code,
            matched_rule_version=rule.version,
            extracted_fields=extraction.extracted_fields,
            routing_match=routing_match,
            dry_run=context.dry_run,
        )

    def _destination(self, destination_code: str) -> Destination:
        destination = self._policy_repository.get_destination(destination_code)
        if not destination.active:
            raise MissingWorkflowConfigError(f"Destination {destination_code} is not active.")
        return destination

    def _format_reason(self, rule: WorkflowRule, extraction: ExtractionPayload, config: dict[str, Any]) -> str:
        reason = rule.reason_template
        if rule.condition_type == "amount_threshold":
            reason = f"Invoice amount {extraction.invoice.amount} exceeds configured threshold {_number_config(config, _runtime_key(rule))} -> FILE to lien release folder"
        if rule.condition_type == "confidence_threshold":
            reason = f"Confidence {extraction.confidence.overall} below configured threshold {_number_config(config, _runtime_key(rule))} -> REVIEW"
        return reason


def _required_condition(rule: WorkflowRule, key: str) -> Any:
    if key not in rule.conditions:
        raise MissingWorkflowConfigError(f"Rule {rule.rule_code} is missing condition {key}.")
    return rule.conditions[key]


def _runtime_key(rule: WorkflowRule) -> str:
    value = _required_condition(rule, "runtime_config_key")
    if not isinstance(value, str) or not value:
        raise MissingWorkflowConfigError(f"Rule {rule.rule_code} runtime_config_key must be a non-empty string.")
    return value


def _number_config(config: dict[str, Any], key: str) -> float:
    if key not in config:
        raise MissingWorkflowConfigError(f"Missing runtime_config value: {key}")
    value = config[key]
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise MissingWorkflowConfigError(f"runtime_config {key} must be numeric.")
    return float(value)


def _required_bool_config(config: dict[str, Any], key: str) -> bool:
    if key not in config:
        raise MissingWorkflowConfigError(f"Missing runtime_config value: {key}")
    value = config[key]
    if not isinstance(value, bool):
        raise MissingWorkflowConfigError(f"runtime_config {key} must be boolean.")
    return value


def _optional_review_destination(config: dict[str, Any], current: str | None) -> str | None:
    if current:
        return current
    value = config.get("default_review_destination")
    return value if isinstance(value, str) else current


def _automatic_confidence_ok(extraction: ExtractionPayload, config: dict[str, Any]) -> bool:
    threshold = _number_config(config, "confidence_threshold")
    return (
        extraction.confidence.overall >= threshold
        and extraction.confidence.document_type >= threshold
        and extraction.confidence.invoice_fields >= threshold
        and extraction.confidence.property_identity >= threshold
        and extraction.confidence.business_unit >= threshold
    )
