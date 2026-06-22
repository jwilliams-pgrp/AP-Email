from __future__ import annotations

from pathlib import Path
from typing import Any


EXTRACTOR_SELECTION_VERSION = "pdf_extractor_selection.v1"
PYMUPDF_MIN_TEXT_QUALITY_SCORE = 0.45
SUPPORTED_DOCUMENT_INTELLIGENCE_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".heif"}
SUPPORTED_DOCUMENT_INTELLIGENCE_CONTENT_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/tiff",
    "image/bmp",
    "image/heif",
}


class DocumentExtractorSelector:
    @property
    def selection_version(self) -> str:
        return EXTRACTOR_SELECTION_VERSION

    def select_attachments(self, attachment_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [self.select_attachment(record) for record in attachment_records]

    def select_attachment(self, record: dict[str, Any]) -> dict[str, Any]:
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        pdf_evaluation = metadata.get("pdf_evaluation") if isinstance(metadata, dict) else None
        word_evaluation = metadata.get("word_evaluation") if isinstance(metadata, dict) else None
        base = _base_selection(pdf_evaluation)

        if _is_inline_attachment_record(record):
            return {**base, "selected_extractor": "none", "reason_code": "inline_attachment"}
        if _is_pdf(record):
            return self._select_pdf(pdf_evaluation)
        if _is_word(record):
            return self._select_word(base, word_evaluation)
        if _is_document_intelligence_supported_attachment(record):
            return {**base, "selected_extractor": "document_intelligence", "reason_code": "supported_non_pdf_requires_document_intelligence"}
        return {**base, "selected_extractor": "none", "reason_code": "unsupported_file_type"}

    def _select_pdf(self, pdf_evaluation: Any) -> dict[str, Any]:
        base = _base_selection(pdf_evaluation)
        if not isinstance(pdf_evaluation, dict):
            return {**base, "selected_extractor": "document_intelligence", "reason_code": "pdf_evaluation_missing"}
        status = pdf_evaluation.get("status")
        if status == "success":
            text_excerpt = pdf_evaluation.get("text_excerpt")
            score = _float(pdf_evaluation.get("text_quality_score"))
            if isinstance(text_excerpt, str) and " ".join(text_excerpt.split()) and score >= PYMUPDF_MIN_TEXT_QUALITY_SCORE:
                return {**base, "selected_extractor": "pymupdf", "reason_code": "pymupdf_text_quality_passed"}
            return {**base, "selected_extractor": "document_intelligence", "reason_code": "pymupdf_text_quality_insufficient"}
        if status == "extractor_unavailable":
            return {**base, "selected_extractor": "document_intelligence", "reason_code": "pymupdf_dependency_unavailable"}
        if status == "encrypted_pdf":
            return {**base, "selected_extractor": "document_intelligence", "reason_code": "encrypted_pdf_requires_document_intelligence"}
        if status == "corrupt_pdf":
            return {**base, "selected_extractor": "document_intelligence", "reason_code": "corrupt_pdf_requires_document_intelligence"}
        if status == "empty_text":
            return {**base, "selected_extractor": "document_intelligence", "reason_code": "empty_pdf_text_requires_document_intelligence"}
        if status == "low_quality":
            return {**base, "selected_extractor": "document_intelligence", "reason_code": "low_quality_pdf_text_requires_document_intelligence"}
        return {**base, "selected_extractor": "document_intelligence", "reason_code": "pdf_evaluation_not_safe"}

    def _select_word(self, base: dict[str, Any], word_evaluation: Any) -> dict[str, Any]:
        if not isinstance(word_evaluation, dict):
            return {**base, "selected_extractor": "none", "reason_code": "word_evaluation_missing"}
        text_excerpt = word_evaluation.get("text_excerpt")
        if word_evaluation.get("status") == "success" and isinstance(text_excerpt, str) and " ".join(text_excerpt.split()):
            return {
                **base,
                "selected_extractor": "word_text",
                "reason_code": "word_text_extracted",
                "text_quality_score": _float(word_evaluation.get("text_quality_score")),
                "text_length": len(text_excerpt),
            }
        return {**base, "selected_extractor": "none", "reason_code": str(word_evaluation.get("reason_code") or "word_text_unavailable")}


def summarize_extractor_selection(attachment_records: list[dict[str, Any]]) -> dict[str, Any]:
    selected: dict[str, int] = {}
    reason_codes: dict[str, int] = {}
    for record in attachment_records:
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        selection = metadata.get("extractor_selection") if isinstance(metadata, dict) else None
        if not isinstance(selection, dict):
            continue
        extractor = str(selection.get("selected_extractor") or "unknown")
        reason = str(selection.get("reason_code") or "unknown")
        selected[extractor] = selected.get(extractor, 0) + 1
        reason_codes[reason] = reason_codes.get(reason, 0) + 1
    return {
        "attachment_count": len(attachment_records),
        "selected_extractors": selected,
        "reason_codes": reason_codes,
        "selection_version": EXTRACTOR_SELECTION_VERSION,
    }


def _base_selection(pdf_evaluation: Any) -> dict[str, Any]:
    pdf = pdf_evaluation if isinstance(pdf_evaluation, dict) else {}
    text_excerpt = pdf.get("text_excerpt")
    return {
        "selected_extractor": "none",
        "reason_code": "not_selected",
        "selection_version": EXTRACTOR_SELECTION_VERSION,
        "page_count": _int(pdf.get("page_count")),
        "text_quality_score": _float(pdf.get("text_quality_score")),
        "text_length": len(text_excerpt) if isinstance(text_excerpt, str) else 0,
        "image_only_or_scanned": pdf.get("status") == "empty_text",
    }


def _is_pdf(record: dict[str, Any]) -> bool:
    storage_path = str(record.get("storage_path") or "")
    content_type = str(record.get("content_type") or "").lower()
    return Path(storage_path).suffix.lower() == ".pdf" or content_type == "application/pdf"


def _is_document_intelligence_supported_attachment(record: dict[str, Any]) -> bool:
    storage_path = str(record.get("storage_path") or "")
    content_type = str(record.get("content_type") or "").lower()
    return Path(storage_path).suffix.lower() in SUPPORTED_DOCUMENT_INTELLIGENCE_EXTENSIONS or content_type in SUPPORTED_DOCUMENT_INTELLIGENCE_CONTENT_TYPES


def _is_word(record: dict[str, Any]) -> bool:
    storage_path = str(record.get("storage_path") or "")
    content_type = str(record.get("content_type") or "").lower()
    return Path(storage_path).suffix.lower() in {".doc", ".docx"} or content_type in {
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }


def _is_inline_attachment_record(record: dict[str, Any]) -> bool:
    metadata = record.get("metadata")
    return isinstance(metadata, dict) and bool(metadata.get("is_inline"))


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _float(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0
