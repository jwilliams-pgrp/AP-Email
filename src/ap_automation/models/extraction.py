from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any, Iterable, Literal


DOCUMENT_TYPES = {
    "invoice",
    "check_request",
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
    "separate_lien_waiver",
    "link_only_invoice",
    "missing_invoice_attachment",
    "contract_or_pay_application",
    "vendor_inquiry",
    "wrong_destination",
    "past_due",
    "statement_or_account_summary",
    "ach_or_auto_draft",
    "ben_e_keith",
    "lien_release_related",
    "conflicting_signals",
    "low_text_quality",
}

ADDRESS_CANDIDATE_LABELS = {
    "deliver_to",
    "ship_to",
    "service_location",
    "site",
    "property",
    "bill_to",
    "customer_account",
}

EXCLUDED_ATTACHMENT_REASON_CODES = {"irrelevant_to_ap_workflow", "payment_instruction_support"}
EXCLUDED_ATTACHMENT_SOURCES = {"document_intelligence", "pymupdf", "filename", "email_context"}


@dataclass(frozen=True)
class ObservedFacts:
    current_invoice_is_past_due: bool
    account_has_past_due_aging_balance: bool
    contains_aging_summary: bool
    mentions_separate_backup_document: bool
    mentions_merge_or_combine_required: bool
    mentions_lien_waiver_or_release: bool
    mentions_payment_link_only: bool
    mentions_missing_invoice_attachment: bool
    indicates_multiple_invoices: bool
    indicates_statement_or_account_summary: bool
    indicates_contract_or_pay_application: bool
    indicates_vendor_question_or_payment_inquiry: bool
    indicates_wrong_destination: bool
    latest_reply_indicates_no_ap_action: bool
    indicates_informational_appointment_notice: bool
    indicates_ach_or_auto_draft: bool
    indicates_ben_e_keith: bool
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
    project_number: str | None
    job_number: str | None
    invoice_date: date | None
    due_date: date | None
    amount: float | None
    currency: str | None
    vendor_name: str | None
    vendor_email: str | None
    bill_to: str | None
    bill_to_name_line_1: str | None
    bill_to_name_line_2: str | None
    bill_to_street_address: str | None
    bill_to_suite: str | None
    bill_to_city: str | None
    bill_to_state: str | None
    bill_to_zip_code: str | None
    property_code: str | None
    property_name: str | None
    service_address: str | None


@dataclass(frozen=True)
class AddressCandidate:
    rank: int
    label: str
    street: str | None
    city: str | None
    state: str | None
    zipcode: str | None
    normalized_address: str | None
    source: str | None
    confidence: float | None
    evidence_text: str | None


@dataclass(frozen=True)
class PropertyLookupFacts:
    property_code: tuple[str, ...]
    property_name: tuple[str, ...]
    tenant: tuple[str, ...]
    address: tuple[str, ...]
    suite: tuple[str, ...]
    city: tuple[str, ...]
    state: tuple[str, ...]
    zipcode: tuple[str, ...]
    address_candidates: tuple[AddressCandidate, ...]


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
class EvidenceSourceRef:
    attachment: str
    page: int | None


@dataclass(frozen=True)
class Evidence:
    summary: str
    source_attachments: tuple[str, ...]
    source_pages: tuple[int, ...]
    source_refs: tuple[EvidenceSourceRef, ...]


@dataclass(frozen=True)
class ExtractionPayload:
    schema_version: Literal["extraction.v1"]
    extractor: ExtractorInfo
    email: EmailMetadata
    document: DocumentFacts
    invoice: InvoiceFacts
    property_lookup: PropertyLookupFacts
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
                "project_number": self.invoice.project_number,
                "job_number": self.invoice.job_number,
                "amount": self.invoice.amount,
                "vendor_name": self.invoice.vendor_name,
                "bill_to": self.invoice.bill_to,
                "bill_to_name_line_1": self.invoice.bill_to_name_line_1,
                "bill_to_name_line_2": self.invoice.bill_to_name_line_2,
                "bill_to_street_address": self.invoice.bill_to_street_address,
                "bill_to_suite": self.invoice.bill_to_suite,
                "bill_to_city": self.invoice.bill_to_city,
                "bill_to_state": self.invoice.bill_to_state,
                "bill_to_zip_code": self.invoice.bill_to_zip_code,
                "property_code": self.invoice.property_code,
                "property_name": self.invoice.property_name,
                "service_address": self.invoice.service_address,
            },
            "business_signals": {
                "business_unit_code": self.business_signals.business_unit_code,
                "possible_property_aliases": list(self.business_signals.possible_property_aliases),
            },
            "property_lookup": {
                "property_code": list(self.property_lookup.property_code),
                "property_name": list(self.property_lookup.property_name),
                "tenant": list(self.property_lookup.tenant),
                "address": list(self.property_lookup.address),
                "suite": list(self.property_lookup.suite),
                "city": list(self.property_lookup.city),
                "state": list(self.property_lookup.state),
                "zipcode": list(self.property_lookup.zipcode),
                "address_candidates": [
                    {
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
                    for candidate in self.property_lookup.address_candidates
                ],
            },
            "observed_facts": {
                "current_invoice_is_past_due": self.observed_facts.current_invoice_is_past_due,
                "account_has_past_due_aging_balance": self.observed_facts.account_has_past_due_aging_balance,
                "contains_aging_summary": self.observed_facts.contains_aging_summary,
                "mentions_separate_backup_document": self.observed_facts.mentions_separate_backup_document,
                "mentions_merge_or_combine_required": self.observed_facts.mentions_merge_or_combine_required,
                "mentions_lien_waiver_or_release": self.observed_facts.mentions_lien_waiver_or_release,
                "mentions_payment_link_only": self.observed_facts.mentions_payment_link_only,
                "mentions_missing_invoice_attachment": self.observed_facts.mentions_missing_invoice_attachment,
                "indicates_multiple_invoices": self.observed_facts.indicates_multiple_invoices,
                "indicates_statement_or_account_summary": self.observed_facts.indicates_statement_or_account_summary,
                "indicates_contract_or_pay_application": self.observed_facts.indicates_contract_or_pay_application,
                "indicates_vendor_question_or_payment_inquiry": self.observed_facts.indicates_vendor_question_or_payment_inquiry,
                "indicates_wrong_destination": self.observed_facts.indicates_wrong_destination,
                "latest_reply_indicates_no_ap_action": self.observed_facts.latest_reply_indicates_no_ap_action,
                "indicates_informational_appointment_notice": self.observed_facts.indicates_informational_appointment_notice,
                "indicates_ach_or_auto_draft": self.observed_facts.indicates_ach_or_auto_draft,
                "indicates_ben_e_keith": self.observed_facts.indicates_ben_e_keith,
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


@dataclass(frozen=True)
class DocumentItem:
    item_kind: Literal["attachment", "email"]
    item_key: str
    display_name: str | None
    attachment_id: str | None
    metadata: dict[str, Any]
    extraction: ExtractionPayload
    raw_item: dict[str, Any]


@dataclass(frozen=True)
class ExcludedAttachment:
    file_name: str
    reason_code: Literal["irrelevant_to_ap_workflow", "payment_instruction_support"]
    reason: str
    source: str | None


@dataclass(frozen=True)
class ExtractionBatch:
    schema_version: Literal["extraction_batch.v1"]
    items: tuple[DocumentItem, ...]
    excluded_attachments: tuple[ExcludedAttachment, ...]
    raw: dict[str, Any]


def validate_extraction(payload: dict[str, Any]) -> ExtractionPayload:
    errors: list[str] = []

    def obj(path: str) -> dict[str, Any]:
        value = _get(payload, path)
        if not isinstance(value, dict):
            errors.append(_type_error(path, "object", value))
            return {}
        return value

    schema_version = payload.get("schema_version")
    if schema_version != "extraction.v1":
        errors.append("schema_version must be extraction.v1")

    extractor_raw = obj("extractor")
    email_raw = obj("email")
    document_raw = obj("document")
    invoice_raw = obj("invoice")
    property_lookup_raw = payload.get("property_lookup")
    if property_lookup_raw is not None and not isinstance(property_lookup_raw, dict):
        errors.append(_type_error("property_lookup", "object or null", property_lookup_raw))
        property_lookup_raw = {}
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
        current_invoice_is_past_due=_required_bool(
            observed_raw,
            "current_invoice_is_past_due",
            "observed_facts.current_invoice_is_past_due",
            errors,
        ),
        account_has_past_due_aging_balance=_required_bool(
            observed_raw,
            "account_has_past_due_aging_balance",
            "observed_facts.account_has_past_due_aging_balance",
            errors,
        ),
        contains_aging_summary=_required_bool(
            observed_raw,
            "contains_aging_summary",
            "observed_facts.contains_aging_summary",
            errors,
        ),
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
        indicates_wrong_destination=_required_bool(
            observed_raw,
            "indicates_wrong_destination",
            "observed_facts.indicates_wrong_destination",
            errors,
        ),
        latest_reply_indicates_no_ap_action=_required_bool(
            observed_raw,
            "latest_reply_indicates_no_ap_action",
            "observed_facts.latest_reply_indicates_no_ap_action",
            errors,
        ),
        indicates_informational_appointment_notice=_required_bool(
            observed_raw,
            "indicates_informational_appointment_notice",
            "observed_facts.indicates_informational_appointment_notice",
            errors,
        ),
        indicates_ach_or_auto_draft=_required_bool(observed_raw, "indicates_ach_or_auto_draft", "observed_facts.indicates_ach_or_auto_draft", errors),
        indicates_ben_e_keith=_required_bool(observed_raw, "indicates_ben_e_keith", "observed_facts.indicates_ben_e_keith", errors),
        has_conflicting_signals=_required_bool(observed_raw, "has_conflicting_signals", "observed_facts.has_conflicting_signals", errors),
        has_low_text_quality=_required_bool(observed_raw, "has_low_text_quality", "observed_facts.has_low_text_quality", errors),
    )
    flags = _derive_document_flags(document_type, link_only, multi_invoice, email_raw, document_raw, invoice_raw, observed_facts, evidence_raw)
    requires_merge = observed_facts.mentions_merge_or_combine_required

    confidence = ConfidenceSignals(
        overall=_required_confidence(confidence_raw, "overall", "confidence.overall", errors),
        document_type=_required_confidence(confidence_raw, "document_type", "confidence.document_type", errors),
        invoice_fields=_required_confidence(confidence_raw, "invoice_fields", "confidence.invoice_fields", errors),
        property_identity=_required_confidence(confidence_raw, "property_identity", "confidence.property_identity", errors),
        business_unit=_required_confidence(confidence_raw, "business_unit", "confidence.business_unit", errors),
    )

    evidence_summary = _required_str(evidence_raw, "summary", "evidence.summary", errors)
    source_pages, legacy_source_refs = _source_pages_and_legacy_refs(evidence_raw.get("source_pages"), errors)
    source_refs = _source_refs(evidence_raw.get("source_refs"), errors)

    address_candidates = _address_candidates((property_lookup_raw or {}).get("address_candidates"), errors)
    property_lookup = _property_lookup_facts(property_lookup_raw or {}, invoice_raw, address_candidates, errors)

    if errors:
        raise ExtractionValidationError(errors)

    return ExtractionPayload(
        schema_version="extraction.v1",
        extractor=ExtractorInfo(
            type=extractor_type,
            name=_optional_str(extractor_raw.get("name"), "extractor.name"),
            model=_optional_str(extractor_raw.get("model"), "extractor.model"),
            prompt_version=_optional_str(extractor_raw.get("prompt_version"), "extractor.prompt_version"),
        ),
        email=EmailMetadata(
            subject=_optional_str(email_raw.get("subject"), "email.subject"),
            sender_email=_optional_str(email_raw.get("sender_email"), "email.sender_email"),
            received_at=_optional_datetime(email_raw.get("received_at"), "email.received_at"),
        ),
        document=DocumentFacts(
            document_type=document_type,
            document_flags=tuple(flags),
            requires_attachment=_optional_bool(document_raw.get("requires_attachment"), "document.requires_attachment"),
            has_invoice_attachment=_optional_bool(document_raw.get("has_invoice_attachment"), "document.has_invoice_attachment"),
            link_only=link_only,
            multi_invoice=multi_invoice,
            requires_merge=requires_merge,
        ),
        invoice=InvoiceFacts(
            invoice_number=_optional_str(invoice_raw.get("invoice_number"), "invoice.invoice_number"),
            project_number=_optional_str(invoice_raw.get("project_number"), "invoice.project_number"),
            job_number=_optional_str(invoice_raw.get("job_number"), "invoice.job_number"),
            invoice_date=_optional_date(invoice_raw.get("invoice_date"), "invoice.invoice_date"),
            due_date=_optional_date(invoice_raw.get("due_date"), "invoice.due_date"),
            amount=_optional_float(invoice_raw.get("amount"), "invoice.amount"),
            currency=_optional_str(invoice_raw.get("currency"), "invoice.currency"),
            vendor_name=_optional_str(invoice_raw.get("vendor_name"), "invoice.vendor_name"),
            vendor_email=_optional_str(invoice_raw.get("vendor_email"), "invoice.vendor_email"),
            bill_to=_optional_str(invoice_raw.get("bill_to"), "invoice.bill_to"),
            bill_to_name_line_1=_optional_str(invoice_raw.get("bill_to_name_line_1"), "invoice.bill_to_name_line_1"),
            bill_to_name_line_2=_optional_str(invoice_raw.get("bill_to_name_line_2"), "invoice.bill_to_name_line_2"),
            bill_to_street_address=_optional_str(invoice_raw.get("bill_to_street_address"), "invoice.bill_to_street_address"),
            bill_to_suite=_optional_str(invoice_raw.get("bill_to_suite"), "invoice.bill_to_suite"),
            bill_to_city=_optional_str(invoice_raw.get("bill_to_city"), "invoice.bill_to_city"),
            bill_to_state=_optional_str(invoice_raw.get("bill_to_state"), "invoice.bill_to_state"),
            bill_to_zip_code=_optional_str(invoice_raw.get("bill_to_zip_code"), "invoice.bill_to_zip_code"),
            property_code=_optional_str(invoice_raw.get("property_code"), "invoice.property_code"),
            property_name=_optional_str(invoice_raw.get("property_name"), "invoice.property_name"),
            service_address=_optional_str(invoice_raw.get("service_address"), "invoice.service_address"),
        ),
        property_lookup=property_lookup,
        business_signals=BusinessSignals(
            business_unit_code=_optional_str(signals_raw.get("business_unit_code"), "business_signals.business_unit_code"),
            possible_property_aliases=tuple(_optional_string_list(signals_raw.get("possible_property_aliases"), "business_signals.possible_property_aliases")),
            subject_instruction_hint=_optional_str(signals_raw.get("subject_instruction_hint"), "business_signals.subject_instruction_hint"),
        ),
        observed_facts=observed_facts,
        confidence=confidence,
        evidence=Evidence(
            summary=evidence_summary,
            source_attachments=tuple(_optional_string_list(evidence_raw.get("source_attachments"), "evidence.source_attachments")),
            source_pages=tuple(source_pages),
            source_refs=tuple(_dedupe_source_refs([*source_refs, *legacy_source_refs])),
        ),
        raw=payload,
    )


def validate_extraction_batch(payload: dict[str, Any]) -> ExtractionBatch:
    if payload.get("schema_version") != "extraction_batch.v1":
        extraction = validate_extraction(payload)
        item_key = _default_item_key(extraction)
        items = _normalize_separate_supporting_document_flags(
            [
                DocumentItem(
                    item_kind="email",
                    item_key=item_key,
                    display_name="Email",
                    attachment_id=None,
                    metadata={"compatibility_wrapped": True},
                    extraction=extraction,
                    raw_item={"item_kind": "email", "item_key": item_key, "extraction": payload},
                )
            ]
        )
        return ExtractionBatch(
            schema_version="extraction_batch.v1",
            items=tuple(items),
            excluded_attachments=(),
            raw={
                "schema_version": "extraction_batch.v1",
                "items": [{"item_kind": "email", "item_key": item_key, "extraction": payload}],
            },
        )

    errors: list[str] = []
    raw_items = payload.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise ExtractionValidationError(["extraction_batch.v1 items must be a non-empty list"])

    items: list[DocumentItem] = []
    seen_keys: set[str] = set()
    for index, raw_item in enumerate(raw_items):
        path = f"items[{index}]"
        if not isinstance(raw_item, dict):
            errors.append(f"{path} must be an object")
            continue
        item_kind = raw_item.get("item_kind")
        if item_kind not in {"attachment", "email"}:
            errors.append(f"{path}.item_kind must be attachment or email")
        item_key = raw_item.get("item_key")
        if not isinstance(item_key, str) or not item_key.strip():
            errors.append(f"{path}.item_key must be a non-empty string")
            item_key = f"invalid:{index}"
        else:
            item_key = item_key.strip()
            if item_key in seen_keys:
                errors.append(f"{path}.item_key must be unique within the batch")
            seen_keys.add(item_key)
        display_name = _optional_str_for_errors(raw_item.get("display_name"), f"{path}.display_name", errors)
        attachment_id = _optional_str_for_errors(raw_item.get("attachment_id"), f"{path}.attachment_id", errors)
        metadata = raw_item.get("metadata") or {}
        if not isinstance(metadata, dict):
            errors.append(f"{path}.metadata must be an object when present")
            metadata = {}
        extraction_payload = raw_item.get("extraction")
        if not isinstance(extraction_payload, dict):
            errors.append(f"{path}.extraction must be an extraction.v1 object")
            continue
        try:
            extraction = validate_extraction(extraction_payload)
        except ExtractionValidationError as exc:
            errors.extend(f"{path}.extraction.{error}" for error in exc.errors)
            continue
        items.append(
            DocumentItem(
                item_kind=item_kind if item_kind in {"attachment", "email"} else "email",
                item_key=item_key,
                display_name=display_name,
                attachment_id=attachment_id,
                metadata=metadata,
                extraction=extraction,
                raw_item=raw_item,
            )
        )

    excluded_attachments = _excluded_attachments(payload.get("excluded_attachments"), errors)

    if errors:
        raise ExtractionValidationError(errors)
    items = _normalize_separate_supporting_document_flags(items)
    return ExtractionBatch(
        schema_version="extraction_batch.v1",
        items=tuple(items),
        excluded_attachments=tuple(excluded_attachments),
        raw=payload,
    )


def _normalize_separate_supporting_document_flags(items: list[DocumentItem]) -> list[DocumentItem]:
    supporting_items = [item for item in items if _is_ap_relevant_supporting_document(item)]
    if not supporting_items:
        return [_set_separate_backup_signal(item, False) for item in items]
    return [
        _set_separate_backup_signal(
            item,
            item.extraction.document.document_type == "invoice"
            and any(_supporting_document_matches_invoice(item, supporting_item) for supporting_item in supporting_items),
        )
        for item in items
    ]


_SUPPORTING_DOCUMENT_KEYWORDS = (
    "lien waiver",
    "lien release",
    "work order",
    "field ticket",
    "service ticket",
    "delivery ticket",
    "signed ticket",
    "time entry",
    "time-entry",
    "hourly detail",
    "shift report",
    "actual hours worked",
    "hours worked",
    "staffing hours",
    "timesheet",
    "time sheet",
    "labor detail",
    "labor/material",
    "material breakdown",
    "job completion",
    "backup",
    "supporting document",
)


def _is_ap_relevant_supporting_document(item: DocumentItem) -> bool:
    extraction = item.extraction
    if extraction.document.document_type == "invoice":
        return False
    if extraction.document.document_type == "lien_release" or extraction.observed_facts.mentions_lien_waiver_or_release:
        return True
    text = " ".join(
        value
        for value in (
            item.display_name,
            extraction.evidence.summary,
            extraction.invoice.invoice_number,
            extraction.invoice.vendor_name,
            extraction.invoice.property_name,
            extraction.invoice.service_address,
        )
        if value
    ).lower()
    return any(keyword in text for keyword in _SUPPORTING_DOCUMENT_KEYWORDS)


def _supporting_document_matches_invoice(invoice_item: DocumentItem, supporting_item: DocumentItem) -> bool:
    invoice = invoice_item.extraction
    supporting = supporting_item.extraction
    if _shared_non_empty(_identity_values(invoice.invoice.vendor_name), _identity_values(supporting.invoice.vendor_name)):
        return True
    if _shared_non_empty(_identity_values(invoice.invoice.invoice_number), _identity_values(supporting.invoice.invoice_number)):
        return True
    invoice_property_values = _property_identity_values(invoice)
    supporting_property_values = _property_identity_values(supporting)
    if _shared_non_empty(invoice_property_values, supporting_property_values):
        return True
    invoice_attachments = set(invoice.evidence.source_attachments) | {ref.attachment for ref in invoice.evidence.source_refs}
    supporting_attachments = set(supporting.evidence.source_attachments) | {ref.attachment for ref in supporting.evidence.source_refs}
    return bool(invoice_attachments and supporting_attachments and invoice_attachments.isdisjoint(supporting_attachments))


def _property_identity_values(extraction: ExtractionPayload) -> set[str]:
    values: list[str | None] = [
        extraction.invoice.property_code,
        extraction.invoice.property_name,
        extraction.invoice.service_address,
        *extraction.property_lookup.property_code,
        *extraction.property_lookup.property_name,
        *extraction.property_lookup.tenant,
        *extraction.property_lookup.address,
    ]
    return _identity_values(*values)


def _identity_values(*values: str | None) -> set[str]:
    return {
        normalized
        for value in values
        if value
        for normalized in [_normalize_lookup_text(value)]
        if normalized
    }


def _shared_non_empty(first: set[str], second: set[str]) -> bool:
    return bool(first and second and first.intersection(second))


def _set_separate_backup_signal(item: DocumentItem, enabled: bool) -> DocumentItem:
    extraction = item.extraction
    flags = [flag for flag in extraction.document.document_flags if flag != "separate_lien_waiver"]
    if enabled:
        flags.append("separate_lien_waiver")
    observed = replace(extraction.observed_facts, mentions_separate_backup_document=enabled)
    document = replace(extraction.document, document_flags=tuple(flags))
    raw = _raw_with_separate_backup_signal(extraction.raw, enabled)
    return replace(item, extraction=replace(extraction, observed_facts=observed, document=document, raw=raw))


def _raw_with_separate_backup_signal(raw: dict[str, Any], enabled: bool) -> dict[str, Any]:
    updated = dict(raw)
    observed = dict(updated.get("observed_facts") or {})
    observed["mentions_separate_backup_document"] = enabled
    updated["observed_facts"] = observed
    document = dict(updated.get("document") or {})
    document.pop("document_flags", None)
    updated["document"] = document
    return updated


def _excluded_attachments(value: Any, errors: list[str]) -> list[ExcludedAttachment]:
    if value is None:
        return []
    if not isinstance(value, list):
        errors.append("excluded_attachments must be a list when present")
        return []
    excluded: list[ExcludedAttachment] = []
    seen_names: set[str] = set()
    for index, raw_attachment in enumerate(value):
        path = f"excluded_attachments[{index}]"
        if not isinstance(raw_attachment, dict):
            errors.append(f"{path} must be an object")
            continue
        file_name = _optional_str_for_errors(raw_attachment.get("file_name"), f"{path}.file_name", errors)
        if file_name is None:
            errors.append(f"{path}.file_name must be a non-empty string")
            file_name = ""
        elif file_name in seen_names:
            errors.append(f"{path}.file_name must be unique within excluded_attachments")
        seen_names.add(file_name)
        reason_code = _optional_str_for_errors(raw_attachment.get("reason_code"), f"{path}.reason_code", errors)
        if reason_code not in EXCLUDED_ATTACHMENT_REASON_CODES:
            errors.append(f"{path}.reason_code must be one of {sorted(EXCLUDED_ATTACHMENT_REASON_CODES)}")
            reason_code = "irrelevant_to_ap_workflow"
        reason = _optional_str_for_errors(raw_attachment.get("reason"), f"{path}.reason", errors)
        if reason is None:
            errors.append(f"{path}.reason must be a non-empty string")
            reason = ""
        source = _optional_str_for_errors(raw_attachment.get("source"), f"{path}.source", errors)
        if source is not None and source not in EXCLUDED_ATTACHMENT_SOURCES:
            errors.append(f"{path}.source must be one of {sorted(EXCLUDED_ATTACHMENT_SOURCES)}")
            source = None
        excluded.append(
            ExcludedAttachment(
                file_name=file_name,
                reason_code=reason_code,  # type: ignore[arg-type]
                reason=reason,
                source=source,
            )
        )
    return excluded


def _default_item_key(extraction: ExtractionPayload) -> str:
    source_attachments = extraction.evidence.source_attachments
    if len(source_attachments) == 1:
        return f"attachment:{source_attachments[0]}"
    return "email:body"


def _property_lookup_facts(
    property_lookup_raw: dict[str, Any],
    invoice_raw: dict[str, Any],
    address_candidates: list[AddressCandidate],
    errors: list[str],
) -> PropertyLookupFacts:
    address_candidates = [*address_candidates]
    addresses = _optional_lookup_string_list(property_lookup_raw.get("address"), "property_lookup.address")
    _append_bill_to_address_candidate(invoice_raw, addresses, address_candidates)
    cities = _optional_lookup_string_list(property_lookup_raw.get("city"), "property_lookup.city")
    states = _optional_lookup_string_list(property_lookup_raw.get("state"), "property_lookup.state")
    zipcodes = _optional_lookup_string_list(property_lookup_raw.get("zipcode"), "property_lookup.zipcode")
    for candidate in sorted(address_candidates, key=lambda item: item.rank):
        addresses.extend(value for value in (candidate.street, candidate.normalized_address) if value)
        if candidate.city:
            cities.append(candidate.city)
        if candidate.state:
            states.append(candidate.state)
        if candidate.zipcode:
            zipcodes.append(candidate.zipcode)
    return PropertyLookupFacts(
        property_code=tuple(_optional_lookup_string_list(property_lookup_raw.get("property_code"), "property_lookup.property_code")),
        property_name=tuple(_optional_lookup_string_list(property_lookup_raw.get("property_name"), "property_lookup.property_name")),
        tenant=tuple(_optional_lookup_string_list(property_lookup_raw.get("tenant"), "property_lookup.tenant")),
        address=tuple(_dedupe_preserving_order(addresses)),
        suite=tuple(_optional_lookup_string_list(property_lookup_raw.get("suite"), "property_lookup.suite")),
        city=tuple(_dedupe_preserving_order(cities)),
        state=tuple(_dedupe_preserving_order(states)),
        zipcode=tuple(_dedupe_preserving_order(zipcodes)),
        address_candidates=tuple(sorted(address_candidates, key=lambda item: item.rank)),
    )


def _append_bill_to_address_candidate(invoice_raw: dict[str, Any], existing_addresses: list[str], address_candidates: list[AddressCandidate]) -> None:
    street = _normalize_lookup_text(_optional_str(invoice_raw.get("bill_to_street_address"), "invoice.bill_to_street_address"))
    city = _normalize_lookup_text(_optional_str(invoice_raw.get("bill_to_city"), "invoice.bill_to_city"))
    state = _normalize_state(_optional_str(invoice_raw.get("bill_to_state"), "invoice.bill_to_state"))
    zipcode = _normalize_zipcode(_optional_str(invoice_raw.get("bill_to_zip_code"), "invoice.bill_to_zip_code"))
    suite = _normalize_lookup_text(_optional_str(invoice_raw.get("bill_to_suite"), "invoice.bill_to_suite"))
    if not any([street, city, state, zipcode]):
        return
    if street and any(street in address or address in street for address in existing_addresses):
        return
    if any(candidate.label in {"bill_to", "customer_account"} and candidate.street == street for candidate in address_candidates):
        return
    normalized_parts = [street, city, state, zipcode]
    address_candidates.append(
        AddressCandidate(
            rank=(max((candidate.rank for candidate in address_candidates), default=0) + 1),
            label="bill_to",
            street=street,
            city=city,
            state=state,
            zipcode=zipcode,
            normalized_address=" ".join(part for part in normalized_parts if part) or None,
            source="invoice.bill_to",
            confidence=None,
            evidence_text=", ".join(
                part
                for part in (
                    _optional_str(invoice_raw.get("bill_to_name_line_1"), "invoice.bill_to_name_line_1"),
                    _optional_str(invoice_raw.get("bill_to_name_line_2"), "invoice.bill_to_name_line_2"),
                    _optional_str(invoice_raw.get("bill_to_street_address"), "invoice.bill_to_street_address"),
                    _optional_str(invoice_raw.get("bill_to_suite"), "invoice.bill_to_suite") or (f"Suite {suite}" if suite else None),
                    _optional_str(invoice_raw.get("bill_to_city"), "invoice.bill_to_city"),
                    _optional_str(invoice_raw.get("bill_to_state"), "invoice.bill_to_state"),
                    _optional_str(invoice_raw.get("bill_to_zip_code"), "invoice.bill_to_zip_code"),
                )
                if part
            )
            or _optional_str(invoice_raw.get("bill_to"), "invoice.bill_to"),
        )
    )


def _normalize_lookup_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.lower().strip()
    replacements = {
        "pkwy": "parkway",
        "pwky": "parkway",
        "fwy": "freeway",
        "ste": "",
        "suite": "",
        "unit": "",
        "#": "",
    }
    for source, replacement in replacements.items():
        normalized = re.sub(rf"\b{re.escape(source)}\b", replacement, normalized)
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized or None


def _normalize_state(value: str | None) -> str | None:
    normalized = _normalize_lookup_text(value)
    if normalized == "texas":
        return "tx"
    return normalized


def _normalize_zipcode(value: str | None) -> str | None:
    if value is None:
        return None
    digits = re.sub(r"[^0-9]+", "", value)
    return digits[:5] or None


def _address_candidates(value: Any, errors: list[str]) -> list[AddressCandidate]:
    if value is None:
        return []
    if not isinstance(value, list):
        errors.append("property_lookup.address_candidates must be a list when present")
        return []
    candidates: list[AddressCandidate] = []
    for index, raw_candidate in enumerate(value):
        path = f"property_lookup.address_candidates[{index}]"
        if not isinstance(raw_candidate, dict):
            errors.append(f"{path} must be an object")
            continue
        rank_value = raw_candidate.get("rank")
        if not isinstance(rank_value, int) or isinstance(rank_value, bool) or rank_value < 1:
            errors.append(f"{path}.rank must be a positive integer")
            rank_value = index + 1
        label = _optional_str_for_errors(raw_candidate.get("label"), f"{path}.label", errors)
        if label is None:
            errors.append(f"{path}.label must be one of {sorted(ADDRESS_CANDIDATE_LABELS)}")
            label = ""
        elif label not in ADDRESS_CANDIDATE_LABELS:
            errors.append(f"{path}.label must be one of {sorted(ADDRESS_CANDIDATE_LABELS)}")
        confidence = raw_candidate.get("confidence")
        confidence_value = None
        if confidence is not None:
            if not isinstance(confidence, int | float) or isinstance(confidence, bool):
                errors.append(f"{path}.confidence must be a number between 0.0 and 1.0")
            else:
                confidence_value = float(confidence)
                if confidence_value < 0.0 or confidence_value > 1.0:
                    errors.append(f"{path}.confidence must be between 0.0 and 1.0")
                    confidence_value = None
        candidate = AddressCandidate(
            rank=rank_value,
            label=label,
            street=_optional_str_for_errors(raw_candidate.get("street"), f"{path}.street", errors),
            city=_optional_str_for_errors(raw_candidate.get("city"), f"{path}.city", errors),
            state=_optional_str_for_errors(raw_candidate.get("state"), f"{path}.state", errors),
            zipcode=_optional_str_for_errors(raw_candidate.get("zipcode"), f"{path}.zipcode", errors),
            normalized_address=_optional_str_for_errors(raw_candidate.get("normalized_address"), f"{path}.normalized_address", errors),
            source=_optional_str_for_errors(raw_candidate.get("source"), f"{path}.source", errors),
            confidence=confidence_value,
            evidence_text=_optional_str_for_errors(raw_candidate.get("evidence_text"), f"{path}.evidence_text", errors),
        )
        if not any([candidate.street, candidate.normalized_address, candidate.city, candidate.state, candidate.zipcode]):
            errors.append(f"{path} must include at least one address component")
        candidates.append(candidate)
    return candidates


def _optional_str_for_errors(value: Any, path: str, errors: list[str]) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    errors.append(f"{path} must be a string or null")
    return None


def _dedupe_preserving_order(values: Iterable[str]) -> list[str]:
    items: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = value.strip()
        if item and item not in seen:
            items.append(item)
            seen.add(item)
    return items


def _derive_document_flags(
    document_type: str,
    link_only: bool,
    multi_invoice: bool,
    email_raw: dict[str, Any],
    document_raw: dict[str, Any],
    invoice_raw: dict[str, Any],
    observed: ObservedFacts,
    evidence_raw: dict[str, Any],
) -> tuple[str, ...]:
    flags: list[str] = []
    requires_attachment = _optional_bool(document_raw.get("requires_attachment"))
    has_invoice_attachment = _optional_bool(document_raw.get("has_invoice_attachment"))
    source_attachments = _optional_string_list(evidence_raw.get("source_attachments"))
    attachment_count = len(source_attachments)
    is_invoice = document_type == "invoice"
    is_notice_type = document_type in {"ach_notice", "auto_draft_notice", "ben_e_keith_notice"}
    has_body_embedded_invoice_facts = (
        is_invoice
        and has_invoice_attachment is False
        and attachment_count == 0
        and _has_usable_body_embedded_invoice_facts(invoice_raw)
    )
    has_complete_invoice_for_payment = (
        is_invoice
        and has_invoice_attachment is True
        and _optional_str(invoice_raw.get("invoice_number")) is not None
        and _optional_float(invoice_raw.get("amount")) not in (None, 0)
        and not observed.has_conflicting_signals
    )

    def add(flag: str, condition: bool) -> None:
        if condition and flag not in flags:
            flags.append(flag)

    add(
        "multi_invoice_pdf",
        is_invoice
        and has_invoice_attachment is True
        and attachment_count == 1
        and (multi_invoice or observed.indicates_multiple_invoices),
    )
    add(
        "link_only_invoice",
        not is_notice_type
        and has_invoice_attachment is False
        and (link_only or (observed.mentions_payment_link_only and not has_body_embedded_invoice_facts)),
    )
    add("missing_invoice_attachment", observed.mentions_missing_invoice_attachment or (requires_attachment is True and has_invoice_attachment is False))
    add("contract_or_pay_application", observed.indicates_contract_or_pay_application or document_type in {"contract", "pay_application"})
    add(
        "vendor_inquiry",
        document_type in {"vendor_question", "payment_inquiry"}
        or (observed.indicates_vendor_question_or_payment_inquiry and not has_complete_invoice_for_payment),
    )
    add("wrong_destination", observed.indicates_wrong_destination)
    add(
        "past_due",
        (is_invoice and observed.current_invoice_is_past_due)
        or document_type == "past_due_notice"
        or _is_payable_invoice_past_due_by_dates(document_type, email_raw, invoice_raw, observed, evidence_raw),
    )
    add("statement_or_account_summary", observed.indicates_statement_or_account_summary or document_type in {"statement", "account_summary"})
    add("ach_or_auto_draft", observed.indicates_ach_or_auto_draft or document_type in {"ach_notice", "auto_draft_notice"})
    add("ben_e_keith", observed.indicates_ben_e_keith or document_type == "ben_e_keith_notice")
    add("lien_release_related", observed.mentions_lien_waiver_or_release or document_type == "lien_release")
    add("conflicting_signals", observed.has_conflicting_signals)
    add("low_text_quality", observed.has_low_text_quality)
    return tuple(flags)


def _is_payable_invoice_past_due_by_dates(
    document_type: str,
    email_raw: dict[str, Any],
    invoice_raw: dict[str, Any],
    observed: ObservedFacts,
    evidence_raw: dict[str, Any],
) -> bool:
    if document_type != "invoice":
        return False
    if _is_current_bucket_aging_exception(observed, evidence_raw):
        return False
    try:
        due_date = _optional_date(invoice_raw.get("due_date"))
        amount = _optional_float(invoice_raw.get("amount"))
        received_at = _optional_datetime(email_raw.get("received_at"))
    except (ExtractionValidationError, ValueError):
        return False
    return (
        due_date is not None
        and received_at is not None
        and amount is not None
        and amount > 0
        and received_at.date() > due_date
        and _has_explicit_due_date_evidence(evidence_raw)
    )


def _has_explicit_due_date_evidence(evidence_raw: dict[str, Any]) -> bool:
    summary = (_optional_str(evidence_raw.get("summary")) or "").lower()
    if not summary:
        return False
    if re.search(r"\b(due|payable|net due)\s+(on|upon)\s+receipt\b", summary):
        return False
    explicit_due_date_patterns = (
        r"\b(?:due date|payment due date|payment due|payments? due|due by|due on|please remit by|remit by)\b"
        r"[^.;,\n]{0,40}"
        r"(?:\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}\b)",
    )
    return any(re.search(pattern, summary) for pattern in explicit_due_date_patterns)


def _is_current_bucket_aging_exception(observed: ObservedFacts, evidence_raw: dict[str, Any]) -> bool:
    if not observed.contains_aging_summary or not observed.account_has_past_due_aging_balance:
        return False
    summary = _optional_str(evidence_raw.get("summary")) or ""
    normalized = summary.lower()
    return "current aging bucket" in normalized or "in current" in normalized


def _has_usable_body_embedded_invoice_facts(invoice_raw: dict[str, Any]) -> bool:
    has_invoice_identifier = _optional_str(invoice_raw.get("invoice_number")) is not None
    has_amount = _optional_float(invoice_raw.get("amount")) not in (None, 0)
    has_vendor = _optional_str(invoice_raw.get("vendor_name")) is not None
    has_property_or_bill_to_signal = any(
        _optional_str(invoice_raw.get(key)) is not None
        for key in (
            "bill_to",
            "bill_to_name_line_1",
            "bill_to_name_line_2",
            "bill_to_street_address",
            "property_code",
            "property_name",
            "service_address",
        )
    )
    return has_invoice_identifier and has_amount and has_vendor and has_property_or_bill_to_signal


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
            extraction.property_lookup.property_code,
            extraction.property_lookup.property_name,
            extraction.property_lookup.tenant,
            extraction.property_lookup.address,
            extraction.business_signals.business_unit_code,
            extraction.business_signals.possible_property_aliases,
        ]
    )
    if (
        not has_property_signal
        and "link_only_invoice" not in extraction.document.document_flags
        and not extraction.document.link_only
    ):
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
        errors.append(_type_error(path, "non-empty string", value))
        return ""
    return value


def _type_error(path: str, contract: str, value: Any) -> str:
    return f"{path} expected {contract}, got {type(value).__name__}"


def _optional_str(value: Any, path: str | None = None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    raise ExtractionValidationError([_type_error(path, "string or null", value) if path else f"expected string or null, got {type(value).__name__}"])


def _required_bool(payload: dict[str, Any], key: str, path: str, errors: list[str]) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        errors.append(_type_error(path, "boolean", value))
        return False
    return value


def _optional_bool(value: Any, path: str | None = None) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    raise ExtractionValidationError([_type_error(path, "boolean or null", value) if path else f"expected boolean or null, got {type(value).__name__}"])


def _required_confidence(payload: dict[str, Any], key: str, path: str, errors: list[str]) -> float:
    value = payload.get(key)
    if not isinstance(value, int | float) or isinstance(value, bool):
        errors.append(_type_error(path, "number", value))
        return 0.0
    result = float(value)
    if result < 0.0 or result > 1.0:
        errors.append(f"{path} must be between 0.0 and 1.0")
        return 0.0
    return result


def _required_string_list(payload: dict[str, Any], key: str, path: str, errors: list[str]) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        errors.append(_type_error(path, "list of strings", value))
        return []
    return value


def _optional_string_list(value: Any, path: str | None = None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    raise ExtractionValidationError([_type_error(path, "list of strings or null", value) if path else f"expected list of strings or null, got {type(value).__name__}"])


def _optional_lookup_string_list(value: Any, path: str | None = None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return [item.strip() for item in value if item.strip()]
    raise ExtractionValidationError([_type_error(path, "string, list of strings, or null", value) if path else f"expected string, list of strings, or null, got {type(value).__name__}"])


def _source_pages_and_legacy_refs(value: Any, errors: list[str]) -> tuple[list[int], list[EvidenceSourceRef]]:
    if value is None:
        return [], []
    if not isinstance(value, list):
        errors.append(_type_error("evidence.source_pages", "list of integers or null", value))
        return [], []
    pages: list[int] = []
    source_refs: list[EvidenceSourceRef] = []
    for index, item in enumerate(value):
        if isinstance(item, int) and not isinstance(item, bool):
            pages.append(item)
            continue
        if isinstance(item, str):
            source_ref = _legacy_source_page_ref(item)
            if source_ref is not None:
                source_refs.append(source_ref)
                if source_ref.page is not None:
                    pages.append(source_ref.page)
                continue
        errors.append(_type_error(f"evidence.source_pages[{index}]", "integer page or legacy attachment:page reference", item))
    return _dedupe_ints(pages), source_refs


def _source_refs(value: Any, errors: list[str]) -> list[EvidenceSourceRef]:
    if value is None:
        return []
    if not isinstance(value, list):
        errors.append(_type_error("evidence.source_refs", "list or null", value))
        return []
    refs: list[EvidenceSourceRef] = []
    for index, raw_ref in enumerate(value):
        path = f"evidence.source_refs[{index}]"
        if not isinstance(raw_ref, dict):
            errors.append(_type_error(path, "object", raw_ref))
            continue
        attachment = _optional_str_for_errors(raw_ref.get("attachment"), f"{path}.attachment", errors)
        if attachment is None:
            errors.append(f"{path}.attachment must be a non-empty string")
            attachment = ""
        page = raw_ref.get("page")
        page_value = None
        if page is not None:
            if not isinstance(page, int) or isinstance(page, bool) or page < 1:
                errors.append(_type_error(f"{path}.page", "positive integer or null", page))
            else:
                page_value = page
        refs.append(EvidenceSourceRef(attachment=attachment, page=page_value))
    return refs


def _legacy_source_page_ref(value: str) -> EvidenceSourceRef | None:
    stripped = value.strip()
    match = re.match(r"^(?P<attachment>.+?):\s*page\s*(?P<page>\d+)$", stripped, re.IGNORECASE)
    if not match:
        return None
    return EvidenceSourceRef(attachment=match.group("attachment").strip(), page=int(match.group("page")))


def _dedupe_ints(values: Iterable[int]) -> list[int]:
    items: list[int] = []
    seen: set[int] = set()
    for value in values:
        if value not in seen:
            items.append(value)
            seen.add(value)
    return items


def _dedupe_source_refs(values: Iterable[EvidenceSourceRef]) -> list[EvidenceSourceRef]:
    items: list[EvidenceSourceRef] = []
    seen: set[tuple[str, int | None]] = set()
    for value in values:
        key = (value.attachment, value.page)
        if value.attachment and key not in seen:
            items.append(value)
            seen.add(key)
    return items


def _optional_float(value: Any, path: str | None = None) -> float | None:
    if value is None:
        return None
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    raise ExtractionValidationError([_type_error(path, "number or null", value) if path else f"expected number or null, got {type(value).__name__}"])


def _optional_date(value: Any, path: str | None = None) -> date | None:
    if value is None:
        return None
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise ExtractionValidationError([_type_error(path, "ISO date string or null", value) if path else f"expected ISO date string or null, got {type(value).__name__}"])


def _optional_datetime(value: Any, path: str | None = None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    raise ExtractionValidationError([_type_error(path, "ISO datetime string or null", value) if path else f"expected ISO datetime string or null, got {type(value).__name__}"])
