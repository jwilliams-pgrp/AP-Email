from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ap_automation.services.pdf_attachment_evaluator import PdfAttachmentEvaluator


class PdfAttachmentEvaluatorTests(unittest.TestCase):
    def test_valid_text_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf_path = root / "local" / "attachments" / "email-1" / "invoice.pdf"
            pdf_path.parent.mkdir(parents=True)
            pdf_path.write_bytes(
                b"%PDF-1.4\n"
                b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
                b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
                b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj\n"
                b"4 0 obj << /Length 92 >> stream\nBT /F1 12 Tf 72 720 Td (Invoice 100 Vendor Hillwood Property Address 9800 Hillwood Pkwy) Tj ET\nendstream endobj\n"
                b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n"
                b"xref\n0 6\n0000000000 65535 f \n0000000010 00000 n \n0000000062 00000 n \n0000000119 00000 n \n0000000272 00000 n \n0000000415 00000 n \n"
                b"trailer << /Size 6 /Root 1 0 R >>\nstartxref\n490\n%%EOF\n"
            )
            evaluator = PdfAttachmentEvaluator(root)
            result = evaluator.evaluate_attachments(
                [{"file_name": "invoice.pdf", "content_type": "application/pdf", "storage_path": "local/attachments/email-1/invoice.pdf"}]
            )[0]
            self.assertTrue(result["eligible"])
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["extraction_method"], "pymupdf_text")
            self.assertEqual(result["evaluation_version"], "pdf_eval.v2")
            self.assertIn("Invoice 100", result["text_excerpt"])

    def test_scanned_or_empty_text_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf_path = root / "local" / "attachments" / "email-1" / "scan.pdf"
            pdf_path.parent.mkdir(parents=True)
            import fitz

            document = fitz.open()
            document.new_page(width=300, height=300)
            document.save(pdf_path)
            evaluator = PdfAttachmentEvaluator(root)
            result = evaluator.evaluate_attachments(
                [{"file_name": "scan.pdf", "content_type": "application/pdf", "storage_path": "local/attachments/email-1/scan.pdf"}]
            )[0]
            self.assertEqual(result["status"], "empty_text")

    def test_corrupt_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf_path = root / "local" / "attachments" / "email-1" / "broken.pdf"
            pdf_path.parent.mkdir(parents=True)
            pdf_path.write_bytes(b"not a pdf")
            evaluator = PdfAttachmentEvaluator(root)
            result = evaluator.evaluate_attachments(
                [{"file_name": "broken.pdf", "content_type": "application/pdf", "storage_path": "local/attachments/email-1/broken.pdf"}]
            )[0]
            self.assertEqual(result["status"], "corrupt_pdf")

    def test_encrypted_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf_path = root / "local" / "attachments" / "email-1" / "encrypted.pdf"
            pdf_path.parent.mkdir(parents=True)
            import fitz

            document = fitz.open()
            document.new_page(width=300, height=300)
            document.save(pdf_path, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="secret", user_pw="secret")
            evaluator = PdfAttachmentEvaluator(root)
            result = evaluator.evaluate_attachments(
                [{"file_name": "encrypted.pdf", "content_type": "application/pdf", "storage_path": "local/attachments/email-1/encrypted.pdf"}]
            )[0]
            self.assertEqual(result["status"], "encrypted_pdf")

    def test_non_pdf_skipped(self) -> None:
        evaluator = PdfAttachmentEvaluator(Path("."))
        result = evaluator.evaluate_attachments(
            [{"file_name": "invoice.jpg", "content_type": "image/jpeg", "storage_path": "local/attachments/email-1/invoice.jpg"}]
        )[0]
        self.assertFalse(result["eligible"])
        self.assertEqual(result["extraction_method"], "none")


if __name__ == "__main__":
    unittest.main()
