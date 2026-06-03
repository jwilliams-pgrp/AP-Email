from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ap_automation.services.local_artifacts import local_artifact_path


PDF_EVALUATION_VERSION = "pdf_eval.v2"


@dataclass(frozen=True)
class PdfDependencyStatus:
    available: bool
    detail: str


class PdfAttachmentEvaluator:
    def __init__(self, project_root: Path) -> None:
        self._project_root = project_root
        self._dependency_status = self._detect_dependency_status()

    @property
    def evaluation_version(self) -> str:
        return PDF_EVALUATION_VERSION

    @property
    def dependency_status(self) -> PdfDependencyStatus:
        return self._dependency_status

    def evaluate_attachments(self, attachment_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        evaluations: list[dict[str, Any]] = []
        for record in attachment_records:
            evaluations.append(self._evaluate_one(record))
        return evaluations

    def _evaluate_one(self, record: dict[str, Any]) -> dict[str, Any]:
        storage_path = record.get("storage_path")
        content_type = str(record.get("content_type") or "").lower()
        is_pdf = isinstance(storage_path, str) and (storage_path.lower().endswith(".pdf") or "pdf" in content_type)
        if not is_pdf:
            return {
                "eligible": False,
                "status": "not_pdf",
                "reason_code": "attachment_not_pdf",
                "page_count": 0,
                "extraction_method": "none",
                "text_excerpt": None,
                "text_quality_score": 0.0,
                "evaluation_version": PDF_EVALUATION_VERSION,
            }

        if not self._dependency_status.available:
            return {
                "eligible": True,
                "status": "extractor_unavailable",
                "reason_code": "pymupdf_dependency_unavailable",
                "page_count": 0,
                "extraction_method": "none",
                "text_excerpt": None,
                "text_quality_score": 0.0,
                "evaluation_version": PDF_EVALUATION_VERSION,
            }

        return self._extract_pdf(local_artifact_path(self._project_root, record))

    def _extract_pdf(self, path: Path) -> dict[str, Any]:
        try:
            import fitz
            document = fitz.open(path)
        except Exception as exc:
            return {
                "eligible": True,
                "status": "corrupt_pdf",
                "reason_code": f"pdf_parse_error:{exc.__class__.__name__}",
                "page_count": 0,
                "extraction_method": "pymupdf_text",
                "text_excerpt": None,
                "text_quality_score": 0.0,
                "evaluation_version": PDF_EVALUATION_VERSION,
            }

        if bool(getattr(document, "is_encrypted", False)):
            return {
                "eligible": True,
                "status": "encrypted_pdf",
                "reason_code": "pdf_encrypted",
                "page_count": document.page_count,
                "extraction_method": "pymupdf_text",
                "text_excerpt": None,
                "text_quality_score": 0.0,
                "evaluation_version": PDF_EVALUATION_VERSION,
            }

        page_count = document.page_count
        chunks: list[str] = []
        for page in document[:5]:
            try:
                text = page.get_text("text") or ""
            except Exception:
                text = ""
            if text:
                chunks.append(text)

        normalized = self._normalize_text("\n".join(chunks))
        if not normalized:
            return {
                "eligible": True,
                "status": "empty_text",
                "reason_code": "pdf_text_empty",
                "page_count": page_count,
                "extraction_method": "pymupdf_text",
                "text_excerpt": None,
                "text_quality_score": 0.0,
                "evaluation_version": PDF_EVALUATION_VERSION,
            }

        score = self._quality_score(normalized)
        excerpt = normalized[:8000]
        if score < 0.45:
            return {
                "eligible": True,
                "status": "low_quality",
                "reason_code": "pdf_text_low_quality",
                "page_count": page_count,
                "extraction_method": "pymupdf_text",
                "text_excerpt": excerpt,
                "text_quality_score": score,
                "evaluation_version": PDF_EVALUATION_VERSION,
            }
        return {
            "eligible": True,
            "status": "success",
            "reason_code": "text_extracted",
            "page_count": page_count,
            "extraction_method": "pymupdf_text",
            "text_excerpt": excerpt,
            "text_quality_score": score,
            "evaluation_version": PDF_EVALUATION_VERSION,
        }

    def _detect_dependency_status(self) -> PdfDependencyStatus:
        try:
            import fitz  # noqa: F401
        except Exception as exc:
            return PdfDependencyStatus(available=False, detail=f"{exc.__class__.__name__}: {exc}")
        return PdfDependencyStatus(available=True, detail="ok")

    @staticmethod
    def _normalize_text(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _quality_score(text: str) -> float:
        length = len(text)
        if length == 0:
            return 0.0
        alpha = sum(1 for ch in text if ch.isalpha())
        alpha_ratio = alpha / length
        tokens = re.findall(r"\b\w+\b", text)
        token_count = len(tokens)
        token_density = token_count / max(length / 10.0, 1.0)
        length_factor = min(length / 500.0, 1.0)
        alpha_factor = min(max(alpha_ratio, 0.0), 1.0)
        density_factor = min(token_density / 1.2, 1.0)
        return round((0.45 * length_factor) + (0.35 * alpha_factor) + (0.20 * density_factor), 4)
