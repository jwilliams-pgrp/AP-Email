from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import date, datetime
from decimal import Decimal
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from ap_automation.config import AppEnv, load_runtime_config
from ap_automation.models.decision import Decision, Destination
from ap_automation.services.msg_parser import ParsedAttachment


LOCAL_DIRECTORIES = (
    "local/ingest",
    "local/processed",
    "local/attachments",
    "local/emails",
    "local/audit/traces",
    "local/audit/prompts",
    "local/audit/extractions",
    "local/audit/actions",
)


class LocalArtifactStore:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root

    def ensure_directories(self) -> None:
        for relative_path in LOCAL_DIRECTORIES:
            (self.project_root / relative_path).mkdir(parents=True, exist_ok=True)

    def write_extraction_snapshot(self, run_id: str, payload: dict[str, Any]) -> str:
        relative_path = Path("local/audit/extractions") / f"{run_id}.json"
        return self.write_json_artifact(relative_path, payload)

    def write_prompt_snapshot(self, run_id: str, prompt: str) -> str:
        relative_path = Path("local/audit/prompts") / f"{run_id}.txt"
        return self.write_text_artifact(relative_path, prompt)

    def write_attachments(self, email_id: str, attachments: tuple[ParsedAttachment, ...]) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for index, attachment in enumerate(attachments, start=1):
            file_name = _safe_file_name(attachment.file_name, index)
            relative_path = Path("local/attachments") / email_id / file_name
            storage_path = self.write_bytes_artifact(relative_path, attachment.content)
            records.append(
                {
                    "file_name": attachment.file_name,
                    "content_type": attachment.content_type,
                    "storage_path": storage_path,
                    "_local_path": str(self.local_path_for(relative_path)),
                    "file_size_bytes": len(attachment.content),
                    "metadata": {**attachment.metadata, "stored_file_name": file_name},
                }
            )
        return records

    def write_email_html_preview(
        self,
        email_id: str,
        subject: str | None,
        sender_name: str | None,
        sender_email: str | None,
        received_at: str | None,
        body_text: str | None,
        body_html: str | None,
        attachments: tuple[ParsedAttachment, ...],
    ) -> str:
        relative_path = Path("local/emails") / email_id / "email.html"
        return self.write_text_artifact(
            relative_path,
            _render_sanitized_html_email(subject, sender_name, sender_email, received_at, body_text, body_html, attachments),
        )

    def write_action_plan(self, run_id: str, decision: Decision, destination: Destination | None, source_message_id: str | None) -> str:
        relative_path = Path("local/audit/actions") / f"{run_id}.json"
        plan = {
            "run_id": run_id,
            "outcome": decision.outcome,
            "destination_code": decision.destination_code,
            "destination_email": destination.email_address if destination else decision.destination_email,
            "destination_parent_folder": destination.parent_folder if destination else None,
            "destination_label": destination.label if destination else None,
            "source_message_id": source_message_id,
            "reason": decision.reason,
            "matched_rule_code": decision.matched_rule_code,
            "matched_rule_version": decision.matched_rule_version,
        }
        return self.write_json_artifact(relative_path, plan)

    def write_trace(self, run_id: str, decision: Decision) -> str:
        relative_path = Path("local/audit/traces") / f"{run_id}.mmd"
        destination = _mermaid_label(decision.destination_code or "No routing destination")
        decision_reason = _mermaid_label(decision.reason)
        lines = [
            "flowchart TD",
            f'  start["Audit Run<br/>{run_id}"] -->|"Processing started"| ingestion["Email Received"]',
            '  ingestion -->|"Email details were recorded for audit escalation"| attachments["Attachments Saved"]',
            '  attachments -->|"Invoice files were preserved for analysis"| selection["Attachment Reader Selected"]',
            '  selection -->|"Extractor selection was recorded"| document_intelligence["Attachments Read"]',
            '  document_intelligence -->|"Selected attachment text was recorded"| extraction["Invoice Details Read"]',
            '  extraction -->|"Extractor returned structured invoice information"| validation["Required Fields Checked"]',
            '  validation -->|"Required invoice data passed schema checks"| duplicate["Duplicate History Checked"]',
            '  duplicate -->|"No blocking duplicate stopped processing"| routing["Property Routing Checked"]',
            '  routing -->|"Property and routing signals were matched against setup tables"| rules["Workflow Policy Applied"]',
            f'  rules -->|"{decision_reason}"| decision["Business Decision<br/>{decision.outcome}"]',
            f'  decision -->|"Decision recorded with confidence {decision.confidence:.2f}"| action["Action Logged<br/>{destination}"]',
            '  action -->|"Mailbox action results were recorded"| finalize["Audit Complete"]',
            "  classDef success fill:#d1fae5,stroke:#15803d,color:#064e3b;",
            "  classDef failure fill:#fee2e2,stroke:#b91c1c,color:#7f1d1d;",
            "  class start,ingestion,attachments,selection,document_intelligence,extraction,validation,duplicate,routing,rules,decision,action,finalize success;",
        ]
        return self.write_text_artifact(relative_path, "\n".join(lines))

    def write_failure_trace(self, run_id: str, failed_step: str, error: str) -> str:
        relative_path = Path("local/audit/traces") / f"{run_id}.mmd"
        safe_error = _mermaid_label(error)
        steps = [
            ("ingestion", "Email Received", "Processing started"),
            ("attachments", "Attachments Saved", "Email details were recorded for audit escalation"),
            ("selection", "Attachment Reader Selected", "Invoice files were preserved for analysis"),
            ("document_intelligence", "Attachments Read", "Extractor selection was recorded"),
            ("extraction", "Invoice Details Read", "Selected attachment text was sent for extraction"),
            ("validation", "Required Fields Checked", "Extractor output was checked against required fields"),
        ]
        failed_node = _step_node_id(failed_step)
        lines = [
            "flowchart TD",
            f'  start["Audit Run<br/>{run_id}"]',
        ]
        successful_nodes = ["start"]
        previous_node = "start"
        for node_id, label, reason in steps:
            if node_id == failed_node:
                lines.append(f'  {previous_node} -->|"{reason}"| {node_id}["Failed Step<br/>{label}"]')
                lines.append(f'  {node_id} -->|"Processing stopped for business escalation"| error["Failure Reason<br/>{safe_error}"]')
                break
            lines.append(f'  {previous_node} -->|"{reason}"| {node_id}["{label}"]')
            successful_nodes.append(node_id)
            previous_node = node_id
        else:
            lines.append(f'  {previous_node} -->|"Unexpected processing issue"| failed["Failed Step<br/>{_mermaid_label(failed_step)}"]')
            lines.append(f'  failed -->|"Processing stopped for business escalation"| error["Failure Reason<br/>{safe_error}"]')
            failed_node = "failed"

        lines.extend(
            [
                "  classDef success fill:#d1fae5,stroke:#15803d,color:#064e3b;",
                "  classDef failure fill:#fee2e2,stroke:#b91c1c,color:#7f1d1d;",
                f"  class {','.join(successful_nodes)} success;",
                f"  class {failed_node},error failure;",
            ]
        )
        return self.write_text_artifact(relative_path, "\n".join(lines))

    def write_json_artifact(self, relative_path: Path, payload: dict[str, Any]) -> str:
        return self.write_text_artifact(relative_path, json.dumps(payload, default=_json_default, indent=2, sort_keys=True))

    def write_text_artifact(self, relative_path: Path, text: str) -> str:
        full_path = self.local_path_for(relative_path)
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(text, encoding="utf-8")
        return relative_path.as_posix()

    def write_bytes_artifact(self, relative_path: Path, content: bytes) -> str:
        full_path = self.local_path_for(relative_path)
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(content)
        return relative_path.as_posix()

    def read_bytes(self, artifact_ref: str) -> bytes:
        return self.local_path_for(Path(artifact_ref)).read_bytes()

    def read_text(self, artifact_ref: str, encoding: str = "utf-8") -> str:
        return self.local_path_for(Path(artifact_ref)).read_text(encoding=encoding)

    def local_path_for(self, relative_path: Path) -> Path:
        return self.project_root / relative_path


class AzureBlobArtifactStore(LocalArtifactStore):
    def __init__(
        self,
        project_root: Path,
        *,
        account_url: str,
        container_name: str,
        credential: Any | None = None,
        local_cache_root: Path | None = None,
    ) -> None:
        super().__init__(local_cache_root or Path(os.getenv("AP_ARTIFACT_CACHE_ROOT") or tempfile.mkdtemp(prefix="ap-artifacts-")))
        self._logical_project_root = project_root
        self._container_name = container_name
        if credential is None:
            from azure.identity import DefaultAzureCredential

            credential = DefaultAzureCredential()
        from azure.storage.blob import BlobServiceClient

        self._container = BlobServiceClient(account_url=account_url, credential=credential).get_container_client(container_name)

    def ensure_directories(self) -> None:
        for relative_path in LOCAL_DIRECTORIES:
            self.local_path_for(Path(relative_path)).mkdir(parents=True, exist_ok=True)

    def write_text_artifact(self, relative_path: Path, text: str) -> str:
        local_ref = super().write_text_artifact(relative_path, text)
        self._upload(relative_path, (self.project_root / local_ref).read_bytes(), "text/plain; charset=utf-8")
        return self._blob_ref(relative_path)

    def write_bytes_artifact(self, relative_path: Path, content: bytes) -> str:
        super().write_bytes_artifact(relative_path, content)
        self._upload(relative_path, content, None)
        return self._blob_ref(relative_path)

    def write_json_artifact(self, relative_path: Path, payload: dict[str, Any]) -> str:
        text = json.dumps(payload, default=_json_default, indent=2, sort_keys=True)
        local_ref = super().write_text_artifact(relative_path, text)
        self._upload(relative_path, (self.project_root / local_ref).read_bytes(), "application/json")
        return self._blob_ref(relative_path)

    def read_bytes(self, artifact_ref: str) -> bytes:
        if artifact_ref.startswith("blob://"):
            blob_name = self._blob_name_from_ref(artifact_ref)
            return self._container.download_blob(blob_name).readall()
        return super().read_bytes(artifact_ref)

    def read_text(self, artifact_ref: str, encoding: str = "utf-8") -> str:
        return self.read_bytes(artifact_ref).decode(encoding)

    def _upload(self, relative_path: Path, content: bytes, content_type: str | None) -> None:
        kwargs: dict[str, Any] = {"overwrite": True}
        if content_type:
            from azure.storage.blob import ContentSettings

            kwargs["content_settings"] = ContentSettings(content_type=content_type)
        self._container.upload_blob(relative_path.as_posix(), content, **kwargs)

    def _blob_ref(self, relative_path: Path) -> str:
        return f"blob://{self._container_name}/{relative_path.as_posix()}"

    def _blob_name_from_ref(self, artifact_ref: str) -> str:
        prefix = f"blob://{self._container_name}/"
        if not artifact_ref.startswith(prefix):
            raise ValueError("Blob artifact reference is not in the configured artifact container.")
        return artifact_ref[len(prefix) :]


def artifact_store_from_env(project_root: Path) -> LocalArtifactStore:
    config = load_runtime_config()
    if config.app_env == AppEnv.AZURE:
        if not config.artifact_account_url:
            raise RuntimeError("AZURE_STORAGE_ACCOUNT_URL or AZURE_STORAGE_ACCOUNT_NAME is required when APP_ENV=AZURE.")
        return AzureBlobArtifactStore(
            project_root,
            account_url=config.artifact_account_url,
            container_name=config.artifact_container,
        )
    return LocalArtifactStore(project_root)


def local_artifact_path(project_root: Path, record: dict[str, Any]) -> Path:
    local_path = record.get("_local_path")
    if isinstance(local_path, str) and local_path:
        return Path(local_path)
    storage_path = record.get("storage_path")
    if not isinstance(storage_path, str):
        raise ValueError("storage_path is required")
    if storage_path.startswith("blob://"):
        raise ValueError("Blob artifact has no local cache path available.")
    return project_root / storage_path


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    raise TypeError(f"Object of type {value.__class__.__name__} is not JSON serializable")


def _safe_file_name(file_name: str, index: int) -> str:
    path_name = Path(file_name).name
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "_", path_name).strip(" .")
    cleaned = cleaned or "attachment.bin"
    return f"{index:03d}-{cleaned}"


def _step_node_id(step_type: str) -> str:
    return {
        "INGESTION": "ingestion",
        "ATTACHMENT_PROCESSING": "attachments",
        "DOCUMENT_EXTRACTION_SELECTION": "selection",
        "DOCUMENT_INTELLIGENCE": "document_intelligence",
        "LLM_EXTRACTION": "extraction",
        "VALIDATION": "validation",
    }.get(step_type, step_type.lower())


def _mermaid_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', "'").replace("\r", " ").replace("\n", " ")


def _render_sanitized_html_email(
    subject: str | None,
    sender_name: str | None,
    sender_email: str | None,
    received_at: str | None,
    body_text: str | None,
    body_html: str | None,
    attachments: tuple[ParsedAttachment, ...],
) -> str:
    header_subject = escape(subject or "No subject")
    from_value = f"{sender_name} <{sender_email}>" if sender_name and sender_email else sender_email or sender_name or "Unknown sender"
    header_from = escape(from_value)
    header_received = escape(received_at or "-")
    body = _sanitize_preview_html(body_html) if body_html and body_html.strip() else escape(body_text or "(No plain-text body parsed)")
    attachment_items = "".join(f"<li>{escape(item.file_name)}</li>" for item in attachments) or "<li>None</li>"

    return (
        "<!doctype html>\n"
        "<html lang=\"en\">\n"
        "<head>\n"
        "  <meta charset=\"utf-8\" />\n"
        "  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />\n"
        f"  <title>{header_subject}</title>\n"
        "  <style>\n"
        "    :root { color-scheme: light; }\n"
        "    body { margin: 0; font-family: Segoe UI, Arial, sans-serif; background: #f5f7fb; color: #111827; }\n"
        "    .wrap { max-width: 980px; margin: 0 auto; padding: 20px; }\n"
        "    .card { background: #ffffff; border: 1px solid #d1d5db; border-radius: 10px; overflow: hidden; }\n"
        "    .hdr { padding: 16px 18px; border-bottom: 1px solid #e5e7eb; }\n"
        "    .hdr h1 { margin: 0 0 8px 0; font-size: 18px; }\n"
        "    .meta { margin: 0; font-size: 13px; color: #4b5563; }\n"
        "    .body { padding: 18px; white-space: pre-wrap; line-height: 1.5; }\n"
        "    .body :first-child { margin-top: 0; }\n"
        "    .body :last-child { margin-bottom: 0; }\n"
        "    .body img { max-width: 100%; height: auto; }\n"
        "    .atts { padding: 0 18px 18px 18px; }\n"
        "    .atts h2 { margin: 0 0 8px 0; font-size: 14px; }\n"
        "    .atts ul { margin: 0; padding-left: 20px; }\n"
        "  </style>\n"
        "</head>\n"
        "<body>\n"
        "  <div class=\"wrap\">\n"
        "    <article class=\"card\">\n"
        "      <header class=\"hdr\">\n"
        f"        <h1>{header_subject}</h1>\n"
        f"        <p class=\"meta\"><strong>From:</strong> {header_from}</p>\n"
        f"        <p class=\"meta\"><strong>Received:</strong> {header_received}</p>\n"
        "      </header>\n"
        f"      <section class=\"body\">{body}</section>\n"
        "      <section class=\"atts\">\n"
        "        <h2>Attachments</h2>\n"
        f"        <ul>{attachment_items}</ul>\n"
        "      </section>\n"
        "    </article>\n"
        "  </div>\n"
        "</body>\n"
        "</html>\n"
    )


_DROP_CONTENT_TAGS = {"script", "style", "object", "embed", "iframe"}
_DROP_VOID_TAGS = {"base", "link", "meta"}
_VOID_TAGS = {"br", "hr", "img"}
_ALLOWED_TAGS = {
    "a",
    "b",
    "blockquote",
    "br",
    "caption",
    "div",
    "em",
    "font",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "i",
    "img",
    "li",
    "ol",
    "p",
    "pre",
    "span",
    "strong",
    "sub",
    "sup",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "u",
    "ul",
}
_ALLOWED_GLOBAL_ATTRS = {"title", "alt", "colspan", "rowspan"}


def _sanitize_preview_html(source_html: str) -> str:
    cleaned = _strip_office_noise(source_html)
    parser = _PreviewHtmlSanitizer()
    parser.feed(cleaned)
    parser.close()
    return parser.html().strip() or "(No visible HTML body parsed)"


def _strip_office_noise(source_html: str) -> str:
    value = re.sub(r"<!--\s*\[if\b.*?\[endif\]\s*-->", "", source_html, flags=re.IGNORECASE | re.DOTALL)
    value = re.sub(r"@font-face\s*\{.*?\}", "", value, flags=re.IGNORECASE | re.DOTALL)
    value = re.sub(r"@page\b[^{]*\{.*?\}", "", value, flags=re.IGNORECASE | re.DOTALL)
    value = re.sub(r"\.[Mm]so[\w-]*\s*\{.*?\}", "", value, flags=re.DOTALL)
    return value


class _PreviewHtmlSanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._drop_depth = 0
        self._open_tags: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_name = tag.lower()
        if tag_name in _DROP_VOID_TAGS:
            return
        if tag_name in _DROP_CONTENT_TAGS:
            self._drop_depth += 1
            return
        if self._drop_depth or tag_name not in _ALLOWED_TAGS:
            return
        safe_attrs = self._safe_attrs(tag_name, attrs)
        attr_text = "".join(f' {name}="{escape(value, quote=True)}"' for name, value in safe_attrs)
        self._parts.append(f"<{tag_name}{attr_text}>")
        if tag_name not in _VOID_TAGS:
            self._open_tags.append(tag_name)

    def handle_endtag(self, tag: str) -> None:
        tag_name = tag.lower()
        if tag_name in _DROP_CONTENT_TAGS and self._drop_depth:
            self._drop_depth -= 1
            return
        if self._drop_depth or tag_name not in _ALLOWED_TAGS or tag_name in _VOID_TAGS:
            return
        if tag_name in self._open_tags:
            while self._open_tags:
                open_tag = self._open_tags.pop()
                self._parts.append(f"</{open_tag}>")
                if open_tag == tag_name:
                    break

    def handle_data(self, data: str) -> None:
        if not self._drop_depth:
            self._parts.append(escape(data))

    def handle_entityref(self, name: str) -> None:
        if not self._drop_depth:
            self._parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if not self._drop_depth:
            self._parts.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        return

    def html(self) -> str:
        while self._open_tags:
            self._parts.append(f"</{self._open_tags.pop()}>")
        return "".join(self._parts)

    def _safe_attrs(self, tag_name: str, attrs: list[tuple[str, str | None]]) -> list[tuple[str, str]]:
        safe: list[tuple[str, str]] = []
        for raw_name, raw_value in attrs:
            name = raw_name.lower()
            value = (raw_value or "").strip()
            if not value or name.startswith("on") or name in {"style", "class", "id"}:
                continue
            if name in _ALLOWED_GLOBAL_ATTRS:
                safe.append((name, value))
                continue
            if tag_name == "a" and name == "href" and _safe_href(value):
                safe.extend([("href", value), ("rel", "noreferrer noopener")])
                continue
            if tag_name == "img" and name == "src" and _safe_image_src(value):
                safe.append(("src", value))
        return safe


def _safe_href(value: str) -> bool:
    return value.lower().startswith(("http://", "https://"))


def _safe_image_src(value: str) -> bool:
    return value.lower().startswith("cid:")
