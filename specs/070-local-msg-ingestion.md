# 070 - Local MSG Ingestion and Attachment Handling Spec

## Purpose

Enable local dry-run processing of saved Outlook `.msg` emails without mutating any mailbox or external system.

The local processor must parse source email metadata, extract attachment binaries to local artifact storage, persist attachment metadata to Postgres, run fixture or Codex CLI `.msg` extraction, and keep the extraction/decision pipeline deterministic and audit logged.

## Local MSG Requirements

- `.msg` files are parsed from local disk only.
- Parsed metadata should include subject, sender email, sender name, received timestamp, body text when available, and transport headers when available.
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
- Fixture extraction remains allowed for tests and golden scenarios, but the fixture must run after `.msg` ingestion and attachment processing.
- `codex exec` must be used as the local LLM extractor when no fixture is provided.
- Reprocessing the same source email may create a new audit run, but must reuse the same email idempotency key and must not create duplicate attachment records for the same email/path/hash.
- The CLI must allow processing a `.msg` file with a fixture extraction in local dry-run mode.
- The CLI must allow processing a `.msg` file without a fixture in local dry-run mode.
- Local extraction without a fixture must fail loudly for unsupported non-`.msg` source files or failed `codex exec` calls.

## Acceptance Criteria

- A saved `.msg` sample can be parsed locally.
- Attachments from a `.msg` file are written to `local/attachments/{email_id}/`.
- Attachment metadata is persisted through the operational repository.
- Attachment artifact paths written to Postgres are relative to the project root.
- Sanitized email HTML previews are written to `local/emails/{email_id}/email.html` and referenced by `emails.html_storage_path`.
- The local audit trail records `ATTACHMENT_PROCESSING` details.
- The existing fixture or Codex CLI extraction, validation, decision, action, and finalization steps still run after `.msg` attachment handling.
- A `.msg` sample can be processed locally without an extraction fixture.
