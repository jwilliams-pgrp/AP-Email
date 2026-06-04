from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Protocol

from ap_automation.models.decision import Decision, Destination, NoActionEmailPattern, PropertyMatch, PropertyMatchEvaluation, RoutingContext, WorkflowRule
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


@dataclass(frozen=True)
class DecisionContext:
    latest_body_text: str | None = None
    quoted_history_text: str | None = None
    has_quoted_history: bool = False

    def to_audit_dict(self) -> dict[str, Any]:
        return {
            "latest_body_text": self.latest_body_text,
            "quoted_history_text": self.quoted_history_text,
            "has_quoted_history": self.has_quoted_history,
        }


class PropertyMatchReviewer(Protocol):
    def suggest(self, extraction: ExtractionPayload, alias_mappings: list[dict[str, Any]]) -> Any | None:
        ...


class DecisionEngine:
    def __init__(self, policy_repository: PolicyRepository, property_match_reviewer: PropertyMatchReviewer | None = None) -> None:
        self._policy_repository = policy_repository
        self._property_match_reviewer = property_match_reviewer

    def decide(
        self,
        extraction: ExtractionPayload,
        idempotency_key: str,
        pre_decision_facts: dict[str, bool] | None = None,
        property_match_evaluation: PropertyMatchEvaluation | None = None,
        decision_context: DecisionContext | None = None,
    ) -> DecisionResult:
        config = self._policy_repository.get_runtime_config()
        _number_config(config, "confidence_threshold")

        rules = sorted(
            (
                rule
                for rule in self._policy_repository.get_active_workflow_rules()
                if not _is_aggregation_only_rule(rule)
            ),
            key=lambda rule: rule.priority,
        )
        if not rules:
            raise MissingWorkflowConfigError("No active workflow rules are configured.")

        missing_auto_fields = automatic_route_missing_fields(extraction)
        property_evaluation = self._review_property_match(
            extraction,
            property_match_evaluation or self._policy_repository.evaluate_property_match(extraction),
        )
        context = RoutingContext(
            duplicate_status=self._policy_repository.find_duplicate_status(extraction, idempotency_key),
            property_evaluation=property_evaluation,
            normal_destination_code=_normal_invoice_destination(rules, extraction, property_evaluation.property_match, missing_auto_fields),
        )

        evaluations: list[RuleEvaluation] = []
        facts = pre_decision_facts or {}
        item_decision_context = decision_context or _decision_context_from_extraction(extraction)

        for rule in rules:
            matched, reason = self._matches(rule, extraction, context, config, missing_auto_fields, facts, item_decision_context)
            evaluations.append(RuleEvaluation(rule.rule_code, rule.condition_type, matched, reason))
            if matched:
                decision = self._build_decision(rule, extraction, context, config, missing_auto_fields, item_decision_context)
                return DecisionResult(decision=decision, evaluations=evaluations)

        raise MissingWorkflowConfigError("No fallback workflow rule matched. Configure an enabled fallback rule.")

    def _review_property_match(
        self,
        extraction: ExtractionPayload,
        evaluation: PropertyMatchEvaluation,
    ) -> PropertyMatchEvaluation:
        if not evaluation.candidates:
            return evaluation
        if self._property_match_reviewer is None:
            return PropertyMatchEvaluation(
                property_match=None,
                standardized_signals=evaluation.standardized_signals,
                candidates=evaluation.candidates,
                llm_advisory={
                    "used": False,
                    "required": True,
                    "candidate_asset_ids": [candidate.asset_id for candidate in evaluation.candidates],
                    "reason": "LLM final property review is required but no reviewer is configured",
                },
                gate={**evaluation.gate, "passed": False, "reason": "LLM final property review required before property routing"},
                lookup_audit=evaluation.lookup_audit,
            )

        candidate_rows = [candidate.to_audit_dict() for candidate in evaluation.candidates]
        suggestion = self._property_match_reviewer.suggest(extraction, candidate_rows)
        if suggestion is None:
            return PropertyMatchEvaluation(
                property_match=None,
                standardized_signals=evaluation.standardized_signals,
                candidates=evaluation.candidates,
                llm_advisory={
                    "used": True,
                    "required": True,
                    "candidate_asset_ids": [candidate.asset_id for candidate in evaluation.candidates],
                    "reason": "LLM final property review did not return one validated candidate",
                },
                gate={**evaluation.gate, "passed": False, "reason": "LLM final property review did not select a valid candidate"},
                lookup_audit=evaluation.lookup_audit,
            )

        selected_ids = tuple(getattr(suggestion, "candidate_asset_ids", ()))
        selected_candidates = [candidate for candidate in evaluation.candidates if candidate.asset_id in selected_ids]
        if len(selected_candidates) != 1:
            return PropertyMatchEvaluation(
                property_match=None,
                standardized_signals=evaluation.standardized_signals,
                candidates=evaluation.candidates,
                llm_advisory={
                    "used": True,
                    "required": True,
                    "candidate_asset_ids": list(selected_ids),
                    "confidence": getattr(suggestion, "confidence", None),
                    "reason": "LLM final property review was ambiguous",
                },
                gate={**evaluation.gate, "passed": False, "reason": "LLM final property review selected zero or multiple candidates"},
                lookup_audit=evaluation.lookup_audit,
            )

        selected = selected_candidates[0]
        property_match = self._policy_repository.get_property_match_by_asset_id(selected.asset_id, selected.matched_text)
        min_score = float(evaluation.gate.get("min_score", 0.0))
        passed = property_match is not None and selected.similarity_score >= min_score
        bill_to_fallback_selected = _is_bill_to_address_fallback(extraction, selected)
        if passed and bill_to_fallback_selected and _has_stronger_conflicting_candidate(extraction, selected, evaluation.candidates, min_score):
            passed = False
            gate_reason = "Bill-to address fallback blocked by stronger conflicting property evidence"
        elif passed and bill_to_fallback_selected and _has_near_equivalent_active_candidate(selected, evaluation.candidates):
            passed = False
            gate_reason = "Bill-to address fallback was ambiguous across multiple active property candidates"
        elif passed and bill_to_fallback_selected:
            gate_reason = "bill_to_address_fallback_selected"
        elif passed:
            gate_reason = "LLM final property review selected a valid candidate"
        else:
            gate_reason = "LLM-selected candidate failed deterministic property gate"

        advisory_reason = getattr(suggestion, "reason", "LLM final property review selected a candidate")
        if passed and bill_to_fallback_selected:
            advisory_reason = "bill_to_address_fallback_selected"
        return PropertyMatchEvaluation(
            property_match=property_match if passed else None,
            standardized_signals=evaluation.standardized_signals,
            candidates=evaluation.candidates,
            llm_advisory={
                "used": True,
                "required": True,
                "candidate_asset_ids": list(selected_ids),
                "confidence": getattr(suggestion, "confidence", None),
                "reason": advisory_reason,
            },
            gate={
                **evaluation.gate,
                "llm_selected_asset_id": selected.asset_id,
                "passed": passed,
                "bill_to_address_fallback": bill_to_fallback_selected,
                "reason": gate_reason,
            },
            lookup_audit=evaluation.lookup_audit,
        )

    def _matches(
        self,
        rule: WorkflowRule,
        extraction: ExtractionPayload,
        context: RoutingContext,
        config: dict[str, Any],
        missing_auto_fields: list[str],
        facts: dict[str, bool],
        decision_context: DecisionContext,
    ) -> tuple[bool, str]:
        condition_type = rule.condition_type

        if condition_type == "document_flag":
            flag = _required_condition(rule, "flag")
            matched = flag in extraction.document.document_flags
            return matched, f"flag {flag} {'matched' if matched else 'not present'}"

        if condition_type == "attachment_extension":
            disallowed_extensions = _required_condition(rule, "disallowed_extensions")
            if not isinstance(disallowed_extensions, list):
                raise MissingWorkflowConfigError(f"{rule.rule_code}.disallowed_extensions must be a list.")
            exempt_document_types = _optional_string_set(rule, "exempt_document_types")
            if extraction.document.document_type in exempt_document_types:
                return False, f"document_type {extraction.document.document_type} is exempt from attachment extension rule"
            exempt_document_flags = _optional_string_set(rule, "exempt_document_flags")
            matched_exempt_flags = sorted(flag for flag in extraction.document.document_flags if flag in exempt_document_flags)
            if matched_exempt_flags:
                return False, f"document flags exempt from attachment extension rule: {matched_exempt_flags}"
            normalized_disallowed = {
                str(ext).strip().lower()
                for ext in disallowed_extensions
                if isinstance(ext, str) and str(ext).strip()
            }
            attachments = extraction.evidence.source_attachments
            matched_attachments = [
                name
                for name in attachments
                if _suffix(name) in normalized_disallowed
            ]
            matched = bool(matched_attachments)
            return matched, (
                f"item evidence attachment extension matched disallowed set: {matched_attachments}"
                if matched
                else "no disallowed attachment extensions matched in item evidence"
            )

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

        if condition_type == "amount_threshold":
            threshold = _number_config(config, _runtime_key(rule))
            exempt_destination = _required_condition(rule, "exempt_destination")
            if not isinstance(exempt_destination, str) or not exempt_destination:
                raise MissingWorkflowConfigError(f"Rule {rule.rule_code} exempt_destination must be a non-empty string.")
            exempt_requires_project_number = bool(rule.conditions.get("exempt_requires_project_number", False))
            amount = extraction.invoice.amount
            amount_exceeds = amount is not None and amount > threshold
            normal_destination = context.normal_destination_code
            has_project_number = _has_project_number(extraction)
            exempt_destination_applies = normal_destination == exempt_destination and (
                not exempt_requires_project_number or has_project_number
            )
            matched = amount_exceeds and normal_destination is not None and not exempt_destination_applies
            if not amount_exceeds:
                return False, f"amount {amount} does not exceed threshold {threshold}"
            if normal_destination is None:
                return False, "amount exceeds threshold but no usable automatic destination was available"
            if exempt_destination_applies:
                return False, f"amount exceeds threshold but destination {normal_destination} is exempt with project number present"
            if normal_destination == exempt_destination and exempt_requires_project_number and not has_project_number:
                return True, f"amount {amount} exceeds threshold {threshold}; destination {normal_destination} requires project number for exemption"
            return True, f"amount {amount} exceeds threshold {threshold} and destination {normal_destination} is not exempt"

        if condition_type == "bill_to_business_unit":
            expected_unit = _required_condition(rule, "business_unit_code")
            actual_unit = extraction.business_signals.business_unit_code
            matched = bool(actual_unit) and actual_unit == expected_unit and not missing_auto_fields
            return matched, f"business_unit {actual_unit or 'none'} {'matched' if matched else 'not matched'}"

        if condition_type == "property_asset_type":
            document_types = _optional_string_set(rule, "document_types")
            if document_types and extraction.document.document_type not in document_types:
                return False, f"document_type {extraction.document.document_type} not eligible for asset_type rule"
            expected_asset_type = str(_required_condition(rule, "asset_type")).strip().lower()
            property_match = context.property_evaluation.property_match
            actual_asset_type = (property_match.asset_type or "").strip().lower() if property_match else ""
            matched = bool(expected_asset_type) and actual_asset_type == expected_asset_type and not missing_auto_fields
            return matched, f"asset_type {actual_asset_type or 'none'} {'matched' if matched else 'not matched'}"

        if condition_type == "alc_signal":
            matched, matched_reasons = _match_alc_signal(rule, extraction, context)
            return matched, "; ".join(matched_reasons) if matched else "no ALC signal matched"

        if condition_type == "property_routing_match":
            required = bool(_required_condition(rule, "requires_property_route"))
            matched = (
                required
                and context.property_evaluation.property_match is not None
                and bool(context.property_evaluation.property_match.destination_code)
                and not missing_auto_fields
            )
            return matched, "property route matched" if matched else "no usable property route matched"

        if condition_type == "check_request_property_routing":
            document_types = _required_condition(rule, "document_types")
            if not isinstance(document_types, list):
                raise MissingWorkflowConfigError(f"{rule.rule_code}.document_types must be a list.")
            allowed_destination_codes = _required_condition(rule, "allowed_destination_codes")
            if not isinstance(allowed_destination_codes, list):
                raise MissingWorkflowConfigError(f"{rule.rule_code}.allowed_destination_codes must be a list.")
            allowed_destinations = {str(value).strip() for value in allowed_destination_codes if isinstance(value, str) and value.strip()}
            property_match = context.property_evaluation.property_match
            matched = (
                extraction.document.document_type in document_types
                and property_match is not None
                and bool(property_match.destination_code)
                and property_match.destination_code in allowed_destinations
            )
            return matched, (
                f"check request matched property destination {property_match.destination_code}"
                if matched and property_match
                else "check request did not match an allowed property destination"
            )

        if condition_type == "informational_property_notice":
            document_types = _required_condition(rule, "document_types")
            if not isinstance(document_types, list):
                raise MissingWorkflowConfigError(f"{rule.rule_code}.document_types must be a list.")
            blocked_flags_raw = _required_condition(rule, "blocked_flags")
            if not isinstance(blocked_flags_raw, list):
                raise MissingWorkflowConfigError(f"{rule.rule_code}.blocked_flags must be a list.")
            blocked_flags = {str(flag) for flag in blocked_flags_raw}
            has_blocked_flag = any(flag in blocked_flags for flag in extraction.document.document_flags)
            matched = (
                extraction.document.document_type in document_types
                and context.property_evaluation.property_match is not None
                and bool(context.property_evaluation.property_match.destination_code)
                and not has_blocked_flag
            )
            return matched, (
                "informational property notice matched configured property destination"
                if matched
                else "informational property notice not matched"
            )

        if condition_type == "property_unmatched":
            allowed_document_types = _optional_string_set(rule, "document_types")
            require_invoice_type = bool(_required_condition(rule, "invoice_only")) if not allowed_document_types else False
            has_property_signal = any(
                [
                    extraction.property_lookup.property_code,
                    extraction.property_lookup.property_name,
                    extraction.property_lookup.tenant,
                    extraction.property_lookup.address,
                    extraction.property_lookup.suite,
                    extraction.property_lookup.city,
                    extraction.property_lookup.state,
                    extraction.property_lookup.zipcode,
                    extraction.business_signals.possible_property_aliases,
                ]
            )
            matched = (
                context.property_evaluation.property_match is None
                and has_property_signal
                and (
                    extraction.document.document_type in allowed_document_types
                    if allowed_document_types
                    else (not require_invoice_type or extraction.document.document_type == "invoice")
                )
                and not missing_auto_fields
            )
            return matched, "property signal present but no routing table match" if matched else "property unmatched rule not triggered"

        if condition_type == "confidence_threshold":
            threshold = _number_config(config, _runtime_key(rule))
            confidence = extraction.confidence.overall
            delta = round(confidence - threshold, 4)
            return False, f"confidence {confidence} vs threshold {threshold} (delta {delta}); threshold is audit-only and does not enforce ESCALATE"

        if condition_type == "email_pattern_match":
            matched_pattern = _match_no_action_email_pattern(self._policy_repository.get_active_no_action_email_patterns(), extraction)
            if matched_pattern is None:
                return False, "no active no-action email pattern matched"
            return True, f"matched no-action email pattern {matched_pattern.pattern_name}"

        if condition_type == "current_reply_no_action":
            matched, reason = _matches_current_reply_no_action(rule, extraction, decision_context)
            return matched, reason

        if condition_type == "observed_fact":
            fact_key = _required_condition(rule, "fact_key")
            if not isinstance(fact_key, str):
                raise MissingWorkflowConfigError(f"{rule.rule_code}.fact_key must be a string.")
            document_types = _optional_string_list_condition(rule, "document_types")
            if document_types and extraction.document.document_type not in document_types:
                return False, f"document_type {extraction.document.document_type} not matched"
            blocked_flags = set(_optional_string_list_condition(rule, "blocked_flags"))
            matched_blocked_flags = sorted(flag for flag in extraction.document.document_flags if flag in blocked_flags)
            if matched_blocked_flags:
                return False, f"blocked flags present: {', '.join(matched_blocked_flags)}"
            if bool(rule.conditions.get("forbid_source_attachments", False)) and extraction.evidence.source_attachments:
                return False, "document item cites attachments"
            actual = bool(getattr(extraction.observed_facts, fact_key, False))
            expected = rule.conditions.get("expected", True)
            if not isinstance(expected, bool):
                raise MissingWorkflowConfigError(f"{rule.rule_code}.expected must be boolean when provided.")
            matched = actual == expected
            return matched, f"observed_fact {fact_key}={actual} expected={expected}"

        if condition_type == "pre_decision_fact":
            fact_key = _required_condition(rule, "fact_key")
            if not isinstance(fact_key, str):
                raise MissingWorkflowConfigError(f"{rule.rule_code}.fact_key must be a string.")
            expected = rule.conditions.get("expected", True)
            if not isinstance(expected, bool):
                raise MissingWorkflowConfigError(f"{rule.rule_code}.expected must be boolean when provided.")
            actual = bool(facts.get(fact_key, False))
            matched = actual == expected
            return matched, f"pre_decision_fact {fact_key}={actual} expected={expected}"

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
        decision_context: DecisionContext,
    ) -> Decision:
        destination_code = rule.destination_code
        if rule.condition_type in {"property_routing_match", "informational_property_notice", "check_request_property_routing"} and context.property_evaluation.property_match:
            destination_code = context.property_evaluation.property_match.destination_code

        if rule.condition_type == "fallback" and missing_auto_fields:
            destination_code = _optional_escalate_destination(config, destination_code)
            reason = f"Missing required automatic-routing fields: {', '.join(missing_auto_fields)} -> ESCALATE"
        else:
            reason = self._format_reason(rule, extraction, context, config)

        destination = self._destination(destination_code) if destination_code else None
        routing_match = {
            "duplicate_status": context.duplicate_status,
            "normal_destination_code": context.normal_destination_code,
            "property_match": (
                context.property_evaluation.property_match.to_audit_dict()
                if context.property_evaluation.property_match
                else None
            ),
            "property_candidates": [candidate.to_audit_dict() for candidate in context.property_evaluation.candidates],
            "property_lookup": context.property_evaluation.lookup_audit,
            "property_gate": context.property_evaluation.gate,
            "property_standardized_signals": context.property_evaluation.standardized_signals,
            "property_llm_advisory": context.property_evaluation.llm_advisory,
            "decision_context": decision_context.to_audit_dict(),
        }
        extracted_fields = {
            **extraction.extracted_fields,
            "decision_context": decision_context.to_audit_dict(),
        }

        return Decision(
            outcome=_normalize_outcome(rule.outcome),
            destination_code=destination.destination_code if destination else None,
            destination_email=destination.email_address if destination and destination.send_email else None,
            reason=reason,
            confidence=extraction.confidence.overall,
            matched_rule_code=rule.rule_code,
            matched_rule_version=rule.version,
            extracted_fields=extracted_fields,
            routing_match=routing_match,
        )

    def _destination(self, destination_code: str) -> Destination:
        destination = self._policy_repository.get_destination(destination_code)
        if not destination.active:
            raise MissingWorkflowConfigError(f"Destination {destination_code} is not active.")
        return destination

    def _format_reason(
        self,
        rule: WorkflowRule,
        extraction: ExtractionPayload,
        context: RoutingContext,
        config: dict[str, Any],
    ) -> str:
        reason = rule.reason_template
        if rule.condition_type == "amount_threshold":
            reason = (
                f"Invoice amount {extraction.invoice.amount} exceeds configured threshold "
                f"{_number_config(config, _runtime_key(rule))}; normal destination "
                f"{context.normal_destination_code} is not exempt or lacks required project number -> "
                f"ESCALATE with OVER-10000 label"
            )
        if rule.condition_type == "confidence_threshold":
            reason = (
                f"Confidence {extraction.confidence.overall} compared to configured threshold "
                f"{_number_config(config, _runtime_key(rule))}; threshold is audit-only"
            )
        return reason


def _required_condition(rule: WorkflowRule, key: str) -> Any:
    if key not in rule.conditions:
        raise MissingWorkflowConfigError(f"Rule {rule.rule_code} is missing condition {key}.")
    return rule.conditions[key]


def _decision_context_from_extraction(extraction: ExtractionPayload) -> DecisionContext:
    thread_context = extraction.raw.get("thread_context")
    if not isinstance(thread_context, dict):
        return DecisionContext()
    latest = thread_context.get("latest_body_text")
    quoted = thread_context.get("quoted_history_text")
    return DecisionContext(
        latest_body_text=latest if isinstance(latest, str) and latest.strip() else None,
        quoted_history_text=quoted if isinstance(quoted, str) and quoted.strip() else None,
        has_quoted_history=thread_context.get("has_quoted_history") is True,
    )


def _optional_string_set(rule: WorkflowRule, key: str) -> set[str]:
    if key not in rule.conditions:
        return set()
    value = rule.conditions[key]
    if not isinstance(value, list):
        raise MissingWorkflowConfigError(f"{rule.rule_code}.{key} must be a list when provided.")
    return {str(item).strip() for item in value if isinstance(item, str) and str(item).strip()}


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


def _has_project_number(extraction: ExtractionPayload) -> bool:
    return bool((extraction.invoice.project_number or "").strip())


def _normal_invoice_destination(
    rules: list[WorkflowRule],
    extraction: ExtractionPayload,
    property_match: PropertyMatch | None,
    missing_auto_fields: list[str],
) -> str | None:
    if extraction.document.document_type != "invoice" or missing_auto_fields:
        return None
    for rule in rules:
        if rule.condition_type == "bill_to_business_unit":
            expected_unit = _required_condition(rule, "business_unit_code")
            if extraction.business_signals.business_unit_code == expected_unit and rule.destination_code:
                return rule.destination_code
        if rule.condition_type == "property_asset_type" and property_match:
            document_types = _optional_string_set(rule, "document_types")
            if document_types and extraction.document.document_type not in document_types:
                continue
            expected_asset_type = str(_required_condition(rule, "asset_type")).strip().lower()
            actual_asset_type = (property_match.asset_type or "").strip().lower()
            if expected_asset_type and actual_asset_type == expected_asset_type and rule.destination_code:
                return rule.destination_code
        if rule.condition_type == "property_routing_match" and property_match:
            required = bool(_required_condition(rule, "requires_property_route"))
            if required and property_match.destination_code:
                return property_match.destination_code
    return None


def _is_bill_to_address_fallback(extraction: ExtractionPayload, selected: Any) -> bool:
    if selected is None or "address" not in str(getattr(selected, "matched_column", "")).lower():
        return False
    if extraction.invoice.property_code or extraction.invoice.service_address:
        return False
    lookup = extraction.property_lookup
    if lookup.property_code:
        return False
    strong_labels = {"deliver_to", "ship_to", "service_location", "site", "property"}
    labels = {candidate.label.strip().lower() for candidate in lookup.address_candidates}
    if labels & strong_labels:
        return False
    if labels & {"bill_to", "customer_account"}:
        return True
    return bool(extraction.invoice.bill_to or extraction.invoice.bill_to_street_address) and bool(lookup.address)


def _has_stronger_conflicting_candidate(extraction: ExtractionPayload, selected: Any, candidates: tuple[Any, ...], min_score: float) -> bool:
    stronger_columns = {"property_code", "property_or_tenant_name", "asset_alias", "asset_name", "property_name", "tenant"}
    bill_to_text = _normalize_address_phrase(
        " ".join(
            value
            for value in (
                extraction.invoice.bill_to,
                extraction.invoice.bill_to_name_line_1,
                extraction.invoice.bill_to_name_line_2,
            )
            if value
        )
    )
    for candidate in candidates:
        if getattr(candidate, "asset_id", None) == getattr(selected, "asset_id", None):
            continue
        if not getattr(candidate, "destination_code", None):
            continue
        if float(getattr(candidate, "similarity_score", 0.0)) < min_score:
            continue
        matched_text = _normalize_address_phrase(str(getattr(candidate, "matched_text", "")))
        if matched_text and bill_to_text and (matched_text in bill_to_text or bill_to_text in matched_text):
            continue
        if str(getattr(candidate, "matched_column", "")).lower() in stronger_columns:
            return True
    return False


def _has_near_equivalent_active_candidate(selected: Any, candidates: tuple[Any, ...]) -> bool:
    for candidate in candidates:
        if getattr(candidate, "asset_id", None) == getattr(selected, "asset_id", None):
            continue
        if not getattr(candidate, "destination_code", None):
            continue
        if "address" not in str(getattr(candidate, "matched_column", "")).lower():
            continue
        score_delta = abs(float(getattr(selected, "similarity_score", 0.0)) - float(getattr(candidate, "similarity_score", 0.0)))
        if score_delta < 0.08:
            return True
    return False


def _is_aggregation_only_rule(rule: WorkflowRule) -> bool:
    return rule.condition_type.startswith("aggregation_")


def _match_alc_signal(
    rule: WorkflowRule,
    extraction: ExtractionPayload,
    context: RoutingContext,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    unit_codes = _optional_upper_string_set(rule, "business_unit_codes")
    if extraction.business_signals.business_unit_code:
        actual_unit = extraction.business_signals.business_unit_code.strip().upper()
        if actual_unit in unit_codes:
            reasons.append(f"business_unit_code {actual_unit} matched")

    property_match = context.property_evaluation.property_match

    suppress_text_only = _has_stronger_non_exempt_address_signal(rule, extraction, context)
    evidence = " ".join(_alc_text_evidence(extraction, property_match))
    if not suppress_text_only:
        phrase_patterns = _optional_string_list_condition(rule, "text_phrases")
        for phrase in phrase_patterns:
            if phrase.lower() in evidence.lower():
                reasons.append(f"text phrase {phrase} matched")
                break

        standalone_terms = _optional_string_list_condition(rule, "standalone_terms")
        for term in standalone_terms:
            if re.search(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", evidence, flags=re.IGNORECASE):
                reasons.append(f"standalone term {term} matched")
                break

    return bool(reasons), reasons


def _has_stronger_non_exempt_address_signal(rule: WorkflowRule, extraction: ExtractionPayload, context: RoutingContext) -> bool:
    if context.property_evaluation.property_match is None:
        return False

    exempt_phrases = [
        _normalize_address_phrase(phrase)
        for phrase in _optional_string_list_condition(rule, "text_signal_exempt_property_addresses")
    ]
    if not exempt_phrases:
        return False

    strong_labels = {"property", "site", "service_location", "ship_to", "deliver_to"}
    for candidate in extraction.property_lookup.address_candidates:
        if candidate.label.strip().lower() not in strong_labels:
            continue
        normalized = _normalize_address_phrase(" ".join(value for value in (candidate.street, candidate.normalized_address) if value))
        if not normalized:
            continue
        if normalized and not any(phrase in normalized for phrase in exempt_phrases):
            return True
    return False


def _normalize_address_phrase(value: str) -> str:
    normalized = value.strip().lower()
    replacements = {
        r"\bpkwy\b": "parkway",
        r"\bste\b": "suite",
        r"\bst\b": "street",
        r"\brd\b": "road",
        r"\bblvd\b": "boulevard",
        r"\bcir\b": "circle",
        r"\bdr\b": "drive",
        r"\bave\b": "avenue",
    }
    for pattern, replacement in replacements.items():
        normalized = re.sub(pattern, replacement, normalized)
    return re.sub(r"[^a-z0-9]+", " ", normalized).strip()


def _alc_text_evidence(extraction: ExtractionPayload, property_match: PropertyMatch | None) -> list[str]:
    invoice = extraction.invoice
    property_lookup = extraction.property_lookup
    values: list[str] = [
        extraction.email.subject,
        extraction.evidence.summary,
        invoice.vendor_name,
        invoice.bill_to,
        invoice.bill_to_name_line_1,
        invoice.bill_to_name_line_2,
        invoice.bill_to_street_address,
        invoice.bill_to_suite,
        invoice.bill_to_city,
        invoice.bill_to_state,
        invoice.bill_to_zip_code,
        invoice.property_code,
        invoice.property_name,
        invoice.service_address,
        property_match.asset_alias if property_match else None,
        property_match.asset_name if property_match else None,
        property_match.ownership if property_match else None,
        property_match.market_name if property_match else None,
        property_match.market_area if property_match else None,
        property_match.matched_alias if property_match else None,
    ]
    values.extend(property_lookup.property_code)
    values.extend(property_lookup.property_name)
    values.extend(property_lookup.tenant)
    values.extend(property_lookup.address)
    values.extend(property_lookup.suite)
    values.extend(property_lookup.city)
    values.extend(property_lookup.state)
    values.extend(property_lookup.zipcode)
    values.extend(extraction.business_signals.possible_property_aliases)
    values.extend(extraction.evidence.source_attachments)
    for candidate in property_lookup.address_candidates:
        values.extend(
            [
                candidate.street,
                candidate.city,
                candidate.state,
                candidate.zipcode,
                candidate.normalized_address,
                candidate.source,
                candidate.evidence_text,
            ]
        )
    return [value for value in values if value]


def _optional_upper_string_set(rule: WorkflowRule, key: str) -> set[str]:
    return {value.upper() for value in _optional_string_list_condition(rule, key)}


def _optional_lower_string_set(rule: WorkflowRule, key: str) -> set[str]:
    return {value.lower() for value in _optional_string_list_condition(rule, key)}


def _optional_string_list_condition(rule: WorkflowRule, key: str) -> list[str]:
    value = rule.conditions.get(key, [])
    if not isinstance(value, list):
        raise MissingWorkflowConfigError(f"{rule.rule_code}.{key} must be a list when provided.")
    return [str(item).strip() for item in value if isinstance(item, str) and str(item).strip()]


def _optional_escalate_destination(config: dict[str, Any], current: str | None) -> str | None:
    if current:
        return current
    value = config.get("default_escalate_destination")
    return value if isinstance(value, str) else current


def _match_no_action_email_pattern(patterns: list[NoActionEmailPattern], extraction: ExtractionPayload) -> NoActionEmailPattern | None:
    sender_email = (extraction.email.sender_email or "").strip().lower()
    sender_domain = sender_email.split("@", 1)[1] if "@" in sender_email else ""
    subject = extraction.email.subject or ""
    body = _no_action_body_match_text(extraction)

    for pattern in patterns:
        if pattern.sender_email_equals and sender_email != pattern.sender_email_equals.strip().lower():
            continue
        if pattern.sender_domain_equals and sender_domain != pattern.sender_domain_equals.strip().lower():
            continue
        if pattern.subject_regex:
            try:
                if re.search(pattern.subject_regex, subject, flags=re.IGNORECASE) is None:
                    continue
            except re.error as exc:
                raise MissingWorkflowConfigError(f"Invalid subject_regex for no-action pattern {pattern.pattern_name}: {exc}") from exc
        if pattern.body_regex:
            try:
                if re.search(pattern.body_regex, body, flags=re.IGNORECASE) is None:
                    continue
            except re.error as exc:
                raise MissingWorkflowConfigError(f"Invalid body_regex for no-action pattern {pattern.pattern_name}: {exc}") from exc
        return pattern
    return None


def _matches_current_reply_no_action(rule: WorkflowRule, extraction: ExtractionPayload, decision_context: DecisionContext) -> tuple[bool, str]:
    if decision_context.latest_body_text is None and not decision_context.has_quoted_history:
        return False, "no thread context available"
    if bool(_required_condition(rule, "require_quoted_history")) and decision_context.has_quoted_history is not True:
        return False, "no quoted history present"

    latest = decision_context.latest_body_text
    if not latest or not latest.strip():
        return False, "latest reply text is empty"

    allowed_domains = _optional_lower_string_set(rule, "allowed_sender_domains")
    sender_email = (extraction.email.sender_email or "").strip().lower()
    sender_domain = sender_email.split("@", 1)[1] if "@" in sender_email else ""
    if allowed_domains and sender_domain not in allowed_domains:
        return False, f"sender domain {sender_domain or 'none'} not allowed for current-reply no-action"

    if extraction.evidence.source_attachments:
        return False, "document item cites attachments"
    if not extraction.observed_facts.latest_reply_indicates_no_ap_action:
        return False, "LLM did not classify latest reply as no-action"
    return True, "LLM classified latest reply as no-action and deterministic thread/attachment gates passed"


def _no_action_body_match_text(extraction: ExtractionPayload) -> str:
    thread_context = extraction.raw.get("thread_context")
    if isinstance(thread_context, dict):
        latest = thread_context.get("latest_body_text")
        if isinstance(latest, str) and latest.strip():
            return latest
    return extraction.evidence.summary or ""


def _suffix(file_name: str) -> str:
    _, dot, suffix = file_name.strip().lower().rpartition(".")
    if not dot:
        return ""
    return f".{suffix}"


def _normalize_outcome(outcome: str) -> str:
    return outcome


