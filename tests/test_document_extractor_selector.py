from __future__ import annotations

import unittest

from ap_automation.services.document_extractor_selector import DocumentExtractorSelector


def _record(
    *,
    file_name: str = "invoice.pdf",
    content_type: str = "application/pdf",
    storage_path: str = "local/attachments/email-1/invoice.pdf",
    status: str = "success",
    text_excerpt: str | None = "Invoice text",
    text_quality_score: float = 0.9,
    is_inline: bool = False,
) -> dict:
    metadata = {
        "is_inline": is_inline,
        "pdf_evaluation": {
            "eligible": storage_path.lower().endswith(".pdf"),
            "status": status,
            "reason_code": "text_extracted",
            "page_count": 1,
            "extraction_method": "pymupdf_text",
            "text_excerpt": text_excerpt,
            "text_quality_score": text_quality_score,
            "evaluation_version": "pdf_eval.v2",
        },
    }
    return {"file_name": file_name, "content_type": content_type, "storage_path": storage_path, "metadata": metadata}


class DocumentExtractorSelectorTests(unittest.TestCase):
    def test_clean_embedded_text_pdf_selects_pymupdf(self) -> None:
        selection = DocumentExtractorSelector().select_attachment(_record())
        self.assertEqual(selection["selected_extractor"], "pymupdf")
        self.assertEqual(selection["reason_code"], "pymupdf_text_quality_passed")
        self.assertEqual(selection["selection_version"], "pdf_extractor_selection.v1")

    def test_unsafe_pdf_selects_document_intelligence(self) -> None:
        selector = DocumentExtractorSelector()
        for status in ("empty_text", "low_quality", "corrupt_pdf", "encrypted_pdf"):
            with self.subTest(status=status):
                selection = selector.select_attachment(_record(status=status, text_excerpt=None, text_quality_score=0.0))
                self.assertEqual(selection["selected_extractor"], "document_intelligence")

    def test_supported_image_selects_document_intelligence(self) -> None:
        record = _record(file_name="invoice.jpg", content_type="image/jpeg", storage_path="local/attachments/email-1/invoice.jpg")
        selection = DocumentExtractorSelector().select_attachment(record)
        self.assertEqual(selection["selected_extractor"], "document_intelligence")

    def test_inline_and_unsupported_select_none(self) -> None:
        selector = DocumentExtractorSelector()
        self.assertEqual(selector.select_attachment(_record(is_inline=True))["selected_extractor"], "none")
        unsupported = _record(file_name="notes.txt", content_type="text/plain", storage_path="local/attachments/email-1/notes.txt")
        self.assertEqual(selector.select_attachment(unsupported)["selected_extractor"], "none")


if __name__ == "__main__":
    unittest.main()
