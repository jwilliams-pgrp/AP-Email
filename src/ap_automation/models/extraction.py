from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal


DOCUMENT_TYPES = {
    "invoice",
    "statement",
    "account_summary",
    "contract",
    "pay_application",
    "vendor_question",
    "payment_inquiry",
    "past_due_notice",
    "ach_notice",
    "auto_draft_notice",
    "ben_e_keith_notice",
    "lien_release",
    "unknown",
}

DOCUMENT_FLAGS = {
    "multi_invoice_pdf",
    "invoice_plus_lien_waiver",
    "link_only_invoice",
    "missing_invoice_attachment",
    "contract_or_pay_application",
    "vendor_inquiry",
    "past_due",
    "statement_or_account_summary",
    "ach_or_auto_draft",
    "ben_e_keith",
    "lien_release_related",
    "sold_property_candidate",
    "conflicting_signals",
    "low_text_quality",
}


@dataclass(frozen=True)
class ObservedFacts:
    mentions_past_due: bool
    mentions_separate_backup_document: bool
    mentions_merge_or_combine_required: bool
    mentions_lien_waiver_or_release: bool
    mentions_payment_link_only: bool
    mentions_missing_invoice_attachment: bool
    indicates_multiple_invoices: bool
    indicates_statement_or_account_summary: bool
    indicates_contract_or_pay_application: bool
    indicates_vendor_question_or_payment_inquiry: bool
    indicates_ach_or_auto_draft: bool
    indicates_ben_e_keith: bool
    indicates_sold_property: bool
    has_conflicting_signals: bool
    has_low_text_quality: bool


class ExtractionValidationError(ValueError):
    """Raised when an extractor payload violates the local extraction contract."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("Invalid extraction payload: " + "; ".join(errors))
        self.errors = errors


@dataclass(frozen=True)
class ExtractorInfo:
    type: str
    name: str | None
    model: str | None
    prompt_version: str | None


@dataclass(frozen=True)
class EmailMetadata:
    subject: str | None
    sender_email: str | None
    received_at: datetime | None


@dataclass(frozen=True)
class DocumentFacts:
    document_type: str
    document_flags: tuple[str, ...]
    requires_attachment: bool | None
    has_invoice_attachment: bool | None
    link_only: bool
    multi_invoice: bool
    requires_merge: bool


@dataclass(frozen=True)
class InvoiceFacts:
    invoice_number: str | None
    invoice_date: date | None
    due_date: date | None
    amount: float | None
    currency: str | None
    vendor_name: str | None
    vendor_email: str | None
    bill_to: str | None
    property_code: str | None
    property_name: str | None
    service_address: str | None


@dataclass(frozen=True)
class BusinessSignals:
    business_unit_code: str | None
    possible_property_aliases: tuple[str, ...]
    subject_instruction_hint: str | None


@dataclass(frozen=True)
class ConfidenceSignals:
    overall: float
    document_type: float
    invoice_fields: float
    property_identity: float
    business_unit: float


@dataclass(frozen=True)
class Evidence:
    summary: str
    source_attachments: tuple[str, ...]
    source_pages: tuple[int, ...]


@dataclass(frozen=True)
class ExtractionPayload:
    schema_version: Literal["extraction.v1"]
    extractor: ExtractorInfo
    email: EmailMetadata
    document: DocumentFacts
    invoice: InvoiceFacts
    business_signals: BusinessSignals
    observed_facts: ObservedFacts
    confidence: ConfidenceSignals
    evidence: Evidence
    raw: dict[str, Any]

    @property
    def extracted_fields(self) -> dict[str, Any]:
        return {
            "document": {
                "document_type": self.document.document_type,
                "document_flags": list(self.document.document_flags),
                "link_only": self.document.link_only,
                "multi_invoice": self.document.multi_invoice,
                "requires_merge": self.document.requires_merge,
            },
            "invoice": {
                "invoice_number": self.invoice.invoice_number,
                "amount": self.invoice.amount,
                "vendor_name": self.invoice.vendor_name,
                "bill_to": self.invoice.bill_to,
                "property_code": self.invoice.property_code,
                "property_name": self.invoice.property_name,
                "service_address": self.invoice.service_address,
            },
            "business_signals": {
                "business_unit_code": self.business_signals.business_unit_code,
                "possible_property_aliases": list(self.business_signals.possible_property_aliases),
            },
            "observed_facts": {
                "mentions_past_due": self.observed_facts.mentions_past_due,
                "mentions_separate_backup_document": self.observed_facts.mentions_separate_backup_document,
                "mentions_merge_or_combine_required": self.observed_facts.mentions_merge_or_combine_required,
                "mentions_lien_waiver_or_release": self.observed_facts.mentions_lien_waiver_or_release,
                "mentions_payment_link_only": self.observed_facts.mentions_payment_link_only,
                "mentions_missing_invoice_attachment": self.observed_facts.mentions_missing_invoice_attachment,
                "indicates_multiple_invoices": self.observed_facts.indicates_multiple_invoices,
                "indicates_statement_or_account_summary": self.observed_facts.indicates_statement_or_account_summary,
                "indicates_contract_or_pay_application": self.observed_facts.indicates_contract_or_pay_application,
                "indicates_vendor_question_or_payment_inquiry": self.observed_facts.indicates_vendor_question_or_payment_inquiry,
                "indicates_ach_or_auto_draft": self.observed_facts.indicates_ach_or_auto_draft,
                "indicates_ben_e_keith": self.observed_facts.indicates_ben_e_keith,
                "indicates_sold_property": self.observed_facts.indicates_sold_property,
                "has_conflicting_signals": self.observed_facts.has_conflicting_signals,
                "has_low_text_quality": self.observed_facts.has_low_text_quality,
            },
            "confidence": {
                "overall": self.confidence.overall,
                "document_type": self.confidence.document_type,
                "invoice_fields": self.confidence.invoice_fields,
                "property_identity": self.confidence.property_identity,
                "business_unit": self.confidence.business_unit,
            },
        }


def validate_extraction(payload: dict[str, Any]) -> ExtractionPayload:
    errors: list[str] = []

    def obj(path: str) -> dict[str, Any]:
        value = _get(payload, path)
        if not isinstance(value, dict):
            errors.append(f"{path} must be an object")
            return {}
        return value

    schema_version = payload.get("schema_version")
    if schema_version != "extraction.v1":
        errors.append("schema_version must be extraction.v1")

    extractor_raw = obj("extractor")
    email_raw = obj("email")
    document_raw = obj("document")
    invoice_raw = obj("invoice")
    signals_raw = obj("business_signals")
    observed_raw = obj("observed_facts")
    confidence_raw = obj("confidence")
    evidence_raw = obj("evidence")

    extractor_type = _required_str(extractor_raw, "type", "extractor.type", errors)
    document_type = _required_str(document_raw, "document_type", "document.document_type", errors)
    if document_type and document_type not in DOCUMENT_TYPES:
        errors.append(f"document.document_type must be one of {sorted(DOCUMENT_TYPES)}")

    if "document_flags" in document_raw:
        errors.append("document.document_flags is derived by Python and must not be returned by the extractor")
    if "requires_merge" in document_raw:
        errors.append("document.requires_merge is derived by Python and must not be returned by the extractor")

    link_only = _required_bool(document_raw, "link_only", "document.link_only", errors)
    multi_invoice = _required_bool(document_raw, "multi_invoice", "document.multi_invoice", errors)
    observed_facts = ObservedFacts(
        mentions_past_due=_required_bool(observed_raw, "mentions_past_due", "observed_facts.mentions_past_due", errors),
        mentions_separate_backup_document=_required_bool(
            observed_raw,
            "mentions_separate_backup_document",
            "observed_facts.mentions_separate_backup_document",
            errors,
        ),
        mentions_merge_or_combine_required=_required_bool(
            observed_raw,
            "mentions_merge_or_combine_required",
            "observed_facts.mentions_merge_or_combine_required",
            errors,
        ),
        mentions_lien_waiver_or_release=_required_bool(
            observed_raw,
            "mentions_lien_waiver_or_release",
            "observed_facts.mentions_lien_waiver_or_release",
            errors,
        ),
        mentions_payment_link_only=_required_bool(observed_raw, "mentions_payment_link_only", "observed_facts.mentions_payment_link_only", errors),
        mentions_missing_invoice_attachment=_required_bool(
            observed_raw,
            "mentions_missing_invoice_attachment",
            "observed_facts.mentions_missing_invoice_attachment",
            errors,
        ),
        indicates_multiple_invoices=_required_bool(observed_raw, "indicates_multiple_invoices", "observed_facts.indicates_multiple_invoices", errors),
        indicates_statement_or_account_summary=_required_bool(
            observed_raw,
            "indicates_statement_or_account_summary",
            "observed_facts.indicates_statement_or_account_summary",
            errors,
        ),
        indicates_contract_or_pay_application=_required_bool(
            observed_raw,
            "indicates_contract_or_pay_application",
            "observed_facts.indicates_contract_or_pay_application",
            errors,
        ),
        indicates_vendor_question_or_payment_inquiry=_required_bool(
            observed_raw,
            "indicates_vendor_question_or_payment_inquiry",
            "observed_facts.indicates_vendor_question_or_payment_inquiry",
            errors,
        ),
        indicates_ach_or_auto_draft=_required_bool(observed_raw, "indicates_ach_or_auto_draft", "observed_facts.indicates_ach_or_auto_draft", errors),
        indicates_ben_e_keith=_required_bool(observed_raw, "indicates_ben_e_keith", "observed_facts.indicates_ben_e_keith", errors),
        indicates_sold_property=_required_bool(observed_raw, "indicates_sold_property", "observed_facts.indicates_sold_property", errors),
        has_conflicting_signals=_required_bool(observed_raw, "has_conflicting_signals", "observed_facts.has_conflicting_signals", errors),
        has_low_text_quality=_required_bool(observed_raw, "has_low_text_quality", "observed_facts.has_low_text_quality", errors),
    )
    flags = _derive_document_flags(document_type, link_only, multi_invoice, document_raw, observed_facts)
    requires_merge = observed_facts.mentions_merge_or_combine_required

    confidence = ConfidenceSignals(
        overall=_required_confidence(confidence_raw, "overall", "confidence.overall", errors),
        document_type=_required_confidence(confidence_raw, "document_type", "confidence.document_type", errors),
        invoice_fields=_required_confidence(confidence_raw, "invoice_fields", "confidence.invoice_fields", errors),
        property_identity=_required_confidence(confidence_raw, "property_identity", "confidence.property_identity", errors),
        business_unit=_required_confidence(confidence_raw, "business_unit", "confidence.business_unit", errors),
    )

    evidence_summary = _required_str(evidence_raw, "summary", "evidence.summary", errors)

    if errors:
        raise ExtractionValidationError(errors)

    return ExtractionPayload(
        schema_version="extraction.v1",
        extractor=ExtractorInfo(
            type=extractor_type,
            name=_optional_str(extractor_raw.get("name")),
            model=_optional_str(extractor_raw.get("model")),
            prompt_version=_optional_str(extractor_raw.get("prompt_version")),
        ),
        email=EmailMetadata(
            subject=_optional_str(email_raw.get("subject")),
            sender_email=_optional_str(email_raw.get("sender_email")),
            received_at=_optional_datetime(email_raw.get("received_at")),
        ),
        document=DocumentFacts(
            document_type=document_type,
            document_flags=tuple(flags),
            requires_attachment=_optional_bool(document_raw.get("requires_attachment")),
            has_invoice_attachment=_optional_bool(document_raw.get("has_invoice_attachment")),
            link_only=link_only,
            multi_invoice=multi_invoice,
            requires_merge=requires_merge,
        ),
        invoice=InvoiceFacts(
            invoice_number=_optional_str(invoice_raw.get("invoice_number")),
            invoice_date=_optional_date(invoice_raw.get("invoice_date")),
            due_date=_optional_date(invoice_raw.get("due_date")),
            amount=_optional_float(invoice_raw.get("amount")),
            currency=_optional_str(invoice_raw.get("currency")),
            vendor_name=_optional_str(invoice_raw.get("vendor_name")),
            vendor_email=_optional_str(invoice_raw.get("vendor_email")),
            bill_to=_optional_str(invoice_raw.get("bill_to")),
            property_code=_optional_str(invoice_raw.get("property_code")),
            property_name=_optional_str(invoice_raw.get("property_name")),
            service_address=_optional_str(invoice_raw.get("service_address")),
        ),
        business_signals=BusinessSignals(
            business_unit_code=_optional_str(signals_raw.get("business_unit_code")),
            possible_property_aliases=tuple(_optional_string_list(signals_raw.get("possible_property_aliases"))),
            subject_instruction_hint=_optional_str(signals_raw.get("subject_instruction_hint")),
        ),
        observed_facts=observed_facts,
        confidence=confidence,
        evidence=Evidence(
            summary=evidence_summary,
            source_attachments=tuple(_optional_string_list(evidence_raw.get("source_attachments"))),
            source_pages=tuple(_optional_int_list(evidence_raw.get("source_pages"))),
        ),
        raw=payload,
    )


def _derive_document_flags(
    document_type: str,
    link_only: bool,
    multi_invoice: bool,
    document_raw: dict[str, Any],
    observed: ObservedFacts,
) -> tuple[str, ...]:
    flags: list[str] = []
    requires_attachment = _optional_bool(document_raw.get("requires_attachment"))
    has_invoice_attachment = _optional_bool(document_raw.get("has_invoice_attachment"))

    def add(flag: str, condition: bool) -> None:
        if condition and flag not in flags:
            flags.append(flag)

    add("multi_invoice_pdf", multi_invoice or observed.indicates_multiple_invoices)
    add("link_only_invoice", link_only or observed.mentions_payment_link_only)
    add("missing_invoice_attachment", observed.mentions_missing_invoice_attachment or (requires_attachment is True and has_invoice_attachment is False))
    add("contract_or_pay_application", observed.indicates_contract_or_pay_application or document_type in {"contract", "pay_application"})
    add("vendor_inquiry", observed.indicates_vendor_question_or_payment_inquiry or document_type in {"vendor_question", "payment_inquiry"})
    add("past_due", observed.mentions_past_due or document_type == "past_due_notice")
    add("statement_or_account_summary", observed.indicates_statement_or_account_summary or document_type in {"statement", "account_summary"})
    add("ach_or_auto_draft", observed.indicates_ach_or_auto_draft or document_type in {"ach_notice", "auto_draft_notice"})
    add("ben_e_keith", observed.indicates_ben_e_keith or document_type == "ben_e_keith_notice")
    add("lien_release_related", observed.mentions_lien_waiver_or_release or document_type == "lien_release")
    add("invoice_plus_lien_waiver", document_type == "invoice" and observed.mentions_lien_waiver_or_release)
    add("sold_property_candidate", observed.indicates_sold_property)
    add("conflicting_signals", observed.has_conflicting_signals)
    add("low_text_quality", observed.has_low_text_quality)
    return tuple(flags)


def automatic_route_missing_fields(extraction: ExtractionPayload) -> list[str]:
    if extraction.document.document_type != "invoice":
        return []

    missing: list[str] = []
    if not extraction.invoice.vendor_name:
        missing.append("invoice.vendor_name")
    if extraction.invoice.amount is None:
        missing.append("invoice.amount")
    has_property_signal = any(
        [
            extraction.invoice.bill_to,
            extraction.invoice.property_code,
            extraction.invoice.property_name,
            extraction.invoice.service_address,
            extraction.business_signals.business_unit_code,
            extraction.business_signals.possible_property_aliases,
        ]
    )
    if not has_property_signal:
        missing.append("property_or_business_unit_signal")
    return missing


def _get(payload: dict[str, Any], path: str) -> Any:
    value: Any = payload
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _required_str(payload: dict[str, Any], key: str, path: str, errors: list[str]) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{path} must be a non-empty string")
        return ""
    return value


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    raise ExtractionValidationError([f"expected string or null, got {type(value).__name__}"])


def _required_bool(payload: dict[str, Any], key: str, path: str, errors: list[str]) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        errors.append(f"{path} must be a boolean")
        return False
    return value


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    raise ExtractionValidationError([f"expected boolean or null, got {type(value).__name__}"])


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


def _required_string_list(payload: dict[str, Any], key: str, path: str, errors: list[str]) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        errors.append(f"{path} must be a list of strings")
        return []
    return value


def _optional_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    raise ExtractionValidationError([f"expected list of strings or null, got {type(value).__name__}"])


def _optional_int_list(value: Any) -> list[int]:
    if value is None:
        return []
    if isinstance(value, list) and all(isinstance(item, int) and not isinstance(item, bool) for item in value):
        return value
    raise ExtractionValidationError([f"expected list of integers or null, got {type(value).__name__}"])


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    raise ExtractionValidationError([f"expected number or null, got {type(value).__name__}"])


def _optional_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise ExtractionValidationError([f"expected ISO date string or null, got {type(value).__name__}"])


def _optional_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    raise ExtractionValidationError([f"expected ISO datetime string or null, got {type(value).__name__}"])
