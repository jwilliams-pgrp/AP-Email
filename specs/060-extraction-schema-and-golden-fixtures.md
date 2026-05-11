# 060 - Extraction Schema and Golden Fixtures Spec

## Purpose

Define the local artifact folder layout and the validated extraction contract used by fixture extractors, Codex CLI local extractors, local extraction commands, and future LLM extractors.

The decision engine must consume only validated extraction payloads. It must not infer missing business facts from raw email text, attachment text, filenames, or model prose after validation.

## Local Artifact Folder Layout

Local runtime stores files under `local/`. These paths are local development artifacts and are the filesystem equivalent of future Blob Storage paths.

| Path | Purpose |
| --- | --- |
| `local/ingest/` | Saved source emails waiting to be processed. Initial samples may be copied here from `reference/test_emails/`. |
| `local/processed/` | Raw emails after local processing has completed. Moving files here is a local filesystem operation only. |
| `local/attachments/{email_id}/` | Extracted attachment files for one email. |
| `local/audit/traces/` | Visual execution trace artifacts, such as Mermaid files, keyed by run id. |
| `local/audit/prompts/` | Rendered prompt artifacts when an LLM extractor is used. |
| `local/audit/extractions/` | Raw extractor responses and validated parsed extraction snapshots when they are too large or sensitive for concise audit summaries. |
| `local/outbound/dry-run/` | Dry-run action manifests for actions that would mutate external systems in production. |
| `local/outbound/review/` | Local review queue artifacts for items with `REVIEW` or `FLAG` outcomes. |
| `local/outbound/review-statements/` | Local file destination for statements and account summaries. This matches the seeded `FOLDER_STATEMENTS` destination. |
| `local/outbound/ach/` | Local file destination for ACH, auto-draft, and projected payment notices. |
| `local/outbound/ben-e-keith/` | Local file destination for Ben E Keith payment notices. |
| `local/outbound/lien-release/` | Local file destination for high-dollar invoice or lien release handling when enabled by workflow policy. |

## Artifact Rules

- The processor must create missing local folders before writing artifacts.
- Large binaries must stay on disk, not in Postgres.
- Postgres records must store paths, hashes, metadata, and audit summaries.
- File paths written to Postgres must be relative to the project root unless a production storage provider explicitly requires absolute or URI paths.
- Reprocessing the same source email must not overwrite prior audit traces. Use `run_id` in trace and extraction artifact names.
- Dry-run outbound artifacts must describe intended actions without forwarding, moving, deleting, or sending anything externally.

## Extraction Contract

The validated extraction payload must be JSON with this top-level shape:

```json
{
  "schema_version": "extraction.v1",
  "extractor": {
    "type": "fixture",
    "name": "local_fixture",
    "model": null,
    "prompt_version": null
  },
  "email": {
    "subject": "string or null",
    "sender_email": "string or null",
    "received_at": "ISO-8601 string or null"
  },
  "document": {
    "document_type": "invoice",
    "requires_attachment": true,
    "has_invoice_attachment": true,
    "link_only": false,
    "multi_invoice": false
  },
  "invoice": {
    "invoice_number": "string or null",
    "invoice_date": "YYYY-MM-DD or null",
    "due_date": "YYYY-MM-DD or null",
    "amount": 0.0,
    "currency": "USD",
    "vendor_name": "string or null",
    "vendor_email": "string or null",
    "bill_to": "string or null",
    "property_code": "string or null",
    "property_name": "string or null",
    "service_address": "string or null"
  },
  "business_signals": {
    "business_unit_code": "PROP",
    "possible_property_aliases": [],
    "subject_instruction_hint": null
  },
  "observed_facts": {
    "mentions_past_due": false,
    "mentions_separate_backup_document": false,
    "mentions_merge_or_combine_required": false,
    "mentions_lien_waiver_or_release": false,
    "mentions_payment_link_only": false,
    "mentions_missing_invoice_attachment": false,
    "indicates_multiple_invoices": false,
    "indicates_statement_or_account_summary": false,
    "indicates_contract_or_pay_application": false,
    "indicates_vendor_question_or_payment_inquiry": false,
    "indicates_ach_or_auto_draft": false,
    "indicates_ben_e_keith": false,
    "indicates_sold_property": false,
    "has_conflicting_signals": false,
    "has_low_text_quality": false
  },
  "confidence": {
    "overall": 0.95,
    "document_type": 0.95,
    "invoice_fields": 0.95,
    "property_identity": 0.95,
    "business_unit": 0.95
  },
  "evidence": {
    "summary": "short explanation of extracted facts",
    "source_attachments": [],
    "source_pages": []
  }
}
```

## Required Fields

These fields must be present in every extraction payload:

- `schema_version`
- `extractor.type`
- `document.document_type`
- `document.link_only`
- `document.multi_invoice`
- every field under `observed_facts`
- `confidence.overall`
- `confidence.document_type`
- `confidence.invoice_fields`
- `confidence.property_identity`
- `confidence.business_unit`
- `evidence.summary`

For `document.document_type = "invoice"`, these invoice fields are required for automatic routing:

- `invoice.vendor_name`
- `invoice.amount`
- `invoice.bill_to` or `invoice.property_code` or `invoice.property_name` or `invoice.service_address`
- at least one usable property or business-unit signal

Missing required automatic-routing fields must produce `REVIEW`, not a guessed route.

## Optional Fields

These fields may be null when they are absent from the source material:

- `email.subject`
- `email.sender_email`
- `email.received_at`
- `invoice.invoice_number`
- `invoice.invoice_date`
- `invoice.due_date`
- `invoice.vendor_email`
- `invoice.property_code`
- `invoice.property_name`
- `invoice.service_address`
- `business_signals.business_unit_code`
- `business_signals.subject_instruction_hint`

Optional fields may improve matching and duplicate detection, but they must not be silently invented.

## Allowed Document Types

`document.document_type` must be one of:

- `invoice`
- `statement`
- `account_summary`
- `contract`
- `pay_application`
- `vendor_question`
- `payment_inquiry`
- `past_due_notice`
- `ach_notice`
- `auto_draft_notice`
- `ben_e_keith_notice`
- `lien_release`
- `unknown`

Unsupported document types must validate only as `unknown` and route through deterministic review fallback.

## Allowed Document Flags

`document.document_flags` is not part of the extractor-facing JSON contract. Extractors must return source-observable booleans under `observed_facts`; Python derives internal document flags after validation.

Internal `document.document_flags` may include:

- `multi_invoice_pdf`
- `invoice_plus_lien_waiver`
- `link_only_invoice`
- `missing_invoice_attachment`
- `contract_or_pay_application`
- `vendor_inquiry`
- `past_due`
- `statement_or_account_summary`
- `ach_or_auto_draft`
- `ben_e_keith`
- `lien_release_related`
- `sold_property_candidate`
- `conflicting_signals`
- `low_text_quality`

Flags are normalized workflow signals only. Final outcomes must still be selected by deterministic workflow rules.

## Special Case Representation

### Multi-Invoice PDF

Set:

```json
{
  "document": {
    "document_type": "invoice",
    "multi_invoice": true
  },
  "observed_facts": {
    "indicates_multiple_invoices": true
  }
}
```

Expected deterministic outcome: `REVIEW`.

### Link-Only Invoice

Set:

```json
{
  "document": {
    "requires_attachment": true,
    "has_invoice_attachment": false,
    "link_only": true
  },
  "observed_facts": {
    "mentions_payment_link_only": true
  }
}
```

Expected deterministic outcome: `REVIEW`.

### Invoice Plus Lien Waiver

Set:

```json
{
  "observed_facts": {
    "mentions_lien_waiver_or_release": true,
    "mentions_merge_or_combine_required": true
  }
}
```

Expected deterministic outcome: `REVIEW`.

### ACH, Auto-Draft, and Ben E Keith Notices

Use `ach_notice`, `auto_draft_notice`, or `ben_e_keith_notice` as `document_type`. These document types are not invoices and must not route to Medius unless a future spec explicitly changes that behavior.

Expected deterministic outcome: `FILE` when matching workflow policy is enabled, otherwise `REVIEW`.

### High-Dollar Invoice

Represent high-dollar invoices as normal invoice extractions with `invoice.amount` populated. The extractor must not decide whether the amount is high.

Expected deterministic outcome: `FILE` to the configured lien release folder using table-driven policy from `amount_review_threshold`.

## Confidence Rules

- Confidence values must be numbers from `0.0` through `1.0`.
- `confidence.overall` is the minimum safe summary confidence for routing.
- `confidence.property_identity` must reflect confidence in building, address, tenant, or alias identity.
- `confidence.business_unit` must reflect confidence in ALC, Multifamily, or Properties bill-to classification.
- If any confidence required for an automatic route is below the configured threshold, the decision must be `REVIEW`.
- A fixture may use high confidence only when the expected fact is explicitly known from the fixture definition.

## Codex CLI Local MSG Extraction

Local `.msg` extraction must use `codex exec` through the CLI as the LLM extractor unless a test fixture is explicitly supplied.

The Codex CLI extractor is allowed to receive parsed email subject, body text, sender metadata, and attachment metadata.

It must:

- Produce the same `extraction.v1` contract as fixture and future LLM extractors.
- Include the required `extraction.v1` JSON field names in the rendered prompt so local LLM output does not drift from the validator.
- Extract only facts explicitly present in the email text or attachment metadata.
- Set confidence below the automatic-routing threshold when invoice amount, vendor, property identity, or business-unit facts are incomplete.
- Use `observed_facts` for source-observable conditions such as link-only invoices, missing attachments, past-due notices, separate backup PDFs, merge/combine instructions, and conflicting signals.
- Do not return `document.document_flags`, `document.requires_merge`, routing outcomes, destinations, workflow decisions, or high-risk labels.
- Avoid final routing decisions, destination selection, or invented property codes.
- Audit `extractor.type = codex_cli`, `extractor.name = codex_exec`, and `extractor.prompt_version`.

Codex CLI `.msg` extraction is the local LLM extraction path. It is not a substitute for attachment OCR/PDF extraction unless attachment text is explicitly provided to the extractor.

## Golden Fixture Files

Golden fixture files should live under `tests/fixtures/extractions/` once tests are added.

Each golden fixture must include:

- source email filename
- extraction JSON payload
- expected outcome
- expected matched rule code
- expected destination code, when applicable
- expected human-readable reason pattern

Initial golden scenarios must cover:

- clean Hillwood-owned invoice
- clean external PM invoice
- ALC invoice
- Multifamily invoice
- multi-invoice PDF
- invoice plus lien waiver
- link-only invoice
- contract or pay application
- invoice over configured amount threshold
- duplicate invoice
- statement or account summary
- ACH or auto-draft notice
- Ben E Keith notice
- sold property
- unknown building
- low-confidence extraction

## Acceptance Criteria

- Local processing uses the folder layout in this spec.
- Missing local artifact folders are created before processing writes files.
- Attachment records store paths under `local/attachments/{email_id}/`.
- Audit trace records store paths under `local/audit/traces/`.
- Dry-run actions create manifests under `local/outbound/dry-run/`.
- Extraction payloads validate against the required top-level shape before the decision engine uses them.
- Invalid extraction payloads produce explicit validation errors and a reviewable outcome.
- Missing required automatic-routing fields produce `REVIEW`.
- Multi-invoice, link-only, and invoice-plus-lien-waiver cases are represented through explicit observed facts and normalized by Python into internal flags.
- Confidence threshold failures produce `REVIEW`.
- Golden fixture tests cover every scenario listed in this spec.
