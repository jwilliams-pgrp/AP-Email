from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ap_automation.services.local_artifacts import LocalArtifactStore, local_artifact_path


DOCUMENT_INTELLIGENCE_VERSION = "document_intelligence.v1"
SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".heif"}
SUPPORTED_CONTENT_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/tiff",
    "image/bmp",
    "image/heif",
}


class DocumentIntelligenceConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class DocumentIntelligenceDependencyStatus:
    available: bool
    detail: str


class DocumentIntelligenceAttachmentAnalyzer:
    def __init__(
        self,
        project_root: Path,
        endpoint: str | None = None,
        api_key: str | None = None,
        client_factory: Callable[[str, str], Any] | None = None,
        artifact_store: LocalArtifactStore | None = None,
    ) -> None:
        self._project_root = project_root
        self._endpoint = endpoint
        self._api_key = api_key
        self._client_factory = client_factory
        self._artifact_store = artifact_store
        self._dependency_status = self._detect_dependency_status()

    @property
    def analysis_version(self) -> str:
        return DOCUMENT_INTELLIGENCE_VERSION

    @property
    def dependency_status(self) -> DocumentIntelligenceDependencyStatus:
        return self._dependency_status

    def analyze_attachments(
        self,
        attachment_records: list[dict[str, Any]],
        *,
        run_id: str,
        require_config: bool,
    ) -> list[dict[str, Any]]:
        client: Any | None = None
        if require_config and any(_is_supported_business_attachment(record) for record in attachment_records):
            client = self._client()

        results: list[dict[str, Any]] = []
        for record in attachment_records:
            result = self._analyze_one(record, run_id=run_id, client=client)
            results.append(result)
        return results

    def _analyze_one(self, record: dict[str, Any], *, run_id: str, client: Any | None) -> dict[str, Any]:
        if _is_inline_attachment_record(record):
            return _base_result(record, eligible=False, status="skipped_inline", reason_code="inline_attachment")
        if not _is_supported_attachment(record):
            return _base_result(record, eligible=False, status="unsupported_file_type", reason_code="unsupported_file_type")
        if not self._dependency_status.available and client is None:
            return _base_result(record, eligible=True, status="analyzer_unavailable", reason_code="azure_document_intelligence_dependency_unavailable")
        if client is None:
            return _base_result(record, eligible=True, status="configuration_required", reason_code="azure_document_intelligence_config_required")

        started_at = time.perf_counter()
        models = ["prebuilt-layout"]
        if _is_invoice_like(record):
            models.append("prebuilt-invoice")

        raw_artifact_paths: list[str] = []
        layout_text: str | None = None
        layout_page_count = 0
        layout_succeeded = False
        fields: dict[str, Any] = {}
        confidences: dict[str, float] = {}
        page_counts: list[int] = []
        errors: list[str] = []
        for model_id in models:
            try:
                raw = self._call_model(client, model_id, record)
                raw_artifact_paths.append(self._write_raw_artifact(run_id, record, model_id, raw))
                page_count = _extract_page_count(raw)
                page_counts.append(page_count)
                if model_id == "prebuilt-layout":
                    layout_succeeded = True
                    layout_text = _extract_content(raw)
                    layout_page_count = page_count
                if model_id == "prebuilt-invoice":
                    extracted_fields, extracted_confidences = _extract_fields(raw)
                    fields.update(extracted_fields)
                    confidences.update(extracted_confidences)
            except Exception as exc:
                errors.append(f"{model_id}:{exc.__class__.__name__}: {exc}")

        latency_ms = max(0, int(round((time.perf_counter() - started_at) * 1000)))
        if not layout_succeeded:
            result = _base_result(record, eligible=True, status="error", reason_code="document_intelligence_error")
            result.update({"model_ids": models, "latency_ms": latency_ms, "errors": errors})
            return result

        text_excerpt = _normalize_text(layout_text or "")[:8000] or None
        result = _base_result(record, eligible=True, status="success", reason_code="document_intelligence_analyzed")
        result.update(
            {
                "model_ids": models,
                "page_count": layout_page_count or (max(page_counts) if page_counts else 0),
                "text_excerpt": text_excerpt,
                "fields": fields,
                "confidences": confidences,
                "artifact_paths": raw_artifact_paths,
                "latency_ms": latency_ms,
                "errors": errors,
            }
        )
        return result

    def _client(self) -> Any:
        endpoint = _required_config(self._endpoint or os.environ.get("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT"), "AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT")
        auth_mode = os.environ.get("AZURE_DOCUMENT_INTELLIGENCE_AUTH_MODE", "identity").strip().lower()
        api_key = self._api_key or os.environ.get("AZURE_DOCUMENT_INTELLIGENCE_KEY")
        if self._client_factory is not None:
            return self._client_factory(endpoint, _required_config(api_key, "AZURE_DOCUMENT_INTELLIGENCE_KEY") if auth_mode == "api_key" else api_key or "identity")
        from azure.ai.documentintelligence import DocumentIntelligenceClient
        if auth_mode == "api_key":
            from azure.core.credentials import AzureKeyCredential

            return DocumentIntelligenceClient(endpoint=endpoint, credential=AzureKeyCredential(_required_config(api_key, "AZURE_DOCUMENT_INTELLIGENCE_KEY")))
        try:
            from azure.identity import DefaultAzureCredential
        except Exception as exc:
            if api_key:
                from azure.core.credentials import AzureKeyCredential

                return DocumentIntelligenceClient(endpoint=endpoint, credential=AzureKeyCredential(api_key))
            raise DocumentIntelligenceConfigurationError(
                "azure-identity is required for identity-based Azure Document Intelligence authentication. "
                "Set AZURE_DOCUMENT_INTELLIGENCE_AUTH_MODE=api_key only for local development fallback."
            ) from exc
        return DocumentIntelligenceClient(endpoint=endpoint, credential=DefaultAzureCredential())

    def _call_model(self, client: Any, model_id: str, record: dict[str, Any]) -> Any:
        storage_path = record.get("storage_path")
        if not isinstance(storage_path, str):
            raise ValueError("attachment storage_path is required")
        with local_artifact_path(self._project_root, record).open("rb") as handle:
            poller = client.begin_analyze_document(model_id, handle)
            result = poller.result()
        return result

    def _write_raw_artifact(self, run_id: str, record: dict[str, Any], model_id: str, raw: Any) -> str:
        filename = f"{_safe_name(str(record.get('file_name') or 'attachment'))}.{model_id}.json"
        relative = Path("local/audit/extractions") / run_id / "document-intelligence" / filename
        payload = _to_jsonable(raw)
        if self._artifact_store is not None:
            return self._artifact_store.write_json_artifact(relative, payload)
        directory = self._project_root / "local" / "audit" / "extractions" / run_id / "document-intelligence"
        directory.mkdir(parents=True, exist_ok=True)
        (self._project_root / relative).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return relative.as_posix()

    @staticmethod
    def _detect_dependency_status() -> DocumentIntelligenceDependencyStatus:
        try:
            import azure.ai.documentintelligence  # noqa: F401
            import azure.core.credentials  # noqa: F401
        except Exception as exc:
            return DocumentIntelligenceDependencyStatus(False, f"{exc.__class__.__name__}: {exc}")
        return DocumentIntelligenceDependencyStatus(True, "ok")


def summarize_document_intelligence(attachment_records: list[dict[str, Any]]) -> dict[str, Any]:
    statuses: dict[str, int] = {}
    pages_analyzed = 0
    per_model_pages: dict[str, int] = {}
    model_call_count = 0
    latency_ms = 0
    artifact_paths: list[str] = []
    analyzed = 0
    eligible = 0
    for record in attachment_records:
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        di = metadata.get("document_intelligence") if isinstance(metadata, dict) else None
        if not isinstance(di, dict):
            continue
        status = str(di.get("status") or "unknown")
        statuses[status] = statuses.get(status, 0) + 1
        if di.get("eligible"):
            eligible += 1
        if status == "success":
            analyzed += 1
        page_count = _int(di.get("page_count"))
        pages_analyzed += page_count
        model_ids = [str(model) for model in di.get("model_ids", []) if isinstance(model, str)]
        model_call_count += len(model_ids)
        for model_id in model_ids:
            per_model_pages[model_id] = per_model_pages.get(model_id, 0) + page_count
        latency_ms += _int(di.get("latency_ms"))
        artifact_paths.extend(path for path in di.get("artifact_paths", []) if isinstance(path, str))
    return {
        "attachment_count": len(attachment_records),
        "eligible_attachment_count": eligible,
        "analyzed_attachment_count": analyzed,
        "model_call_count": model_call_count,
        "pages_analyzed": pages_analyzed,
        "per_model_pages": per_model_pages,
        "latency_ms": latency_ms,
        "statuses": statuses,
        "artifact_paths": artifact_paths,
    }


def _base_result(record: dict[str, Any], *, eligible: bool, status: str, reason_code: str) -> dict[str, Any]:
    return {
        "eligible": eligible,
        "status": status,
        "reason_code": reason_code,
        "model_ids": [],
        "page_count": 0,
        "text_excerpt": None,
        "fields": {},
        "confidences": {},
        "artifact_paths": [],
        "latency_ms": 0,
        "errors": [],
        "analysis_version": DOCUMENT_INTELLIGENCE_VERSION,
    }


def _is_supported_business_attachment(record: dict[str, Any]) -> bool:
    return not _is_inline_attachment_record(record) and _is_supported_attachment(record)


def _is_supported_attachment(record: dict[str, Any]) -> bool:
    storage_path = str(record.get("storage_path") or "")
    content_type = str(record.get("content_type") or "").lower()
    return Path(storage_path).suffix.lower() in SUPPORTED_EXTENSIONS or content_type in SUPPORTED_CONTENT_TYPES


def _is_invoice_like(record: dict[str, Any]) -> bool:
    text = " ".join(str(record.get(key) or "") for key in ("file_name", "content_type", "storage_path")).lower()
    return _is_supported_attachment(record) and any(signal in text for signal in ("invoice", "inv", "bill", ".pdf", "image/"))


def _is_inline_attachment_record(record: dict[str, Any]) -> bool:
    metadata = record.get("metadata")
    return isinstance(metadata, dict) and bool(metadata.get("is_inline"))


def _extract_content(raw: Any) -> str | None:
    value = getattr(raw, "content", None)
    if isinstance(value, str):
        return value
    if isinstance(raw, dict) and isinstance(raw.get("content"), str):
        return raw["content"]
    return None


def _extract_page_count(raw: Any) -> int:
    pages = getattr(raw, "pages", None)
    if pages is None and isinstance(raw, dict):
        pages = raw.get("pages")
    return len(pages) if isinstance(pages, list) else 0


def _extract_fields(raw: Any) -> tuple[dict[str, Any], dict[str, float]]:
    documents = getattr(raw, "documents", None)
    if documents is None and isinstance(raw, dict):
        documents = raw.get("documents")
    fields: dict[str, Any] = {}
    confidences: dict[str, float] = {}
    if not isinstance(documents, list) or not documents:
        return fields, confidences
    first = documents[0]
    raw_fields = getattr(first, "fields", None)
    if raw_fields is None and isinstance(first, dict):
        raw_fields = first.get("fields")
    if not isinstance(raw_fields, dict):
        return fields, confidences
    for name, field in raw_fields.items():
        value = getattr(field, "content", None)
        if value is None:
            value = getattr(field, "value", None)
        if value is None and isinstance(field, dict):
            value = field.get("content", field.get("value"))
        if value is not None:
            fields[str(name)] = value
        confidence = getattr(field, "confidence", None)
        if confidence is None and isinstance(field, dict):
            confidence = field.get("confidence")
        if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
            confidences[str(name)] = float(confidence)
    return fields, confidences


def _to_jsonable(raw: Any) -> Any:
    if hasattr(raw, "as_dict"):
        return raw.as_dict()
    if hasattr(raw, "to_dict"):
        return raw.to_dict()
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(json.dumps(raw, default=lambda obj: getattr(obj, "__dict__", str(obj))))
    except TypeError:
        return {"repr": repr(raw)}


def _required_config(value: str | None, name: str) -> str:
    if value and value.strip():
        return value.strip()
    raise DocumentIntelligenceConfigurationError(f"{name} is required for Azure Document Intelligence attachment analysis.")


def _normalize_text(text: str) -> str:
    return " ".join(text.split())


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ".-_" else "_" for ch in value)[:120] or "attachment"


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
