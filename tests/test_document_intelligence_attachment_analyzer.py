from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ap_automation.services.document_intelligence_attachment_analyzer import (
    DocumentIntelligenceAttachmentAnalyzer,
    DocumentIntelligenceConfigurationError,
    summarize_document_intelligence,
)


class DocumentIntelligenceAttachmentAnalyzerTests(unittest.TestCase):
    def test_supported_attachment_writes_summary_and_raw_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            attachment_path = root / "local" / "attachments" / "email-1" / "invoice.pdf"
            attachment_path.parent.mkdir(parents=True)
            attachment_path.write_bytes(b"%PDF-1.4")
            analyzer = DocumentIntelligenceAttachmentAnalyzer(
                root,
                endpoint="https://example.cognitiveservices.azure.com",
                api_key="key",
                client_factory=lambda endpoint, key: FakeDocumentIntelligenceClient(),
            )

            records = [
                {
                    "file_name": "invoice.pdf",
                    "content_type": "application/pdf",
                    "storage_path": "local/attachments/email-1/invoice.pdf",
                    "metadata": {},
                }
            ]
            result = analyzer.analyze_attachments(records, run_id="run-1", require_config=True)[0]

            self.assertTrue(result["eligible"])
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["model_ids"], ["prebuilt-layout", "prebuilt-invoice"])
            self.assertEqual(result["page_count"], 2)
            self.assertIn("Invoice 100", result["text_excerpt"])
            self.assertEqual(result["fields"]["VendorName"], "Vendor LLC")
            self.assertEqual(result["confidences"]["VendorName"], 0.91)
            self.assertEqual(len(result["artifact_paths"]), 2)
            self.assertTrue((root / result["artifact_paths"][0]).exists())

    def test_unsupported_and_inline_attachments_are_explicit(self) -> None:
        analyzer = DocumentIntelligenceAttachmentAnalyzer(Path("."), client_factory=lambda endpoint, key: FakeDocumentIntelligenceClient())
        results = analyzer.analyze_attachments(
            [
                {"file_name": "terms.txt", "content_type": "text/plain", "storage_path": "local/attachments/email-1/terms.txt", "metadata": {}},
                {"file_name": "logo.png", "content_type": "image/png", "storage_path": "local/attachments/email-1/logo.png", "metadata": {"is_inline": True}},
            ],
            run_id="run-1",
            require_config=False,
        )

        self.assertEqual(results[0]["status"], "unsupported_file_type")
        self.assertFalse(results[0]["eligible"])
        self.assertEqual(results[1]["status"], "skipped_inline")
        self.assertFalse(results[1]["eligible"])

    def test_blank_api_key_config_fails_loudly_when_key_auth_required(self) -> None:
        import os

        old_auth_mode = os.environ.get("AZURE_DOCUMENT_INTELLIGENCE_AUTH_MODE")
        os.environ["AZURE_DOCUMENT_INTELLIGENCE_AUTH_MODE"] = "api_key"
        analyzer = DocumentIntelligenceAttachmentAnalyzer(Path("."), endpoint="", api_key="", client_factory=lambda endpoint, key: FakeDocumentIntelligenceClient())
        try:
            with self.assertRaises(DocumentIntelligenceConfigurationError):
                analyzer.analyze_attachments(
                    [{"file_name": "invoice.pdf", "content_type": "application/pdf", "storage_path": "local/attachments/email-1/invoice.pdf", "metadata": {}}],
                    run_id="run-1",
                    require_config=True,
                )
        finally:
            if old_auth_mode is None:
                os.environ.pop("AZURE_DOCUMENT_INTELLIGENCE_AUTH_MODE", None)
            else:
                os.environ["AZURE_DOCUMENT_INTELLIGENCE_AUTH_MODE"] = old_auth_mode

    def test_service_error_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            attachment_path = root / "local" / "attachments" / "email-1" / "invoice.pdf"
            attachment_path.parent.mkdir(parents=True)
            attachment_path.write_bytes(b"%PDF-1.4")
            analyzer = DocumentIntelligenceAttachmentAnalyzer(
                root,
                endpoint="https://example.cognitiveservices.azure.com",
                api_key="key",
                client_factory=lambda endpoint, key: ErrorDocumentIntelligenceClient(),
            )
            result = analyzer.analyze_attachments(
                [{"file_name": "invoice.pdf", "content_type": "application/pdf", "storage_path": "local/attachments/email-1/invoice.pdf", "metadata": {}}],
                run_id="run-1",
                require_config=True,
            )[0]

            self.assertEqual(result["status"], "error")
            self.assertEqual(result["reason_code"], "document_intelligence_error")
            self.assertTrue(result["errors"])

    def test_invoice_model_error_preserves_successful_layout_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            attachment_path = root / "local" / "attachments" / "email-1" / "invoice.pdf"
            attachment_path.parent.mkdir(parents=True)
            attachment_path.write_bytes(b"%PDF-1.4")
            analyzer = DocumentIntelligenceAttachmentAnalyzer(
                root,
                endpoint="https://example.cognitiveservices.azure.com",
                api_key="key",
                client_factory=lambda endpoint, key: InvoiceErrorDocumentIntelligenceClient(),
            )
            result = analyzer.analyze_attachments(
                [{"file_name": "invoice.pdf", "content_type": "application/pdf", "storage_path": "local/attachments/email-1/invoice.pdf", "metadata": {}}],
                run_id="run-1",
                require_config=True,
            )[0]

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["text_excerpt"], "Layout Invoice 100")
            self.assertTrue(result["errors"])

    def test_layout_text_is_authoritative_even_when_invoice_model_has_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            attachment_path = root / "local" / "attachments" / "email-1" / "invoice.pdf"
            attachment_path.parent.mkdir(parents=True)
            attachment_path.write_bytes(b"%PDF-1.4")
            analyzer = DocumentIntelligenceAttachmentAnalyzer(
                root,
                endpoint="https://example.cognitiveservices.azure.com",
                api_key="key",
                client_factory=lambda endpoint, key: EmptyLayoutDocumentIntelligenceClient(),
            )
            result = analyzer.analyze_attachments(
                [{"file_name": "invoice.pdf", "content_type": "application/pdf", "storage_path": "local/attachments/email-1/invoice.pdf", "metadata": {}}],
                run_id="run-1",
                require_config=True,
            )[0]

            self.assertEqual(result["status"], "success")
            self.assertIsNone(result["text_excerpt"])
            self.assertEqual(result["fields"]["VendorName"], "Vendor LLC")

    def test_usage_summary_counts_pages_models_latency_and_statuses(self) -> None:
        records = [
            {
                "metadata": {
                    "document_intelligence": {
                        "eligible": True,
                        "status": "success",
                        "model_ids": ["prebuilt-layout", "prebuilt-invoice"],
                        "page_count": 3,
                        "latency_ms": 20,
                        "artifact_paths": ["a.json"],
                    }
                }
            },
            {"metadata": {"document_intelligence": {"eligible": False, "status": "unsupported_file_type", "model_ids": [], "page_count": 0, "latency_ms": 0, "artifact_paths": []}}},
        ]

        summary = summarize_document_intelligence(records)

        self.assertEqual(summary["attachment_count"], 2)
        self.assertEqual(summary["eligible_attachment_count"], 1)
        self.assertEqual(summary["analyzed_attachment_count"], 1)
        self.assertEqual(summary["model_call_count"], 2)
        self.assertEqual(summary["pages_analyzed"], 3)
        self.assertEqual(summary["per_model_pages"], {"prebuilt-layout": 3, "prebuilt-invoice": 3})
        self.assertEqual(summary["latency_ms"], 20)
        self.assertEqual(summary["statuses"]["success"], 1)
        self.assertEqual(summary["artifact_paths"], ["a.json"])


class FakePoller:
    def __init__(self, result) -> None:
        self._result = result

    def result(self):
        return self._result


class FakeDocumentIntelligenceClient:
    def begin_analyze_document(self, model_id: str, document):
        if model_id == "prebuilt-invoice":
            return FakePoller(
                {
                    "content": "Invoice 100 Vendor LLC",
                    "pages": [{}, {}],
                    "documents": [
                        {
                            "fields": {
                                "VendorName": {"content": "Vendor LLC", "confidence": 0.91},
                            }
                        }
                    ],
                }
            )
        return FakePoller(SimpleNamespace(content="Layout Invoice 100", pages=[{}, {}], as_dict=lambda: {"content": "Layout Invoice 100", "pages": [{}, {}]}))


class ErrorDocumentIntelligenceClient:
    def begin_analyze_document(self, model_id: str, document):
        raise RuntimeError("service unavailable")


class InvoiceErrorDocumentIntelligenceClient:
    def begin_analyze_document(self, model_id: str, document):
        if model_id == "prebuilt-invoice":
            raise RuntimeError("invoice unavailable")
        return FakePoller(SimpleNamespace(content="Layout Invoice 100", pages=[{}], as_dict=lambda: {"content": "Layout Invoice 100", "pages": [{}]}))


class EmptyLayoutDocumentIntelligenceClient:
    def begin_analyze_document(self, model_id: str, document):
        if model_id == "prebuilt-invoice":
            return FakePoller(
                {
                    "content": "Invoice 100 Vendor LLC",
                    "pages": [{}],
                    "documents": [{"fields": {"VendorName": {"content": "Vendor LLC", "confidence": 0.91}}}],
                }
            )
        return FakePoller(SimpleNamespace(content="   ", pages=[{}], as_dict=lambda: {"content": "   ", "pages": [{}]}))


if __name__ == "__main__":
    unittest.main()
