from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ap_automation.services.azure_openai_extractor import AzureOpenAIExtractor, _attachment_context_parts


def _record(root: Path, name: str, content_type: str | None, data: bytes, *, is_inline: bool = False) -> dict:
    path = root / "local" / "attachments" / "email-1" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {
        "file_name": name,
        "content_type": content_type,
        "storage_path": str(path.relative_to(root)).replace("\\", "/"),
        "_local_path": str(path),
        "file_size_bytes": len(data),
        "metadata": {"is_inline": is_inline},
    }


class AzureOpenAIAttachmentContextTests(unittest.TestCase):
    def test_pdf_and_docx_become_file_parts_without_base64_in_audit_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            records = [
                _record(root, "invoice.pdf", "application/pdf", b"%PDF-test"),
                _record(
                    root,
                    "support.docx",
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    b"docx-test",
                ),
            ]

            context = _attachment_context_parts(root, records)

            self.assertEqual([part["type"] for part in context.content_parts], ["file", "file"])
            self.assertEqual(context.content_parts[0]["file"]["filename"], "invoice.pdf")
            self.assertTrue(context.content_parts[0]["file"]["file_data"].startswith("data:application/pdf;base64,"))
            self.assertEqual(
                context.content_parts[1]["file"]["file_data"],
                "data:application/vnd.openxmlformats-officedocument.wordprocessingml.document;base64,"
                + base64.b64encode(b"docx-test").decode("ascii"),
            )
            metadata_json = json.dumps(context.audit_metadata)
            self.assertIn("invoice.pdf", metadata_json)
            self.assertNotIn(base64.b64encode(b"%PDF-test").decode("ascii"), metadata_json)
            self.assertEqual(context.audit_metadata["attached_file_count"], 2)

    def test_non_inline_image_becomes_image_url_and_inline_image_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            records = [
                _record(root, "invoice.png", "image/png", b"png-test"),
                _record(root, "logo.png", "image/png", b"logo-test", is_inline=True),
                _record(root, "notes.txt", "text/plain", b"notes"),
            ]

            context = _attachment_context_parts(root, records)

            self.assertEqual(len(context.content_parts), 1)
            self.assertEqual(context.content_parts[0]["type"], "image_url")
            self.assertEqual(
                context.content_parts[0]["image_url"]["url"],
                "data:image/png;base64," + base64.b64encode(b"png-test").decode("ascii"),
            )
            self.assertEqual(context.audit_metadata["attached_file_count"], 1)
            self.assertEqual(context.audit_metadata["skipped_file_count"], 2)

    def test_run_json_prompt_sends_multipart_user_content_when_context_is_present(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            context = _attachment_context_parts(root, [_record(root, "invoice.pdf", "application/pdf", b"%PDF-test")])
            captured: dict[str, object] = {}

            class FakeResponse:
                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return False

                def read(self) -> bytes:
                    return json.dumps({"choices": [{"message": {"content": "{\"ok\": true}"}}], "usage": {}}).encode(
                        "utf-8"
                    )

            def fake_urlopen(request, timeout):
                captured["payload"] = json.loads(request.data.decode("utf-8"))
                return FakeResponse()

            extractor = AzureOpenAIExtractor(
                root,
                endpoint="https://example.openai.azure.com",
                api_key="key",
                api_version="2024-12-01-preview",
                deployment="gpt-test",
            )
            with patch.dict("os.environ", {"AZURE_OPENAI_AUTH_MODE": "api_key"}, clear=False), patch(
                "urllib.request.urlopen", fake_urlopen
            ):
                result = extractor.run_json_prompt("Return JSON.", attachment_context=context)

            payload = captured["payload"]
            user_content = payload["messages"][1]["content"]
            self.assertIsInstance(user_content, list)
            self.assertEqual(user_content[0], {"type": "text", "text": "Return JSON."})
            self.assertEqual(user_content[1]["type"], "file")
            self.assertEqual(result.request_parameters["attachment_context"]["attached_file_count"], 1)
            self.assertNotIn("file_data", json.dumps(result.request_parameters))


if __name__ == "__main__":
    unittest.main()
