# 070 - Local MSG Ingestion and Attachment Handling Spec

## Purpose

Enable local processing of saved Outlook `.msg` emails with full local audit artifacts.

The local processor must parse source email metadata, extract attachment binaries to local artifact storage, persist attachment metadata to Postgres, run fixture or Azure OpenAI `.msg` extraction, and keep the extraction/decision pipeline deterministic and audit logged.

## Local MSG Requirements

- `.msg` parsing remains supported for fixtures/tests; production-like local runs should ingest from Graph Intake.
- Graph Intake must claim the oldest Inbox message by moving it to the existing shared mailbox folder named `processing` before any extraction, validation, decision, or action work starts.
- Graph Intake must resolve `processing` by literal display name and fail loudly if the folder is missing or ambiguous.
- Graph Intake must load full message body and attachments from the post-claim Graph message id, not the original Inbox id.
- Parsed metadata should include subject, sender email, sender name, received timestamp, body text when available, and transport headers when available.
- Graph Intake must fetch full message body content for extraction; `bodypreview` may be used only as a fallback when full body content is unavailable.
- Graph HTML body content must be converted to readable plain text before extraction.
- Parsed email content must be converted to sanitized browser-readable HTML when body content is available.
- Sanitized HTML previews must be written under `local/emails/{email_id}/email.html`.
- The generated HTML preview path must be persisted on `emails.html_storage_path`.
- Attachment binaries must be written under `local/attachments/{email_id}/`.
- Attachment records in Postgres must include original filename, content type when known, relative storage path, byte size, SHA-256 hash, and useful parser metadata.
- Large attachment contents must never be stored in Postgres.
- The HTML preview must not embed attachment binaries; it should reference attachment names and rely on artifact endpoints for attachment access.
- The HTML preview must not include executable scripts, unsafe inline event handlers, or remote resource loading.
- Parsing failures must fail loudly with actionable errors; the processor must not silently continue with missing attachments.
- Unsupported nested message attachments may be stored as attachment binaries when available, but are not recursively processed in the initial local build.

## Pipeline Requirements

- The `ATTACHMENT_PROCESSING` audit step must include attachment count, filenames, hashes, and storage paths.
- A distinct `DOCUMENT_EXTRACTION_SELECTION` audit step must run after `ATTACHMENT_PROCESSING` and before Document Intelligence or fixture/Azure OpenAI extraction.
- A distinct `DOCUMENT_INTELLIGENCE` audit step must run after `DOCUMENT_EXTRACTION_SELECTION` and before fixture or Azure OpenAI extraction.
- Compatibility attachment content evaluation is limited to PDF attachments only.
- Non-PDF attachments must still be persisted and audit logged, but their content must not be evaluated by PyMuPDF.
- PDF attachment evaluation may produce per-attachment `pdf_evaluation` metadata with compatibility fields:
  - `eligible`
  - `status`
  - `reason_code`
  - `page_count`
  - `extraction_method`
  - `text_excerpt`
  - `text_quality_score`
  - `evaluation_version`
- Allowed PDF evaluation statuses are `success`, `empty_text`, `corrupt_pdf`, `encrypted_pdf`, `extractor_unavailable`, and `low_quality`.
- Fixture extraction remains allowed for tests and golden scenarios, but the fixture must run after `.msg` ingestion and attachment processing.
- Fixture and Azure extraction may return `extraction_batch.v1`; each batch item is validated and decided independently before one final email-level decision/action is aggregated.
- Deterministic extractor selection must choose `pymupdf`, `document_intelligence`, or `none` per attachment before Azure OpenAI extraction. PyMuPDF is the fast path for non-inline PDFs that open successfully, are not encrypted, have non-empty normalized text, and pass quality scoring. Azure Document Intelligence must analyze attachments selected for it and write full raw responses under `local/audit/extractions/{run_id}/document-intelligence/`.
- Azure OpenAI Foundry must be used as the local LLM extractor when no fixture is provided and must return one `extraction_batch.v1` response for the email, not one response per attachment.
- Reprocessing the same source email may create a new audit run, but must reuse the same email idempotency key and must not create duplicate attachment records for the same email/path/hash.
- The CLI must allow processing a `.msg` file with a fixture extraction locally.
- The CLI must allow processing a `.msg` file without a fixture locally.
- Local extraction without a fixture must fail loudly for unsupported non-`.msg` source files or failed Azure OpenAI calls.

## Acceptance Criteria

- A saved `.msg` sample can be parsed locally.
- Attachments from a `.msg` file are written to `local/attachments/{email_id}/`.
- Attachment metadata is persisted through the operational repository.
- Attachment artifact paths written to Postgres are relative to the project root.
- Sanitized email HTML previews are written to `local/emails/{email_id}/email.html` and referenced by `emails.html_storage_path`.
- Sanitized email HTML previews strip unsafe and noisy Outlook markup, including scripts, Office conditional comments, Office CSS blocks, unsafe event attributes, `@font-face`, `@page`, and obvious `.Mso...` CSS noise.
- Sanitized email HTML previews preserve uncertain visible business text by default, including forwarded headers, signatures, invoice facts, payment details, links, and surrounding text near stripped markup.
- Sanitized email HTML preview cleanup is display-only and does not change extraction text, routing decisions, actions, or audit semantics.
- The local audit trail records `ATTACHMENT_PROCESSING` details.
- The local audit trail records `DOCUMENT_EXTRACTION_SELECTION` after attachment processing and before Document Intelligence.
- The local audit trail records `DOCUMENT_INTELLIGENCE` usage with attachment counts, model call counts, pages analyzed, per-model pages, latency, statuses, and artifact paths.
- `ATTACHMENT_PROCESSING` includes a PDF evaluation summary with `pdf_total`, `pdf_success`, `pdf_failed`, and `non_pdf_total`.
- Compatibility PDF evaluation outcomes are explicit and auditable:
  - `success` when text extraction succeeds with usable text.
  - `empty_text` when extraction succeeds but yields no usable text.
  - `corrupt_pdf` when PDF parsing fails due to invalid/corrupt structure.
  - `encrypted_pdf` when the attachment is encrypted and unreadable without credentials.
  - `extractor_unavailable` when required PDF extraction dependency is unavailable at runtime.
  - `low_quality` when deterministic quality heuristics mark extracted text as insufficient quality.
- Non-PDF attachments are persisted and audited with `eligible=false` and are not content-evaluated.
- Required invoice attachments are considered readable only when the selected extractor produced non-empty normalized text: PyMuPDF through successful `pdf_evaluation`, or Document Intelligence through successful `document_intelligence`.
- Required supported attachments with missing selected-extractor metadata, selected Document Intelligence `configuration_required`, `analyzer_unavailable`, `error`, empty layout text, or unsupported extractor selection produce an ESCALATEable unreadable-required-attachment fact.
- Unreadable-required-attachment facts are item scoped. When `evidence.source_attachments` names a valid readable invoice file, other non-inline unsupported attachments remain visible in `ATTACHMENT_PROCESSING`, `DOCUMENT_EXTRACTION_SELECTION`, and `DOCUMENT_INTELLIGENCE` audit data but do not block that invoice route.
- The existing fixture or Azure OpenAI extraction, validation, decision, action, and finalization steps still run after `.msg` attachment handling.
- The existing fixture or Azure OpenAI extraction, validation, decision, action, and finalization steps still run after `.msg` attachment handling, extractor selection, and selected Document Intelligence analysis.
- A `.msg` sample can be processed locally without an extraction fixture.
- Graph Intake extraction input includes full body text, including service or property addresses beyond the Graph `bodypreview` prefix.
- The claimed Graph email remains the unit of action; local processing must move/file/route the post-claim mailbox item once according to the aggregated final decision.
- If processing fails after claim, the claimed Graph email must be moved to `ESCALATE` on a best-effort basis and the processing failure must remain visible.
