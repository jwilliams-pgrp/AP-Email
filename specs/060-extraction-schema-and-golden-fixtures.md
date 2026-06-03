# 060 - Extraction Schema and Golden Fixtures Spec

## Purpose

Define the local artifact folder layout and the validated extraction contract used by fixture extractors, Azure OpenAI local extractors, local extraction commands, and future LLM extractors.

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
| `local/audit/actions/` | Action manifests and mailbox routing records. |
| `local/outbound/ESCALATE/` | Local ESCALATE queue artifacts for items with `ESCALATE` or `FLAG` outcomes. |
| `local/outbound/ESCALATE-statements/` | Local file destination for statements and account summaries. This matches the seeded `FOLDER_STATEMENTS` destination. |
| `local/outbound/ach/` | Local file destination for ACH, auto-draft, and projected payment notices. |
| `local/outbound/ben-e-keith/` | Local file destination for Ben E Keith payment notices. |
| `local/outbound/lien-release/` | Local file destination for lien release handling when enabled by workflow policy. |

## Artifact Rules

- The processor must create missing local folders before writing artifacts.
- Large binaries must stay on disk, not in Postgres.
- Postgres records must store paths, hashes, metadata, and audit summaries.
- File paths written to Postgres must be relative to the project root unless a production storage provider explicitly requires absolute or URI paths.
- Reprocessing the same source email must not overwrite prior audit traces. Use `run_id` in trace and extraction artifact names.
- Dry-run outbound artifacts must describe intended actions without forwarding, moving, deleting, or sending anything externally.

## Extraction Contract

The per-document validated extraction payload must be JSON with this top-level shape:

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
    "project_number": "string or null",
    "job_number": "string or null",
    "invoice_date": "YYYY-MM-DD or null",
    "due_date": "YYYY-MM-DD or null",
    "amount": 0.0,
    "currency": "USD",
    "vendor_name": "string or null",
    "vendor_email": "string or null",
    "bill_to": "string or null",
    "bill_to_name_line_1": "string or null",
    "bill_to_name_line_2": "string or null",
    "bill_to_street_address": "string or null",
    "bill_to_suite": "string or null",
    "bill_to_city": "string or null",
    "bill_to_state": "string or null",
    "bill_to_zip_code": "string or null",
    "property_code": "string or null",
    "property_name": "string or null",
    "service_address": "string or null"
  },
  "property_lookup": {
    "property_code": [],
    "property_name": [],
    "tenant": [],
    "address": [],
    "suite": [],
    "city": [],
    "state": [],
    "zipcode": [],
    "address_candidates": [
      {
        "rank": 1,
        "label": "deliver_to",
        "street": "string or null",
        "city": "string or null",
        "state": "string or null",
        "zipcode": "string or null",
        "normalized_address": "string or null",
        "source": "string or null",
        "confidence": 0.95,
        "evidence_text": "string or null"
      }
    ]
  },
  "business_signals": {
    "business_unit_code": "PROP",
    "possible_property_aliases": [],
    "subject_instruction_hint": null
  },
  "observed_facts": {
    "current_invoice_is_past_due": false,
    "account_has_past_due_aging_balance": false,
    "contains_aging_summary": false,
    "mentions_separate_backup_document": false,
    "mentions_merge_or_combine_required": false,
    "mentions_lien_waiver_or_release": false,
    "mentions_payment_link_only": false,
    "mentions_missing_invoice_attachment": false,
    "indicates_multiple_invoices": false,
    "indicates_statement_or_account_summary": false,
    "indicates_contract_or_pay_application": false,
    "indicates_vendor_question_or_payment_inquiry": false,
    "indicates_wrong_destination": false,
    "latest_reply_indicates_no_ap_action": false,
    "indicates_ach_or_auto_draft": false,
    "indicates_ben_e_keith": false,
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
    "source_pages": [],
    "source_refs": [
      {
        "attachment": "invoice.pdf",
        "page": 1
      }
    ]
  }
}
```

Local `.msg` LLM extraction must return an additive batch envelope:

```json
{
  "schema_version": "extraction_batch.v1",
  "excluded_attachments": [
    {
      "file_name": "logo.jpg",
      "reason_code": "irrelevant_to_ap_workflow",
      "reason": "Decorative logo with no AP workflow facts.",
      "source": "filename"
    }
  ],
  "items": [
    {
      "item_kind": "attachment",
      "attachment_id": null,
      "item_key": "attachment:{sha256}",
      "display_name": "invoice.pdf",
      "metadata": {},
      "extraction": {}
    }
  ]
}
```

Each `items[].extraction` must independently validate as `extraction.v1`. Single legacy `extraction.v1` fixtures remain valid and are compatibility-wrapped as one email-level item by local processing.

- Return document items only for AP workflow-relevant sources: invoices, statements, payment inquiries, lien releases or waivers, contracts, pay applications, check requests, account summaries, past-due notices, ACH or auto-draft notices, Ben E Keith notices, wrong-destination evidence, or other documents with invoice, payment, property, vendor, amount, account, hard-exception, or routing facts.
- Omit clearly irrelevant attachments from `items` after reviewing selected attachment text, Document Intelligence text, filename, and email context, and return them in optional batch-level `excluded_attachments`. Clearly irrelevant attachments include generic sign photos, legal notice images unrelated to the invoice, logos, and decorative/non-business images with no AP workflow facts.
- Standalone payment-instruction support PDFs attached alongside at least one separate invoice item may be returned in optional batch-level `excluded_attachments` with `reason_code = "payment_instruction_support"`. Examples include standalone wire instructions, ACH instructions, remittance instructions, and payment portal instructions. This exclusion applies only when the payment-instruction attachment has no invoice number, payable amount, bill-to/property/service address, statement balance, vendor question, dispute, missing-remittance question, unsupported invoice evidence, or hard-exception content. Embedded payment instructions inside an invoice PDF remain normal invoice evidence.
- Each `excluded_attachments[]` entry must include `file_name`, `reason_code`, and `reason`; `source` is optional. Allowed `reason_code` values are `irrelevant_to_ap_workflow` and `payment_instruction_support`. Allowed `source` values are `document_intelligence`, `pymupdf`, `filename`, and `email_context`.
- Excluded attachments must not appear in any item's `evidence.source_attachments` or `evidence.source_refs`.
- Return one email-level item only when the body independently contains routing facts, hard exceptions, vendor/payment inquiry, wrong-destination facts, link-only/no-attachment facts, or another non-attachment document.
- Do not merge invoice facts across attachments.
- Scope `evidence.source_attachments` and `evidence.source_refs` to the current item. Unrelated attachments, including clearly irrelevant, unsupported, or unreadable non-inline attachments, must be omitted from the valid invoice item's evidence while still being persisted and audit logged.
- Unsupported or unreadable attachments that are the invoice item's required evidence must remain in that item's `evidence.source_attachments` and produce an explicit ESCALATEable outcome.
- Unsupported, unreadable, image, spreadsheet, or word-processing attachments that contain AP workflow facts must be returned as items rather than omitted.
- Payment-instruction documents with vendor questions, disputes, missing-remittance questions, unsupported invoice evidence, statement or account-summary content, lien waivers, work tickets, backup packets, contracts, or pay applications must remain document items and route through deterministic policy. When a standalone payment-instruction document is the only actionable source in the batch, it must not be silently excluded.
- Use one LLM call returning the batch, not one call per attachment.
- One invalid item makes the batch invalid; local processing must fail loudly rather than silently routing partial results.
- Invalid `excluded_attachments` metadata makes the batch invalid. Excluded attachments are audit metadata only and must not create document items, decisions, property lookups, or routing aggregation inputs.

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

Missing required automatic-routing fields must produce `ESCALATE`, not a guessed route.
For invoices normalized to the internal `link_only_invoice` flag, property and business-unit identity are not required to apply the hard-exception escalation. Missing automatic-routing fields still block `AUTO`, but they must not replace the configured link-only escalation label or destination.

## Optional Fields

These fields may be null when they are absent from the source material:

- `email.subject`
- `email.sender_email`
- `email.received_at`
- `invoice.invoice_number`
- `invoice.project_number`
- `invoice.job_number`
- `invoice.invoice_date`
- `invoice.due_date`
- `invoice.vendor_email`
- `invoice.bill_to_name_line_1`
- `invoice.bill_to_name_line_2`
- `invoice.bill_to_street_address`
- `invoice.bill_to_suite`
- `invoice.bill_to_city`
- `invoice.bill_to_state`
- `invoice.bill_to_zip_code`
- `invoice.property_code`
- `invoice.property_name`
- `invoice.service_address`
- `business_signals.business_unit_code`
- `business_signals.subject_instruction_hint`

Optional fields may improve matching and duplicate detection, but they must not be silently invented.

`invoice.due_date` is an explicit calendar-date field only. Extractors must populate it only when source text explicitly labels a concrete calendar date as the due date, payment due date, remit-by date, or equivalent payment deadline. Receipt-based terms such as `Due On Receipt`, `Due Upon Receipt`, `Payable Upon Receipt`, `Net Due Upon Receipt`, or similar wording are payment terms, not due dates, and must leave `invoice.due_date = null`. Extractors must not infer `invoice.due_date` from invoice date, service date, activity date, posting date, email received date, or receipt-based terms.

`evidence.source_pages` is legacy page evidence for single-document contexts and must contain page numbers as integers. For multiple attachments, extractors should use `evidence.source_refs` with one object per cited source page. Each source ref must include the attachment filename and may include a positive integer page when page evidence is available. Legacy LLM outputs that put values such as `invoice.pdf:page1` in `source_pages` must be normalized into `source_refs` and integer `source_pages` instead of blocking deterministic validation; raw extractor output remains preserved in the audit extraction artifact.

`property_lookup.address_candidates` is optional and backward-compatible under `extraction.v1`. When present, each candidate must include a positive integer `rank`, one of the allowed labels, at least one address component, and may include LLM confidence and short visible evidence text for audit/debugging. Project names, property names, asset names, building aliases, tenant names, account names, and job descriptions are not address candidates unless an address component is visible in the same candidate. LLM candidate confidence is advisory only and must not authorize routing.

## Property Lookup Normalization

Extractors should return `property_lookup` values for database comparison when source text contains property, service, billing, bill-to, shipping, delivery, or site identity signals. These values are normalized structured lookup fields only: `property_code`, `property_name`, `tenant`, `address`, `suite`, `city`, `state`, `zipcode`, and optional `address_candidates`.

Each flat `property_lookup` field must be an array of normalized strings. Use an empty array when no value is present. Include explicit property/address candidates visible in the source, including complete address strings and component values. When a property/service/site/shipping/delivery address is visible with city, state, or ZIP, include the street-only value and the complete normalized address value in `property_lookup.address`, with the street-only value first.

Extractors should also return every visible plausible asset address in `property_lookup.address_candidates`, ranked by source-label strength. Allowed labels are `deliver_to`, `ship_to`, `service_location`, `site`, `property`, `bill_to`, and `customer_account`. Strongest labels are `DELIVER TO`, `SHIP TO`, `SERVICE LOCATION`, `SITE`, `JOB`, and `LOCATION`; customer/account or bill-to is medium when no stronger site signal exists; bill-to is weaker when a stronger site/shipping/delivery address also exists. Omit `address_candidates` entries when the source signal contains only a non-address property identity such as a project name, property name, building alias, tenant name, account name, or job description; place those facts in `property_lookup.property_name`, `property_lookup.property_code`, `property_lookup.tenant`, or `business_signals.possible_property_aliases` instead. Python must convert structured `address_candidates` into the flat `property_lookup.address`, `city`, `state`, and `zipcode` arrays in candidate rank order for compatibility. Do not include sender, vendor, remit-to, or email signature addresses unless that same address is explicitly labeled as the service, property, site, shipping, delivery, or bill-to address for the invoice.

Normalize extracted property lookup values before database comparison:

- Lowercase all values.
- Trim whitespace.
- Replace punctuation and special characters with spaces.
- Collapse multiple spaces.
- Expand common address abbreviations: `st` to `street`, `rd` to `road`, `dr` to `drive`, `pkwy` or `pwky` to `parkway`, `fwy` to `freeway`, `blvd` to `boulevard`, `ln` to `lane`, `ct` to `court`, and `ave` to `avenue`.
- Normalize directional and city abbreviations: `ft worth` to `fort worth`, `n` to `north`, `s` to `south`, `e` to `east`, and `w` to `west`.
- Normalize suite values by removing prefixes such as `ste`, `suite`, `unit`, and `#`; for example, `STE 300` becomes `300`.
- Keep states as lowercase 2-letter abbreviations.
- Keep ZIP codes as 5-digit numeric values only.
- Do not invent missing values.
- Treat labels such as Building, Property, Site, Job, Location, Ship To, Service Location, and the email subject as property identity sources when they explicitly contain property facts.
- Asset codes are short building aliases such as `GW31`, `GW 31`, `HCX`, `HC-2`, `ACC 14`, or `WP9`; return normalized compact variants in `property_lookup.property_code`, such as `gw31` or `hc2`.
- Asset names are building names such as `Alliance Gateway 31` or `Heritage Commons X`; return normalized names in `property_lookup.property_name` when visible.
- Preserve explicit visible asset names exactly in audit-facing fields such as `invoice.property_name`, `address_candidates.evidence_text`, and `evidence.summary`. Do not rewrite a visible asset name into a different canonical asset family because a nearby address, bill-to value, customer account, or code resembles another asset.
- When a visible property name and address disagree, prefer the visible property name for canonical asset normalization unless the source explicitly identifies the address as the service, site, delivery, shipping, or property address for the invoice. If the conflict cannot be resolved confidently, lower `confidence.property_identity` and set `observed_facts.has_conflicting_signals = true`.
- If `asset_reference` contains `Hillwood Commons II` / `HWC2` and the source visibly says `Hillwood Commons II`, normalize lookup candidates to `property_lookup.property_name=["hillwood commons ii"]` and `property_lookup.property_code=["hwc2"]`. Do not convert visible `Hillwood Commons II` to `Heritage Commons II` / `HC2` unless the source visibly says `Heritage Commons II` or `HC2`.
- Existing exact-code normalization remains valid when the source actually says `HC2`, `HC-2`, or `Heritage Commons II`; in those cases the extractor may normalize to `property_lookup.property_code=["hc2"]` and `property_lookup.property_name=["heritage commons ii"]` when supported by `asset_reference`.
- Visible Alliance Gateway shorthand such as `AG31`, `AG 31`, or `AG-31` may be normalized as `Alliance Gateway 31` when the corresponding building exists in `asset_reference`; use the listed asset name and configured asset alias, and do not invent missing Alliance Gateway assets.
- Tenant or occupant names shown beside a building alias, such as `GW 31 / US Conec`, should be returned in `property_lookup.tenant` as normalized text.
- Keep `invoice.bill_to_*` component fields when present, but format `invoice.bill_to` as a single compressed display line for audit/UI readability. Routing must rely on structured `property_lookup` and deterministic Postgres matching, not only `invoice.bill_to`.
- Validation must mirror visible structured `invoice.bill_to_*` address components into a ranked `bill_to` `property_lookup.address_candidates` entry when the extractor omitted the bill-to address from `property_lookup`. This is deterministic extraction normalization, not repository-side lookup synthesis.

## Allowed Document Types

`document.document_type` must be one of:

- `invoice`
- `check_request`
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

Unsupported document types must validate only as `unknown` and route through deterministic ESCALATE fallback.

## Allowed Document Flags

`document.document_flags` is not part of the extractor-facing JSON contract. Extractors must return source-observable booleans under `observed_facts`; Python derives internal document flags after validation.

Internal `document.document_flags` may include:

- `multi_invoice_pdf`
- `separate_lien_waiver`
- `link_only_invoice`
- `missing_invoice_attachment`
- `contract_or_pay_application`
- `vendor_inquiry`
- `wrong_destination`
- `past_due`
- `statement_or_account_summary`
- `ach_or_auto_draft`
- `ben_e_keith`
- `lien_release_related`
- `conflicting_signals`
- `low_text_quality`

Flags are normalized workflow signals only. Final outcomes must still be selected by deterministic workflow rules.

Routine invoice-payment collection language must not set `observed_facts.indicates_vendor_question_or_payment_inquiry` when a valid invoice attachment is present and extractable. Examples include "attached invoice is due", "please ESCALATE for payment", "when can we expect payment", invoice payment options, remittance instructions, and invoice dispute contact text.

`observed_facts.indicates_vendor_question_or_payment_inquiry` is reserved for AP research or response cases where the sender asks AP to answer, confirm, research, reconcile, or explain invoice, payment, or account facts. Examples include duplicate payment confirmation, "can you please confirm", "please advise", missing remittance, identifying which invoice an ACH paid, payment-to-invoice matching, multiple possible open invoices for one payment, account reconciliation, disputes, credits, missing backup/support questions, and no-attachment vendor payment/account questions.

`observed_facts.indicates_wrong_destination` is reserved for explicit recipient replies or forwards stating they are the wrong person, should not have received the routed email, or that AP should escalate because the prior destination was wrong.

`observed_facts.current_invoice_is_past_due` is reserved for facts showing the current payable invoice is itself explicitly past due, overdue, in collection, or the document is classified as a true `past_due_notice`. A payable current invoice with `invoice.due_date` before `email.received_at.date()` must not set this fact from date comparison alone. It may be true when the source includes explicit past-due, overdue, collection, reminder, `Due Date`, `Payment Due`, `Please remit by`, or equivalent payment-deadline wording for the current invoice and the labeled date is before receipt. `invoice.due_date` may still be populated for extracted invoice fields, including payable-upon-receipt terms when the document presents a payment due date, but extractors must not copy `invoice.invoice_date` into `invoice.due_date` unless the document explicitly presents that date as the payment due date. “Payable upon receipt,” “current invoice due,” prior balances, or copied invoice dates alone must not count as explicit past-due evidence. Statements and account summaries with aging tables, open items, or account-level past-due balances remain statement/account-summary filing candidates and must not be classified as past-due notices unless the document is explicitly a past-due or collection notice. It must be false when the current invoice amount or balance due is in the `Current` aging bucket, even if other account-level aging buckets have nonzero past-due balances.

`observed_facts.account_has_past_due_aging_balance` identifies separate account-level aging balances outside the current invoice. `observed_facts.contains_aging_summary` identifies the presence of an aging table or aging footer. These fields are observable extraction facts only and must not independently derive the internal `past_due` workflow flag.

## Invoice vs Account Summary Classification

Filename, attachment title, and subject are weak document-type metadata. They must not override extracted content. A document named `Receipt.pdf` that contains invoice-positive facts should be classified by the extractor as `invoice` unless explicit statement or account-summary structure dominates.

Invoice-positive signals include:

- invoice number, `INV:`, or `Invoice #`
- invoice date or due date
- current amount due, total due, current charges, service charges, subtotal, tax, or balance due
- bill-to, sold-to, ship-to, service address, site address, or property address
- current service period, line items, quantities, unit prices, delivery charges, or service charges
- payment, remittance, view-bill, or pay-now instructions

Use `statement` or `account_summary` only for documents dominated by non-payable receipts, customer statements, aging summaries, balance recaps, payment confirmations, transaction histories, or multiple open-item summaries. Do not use `account_summary` solely because a filename or title says "Receipt".

Do not classify as `statement` or `account_summary` solely because labels say `Statement Date`, `Summary of Charges`, `Previous Balance`, or `Balance Forward`. A utility, telecom, or service bill is an `invoice` when it presents a single current payable bill or invoice with invoice number, due date, current amount due or total due, current service charges or service period, and bill-to, service address, property, or payment/remittance facts.

If invoice-positive and account-summary signals both appear, the extractor should classify as `invoice` unless explicit account-summary or statement structure dominates. If a single payable invoice is complete, keep `document.document_type = "invoice"`, keep `observed_facts.indicates_statement_or_account_summary = false`, and mention conflicting statement labels in `evidence.summary`. Set `observed_facts.indicates_statement_or_account_summary = true` only when statement or account-summary structure dominates over the payable invoice structure. If unclear, keep `document.document_type = "invoice"`, lower `confidence.document_type`, and set `observed_facts.has_conflicting_signals = true`. Deterministic validation must preserve valid LLM `statement` and `account_summary` classifications after schema validation, including when invoice-like fields are present. It may reject malformed classifications, but it must not reinterpret a valid extractor document type as `invoice`.

## Invoice vs Pay Application Classification

Progress-billing terminology or columns do not by themselves make a document a `pay_application`. Columns or labels such as `Contract Amount`, `Percent Complete`, `Total Billed`, `Prior Billed`, and `Current Billed` may appear on professional-services invoices and must be weighed against the full document presentation.

Classify a document as `invoice` when invoice evidence dominates, including documents that show `INVOICE`, `Invoice No`, invoice date, `Total This Invoice`, payable amount, remittance copy, and payment instructions. Westwood-style or other professional-services progress-billing invoices that present as invoices must use `document.document_type = "invoice"` and `observed_facts.indicates_contract_or_pay_application = false`.

Reserve `document.document_type = "pay_application"` for explicit pay-application or draw-request evidence, such as forms titled `Application for Payment`, `Pay Application`, AIA-style payment applications, draw requests, or documents where the payment-application structure dominates the invoice evidence.

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

Expected deterministic outcome: `ESCALATE`.
Only use this representation when one attached PDF contains multiple invoices. Do not use it when invoices are split across separate attachment files.

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

Expected deterministic outcome: `ESCALATE`.
Use this representation only when invoice or bill retrieval/payment is link-only, the payable invoice or bill details are not available in the email body or attachments, and there is no usable invoice attachment. Utility, service, and account notices with visible bill facts such as amount, due date, account, or service location plus a portal/payment/view-bill link must use this representation even when the extractor otherwise classifies the document as `account_summary`. QuickBooks-style invoice emails that expose usable invoice facts in the body, including invoice number, vendor, amount, due date, bill-to or service-location details, and line items or work description, are body-embedded invoices rather than link-only invoices even when the email also includes print, save, view, or pay links. Do not use it for generic auto-pay enrollment, registration-only, or informational payment portal notices that do not identify a current bill requiring retrieval or payment.

HTML email parsing must preserve HTTP and HTTPS anchor targets in normalized body text so deterministic link-only safeguards can evaluate rendered links such as "Click here" or "View bill".

### Invoice With Separate Related Backup

Set:

```json
{
  "document": {
    "document_type": "invoice"
  },
  "observed_facts": {
    "mentions_separate_backup_document": true
  }
}
```

Expected deterministic outcome: `ESCALATE`.

Use this representation only when an invoice item is accompanied by a distinct extracted AP-relevant supporting-document item, including lien waivers, lien releases, work orders, field tickets, service tickets, delivery tickets, time-entry detail reports, hourly detail reports, shift reports, actual hours worked, hours worked, staffing hours, timesheets, time sheets, labor/detail backup, labor/material breakdowns, job completion records, signed tickets, or similar support. The supporting document does not need to say "invoice". Relation may be inferred from shared vendor, project/job, customer, location, invoice number, work order number, service date, technician, amount, line items, or work description. This is not a duplicate-invoice scenario and does not require `mentions_merge_or_combine_required = true`. Attachment count, excluded attachments, not-selected attachments, embedded invoice pages, same-invoice line-item or work detail, inline images, logos, decorative images, and photo filenames or image references inside the invoice PDF must not qualify.

### ACH, Auto-Draft, and Ben E Keith Notices

Use `ach_notice`, `auto_draft_notice`, or `ben_e_keith_notice` as `document_type`. For any email or attachment content clearly associated with Ben E Keith, set `observed_facts.indicates_ben_e_keith = true`; Python derives the internal `ben_e_keith` flag and table-driven workflow policy files it to the configured Ben E Keith destination before file-type and PDF-readability exceptions, including `.txt` integration attachments. These document types are not invoices and must not route to Medius unless a future spec explicitly changes that behavior.

Expected deterministic outcome: `FILE` when matching workflow policy is enabled, otherwise `ESCALATE`.

### Check Request

Use `check_request` as `document_type` when the document is a request to issue or process a check rather than a standard invoice.

Expected deterministic outcome: `AUTO` when deterministic property matching resolves to configured `MEDIUS_PROPERTIES`; otherwise `ESCALATE`.

### High-Dollar Invoice

Represent high-dollar invoices as normal invoice extractions with `invoice.amount` populated. Extract explicit `PROJECT NO` or `PROJECT NUMBER` values into `invoice.project_number`. Extract explicit `JOB NO` or `JOB NUMBER` values into `invoice.job_number`, not `invoice.project_number`. The extractor must not decide whether the amount is high.

Expected deterministic outcome: `AUTO` when the normal deterministic destination is `MEDIUS_PROPERTIES`; otherwise `ESCALATE` to the configured `ESCALATE_OVER_10000` destination when the invoice has a usable non-Properties destination. If no usable destination exists, use the existing explicit `ESCALATE` path such as unmatched building or fallback.

## Confidence Rules

- Confidence values must be numbers from `0.0` through `1.0`.
- `confidence.overall` is the minimum safe summary confidence for routing.
- `confidence.property_identity` must reflect confidence in building, address, tenant, or alias identity.
- `confidence.property_identity` must be lowered when visible property name, property code, or address signals conflict and the extractor cannot confidently resolve the configured asset from visible source evidence.
- `confidence.business_unit` must reflect confidence in ALC, Multifamily, or Properties bill-to classification. Multifamily classification is extraction evidence only and must not trigger the ALC escalation rule.
- Confidence values must be compared against the configured threshold and recorded in audit data.
- Confidence threshold comparison is audit-only and does not by itself force `ESCALATE`.
- A fixture may use high confidence only when the expected fact is explicitly known from the fixture definition.

## Azure OpenAI Local MSG Extraction

Local `.msg` extraction must use Azure OpenAI Foundry as the LLM extractor unless a test fixture is explicitly supplied.

The Azure OpenAI extractor is allowed to receive parsed email subject, body text, sender metadata, attachment metadata, PyMuPDF text excerpts, and Document Intelligence layout text excerpts when explicitly selected for the attachment.
It may also receive the read-only asset reference list from Postgres using `asset_name`, `asset_alias`, `asset_type`, and `address` so visible property/building signals can be normalized into canonical `property_lookup` candidates before deterministic Postgres matching. `asset_type` is context only; it may be used to normalize source-visible property words such as Retail, Multifamily, or Ground Lease, but must not invent facts or authorize routing.

It must:

- Produce the same `extraction.v1` contract as fixture and future LLM extractors.
- Include the required `extraction.v1` JSON field names in the rendered prompt so local LLM output does not drift from the validator.
- Extract only facts explicitly present in the email text or attachment metadata.
- Extract only facts explicitly present in the email text, selected attachment text excerpts, or attachment metadata.
- Normalize non-payable receipt-only documents to `document.document_type = "account_summary"` so deterministic workflow policy files them without routing. A receipt-labeled attachment with invoice number, invoice date or due date, terms, line items, tax, and total is an `invoice`, not an `account_summary`.
- Classify no-attachment vendor payment/account questions as `vendor_question` or `payment_inquiry`.
- Do not set `document.link_only` or `observed_facts.mentions_payment_link_only` when the email body already contains usable payable invoice facts such as invoice number, vendor, amount, due date, bill-to or service-location details, and line items or work description.
- Set confidence below the automatic-routing threshold when invoice amount, vendor, property identity, or business-unit facts are incomplete.
- Use `observed_facts` for source-observable conditions such as link-only invoices, missing attachments, past-due notices, wrong-destination replies, separate backup documents, merge/combine instructions, and conflicting signals.
- Return separate AP-relevant supporting documents as their own batch items when they contain workflow facts tied to an invoice.
- Set `observed_facts.mentions_separate_backup_document = true` on an invoice only when a distinct supporting-document item exists in the batch, such as lien waivers, work orders, tickets, time detail, shift reports, actual hours worked, hours worked, staffing hours, timesheets, time sheets, labor/detail backup, labor/material breakdowns, job completion records, or similar support. The support does not need to say "invoice"; infer relation from shared vendor, project/job, customer, location, invoice number, work order number, service date, technician, amount, line items, or work description. Do not treat this as a duplicate-invoice scenario.
- Do not set `observed_facts.mentions_separate_backup_document = true` for embedded invoice pages, same-invoice line-item or work detail, invoice-contained work descriptions, inline images, logos, decorative images, photo filenames or image references inside the invoice PDF, excluded irrelevant attachments, or not-selected attachments.
- Set `observed_facts.indicates_vendor_question_or_payment_inquiry = true` when the sender asks AP to answer, confirm, research, reconcile, or explain invoice, payment, or account facts, including duplicate payments received with invoice numbers listed and "Can you please Confirm?".
- Do not return `document.document_flags`, `document.requires_merge`, routing outcomes, destinations, workflow decisions, or high-risk labels.
- Return all visible plausible asset addresses in ranked `property_lookup.address_candidates` and mirror them into flat `property_lookup` arrays in the same order.
- Exclude vendor, remit-to, sender, and signature addresses from address candidates unless explicitly labeled as invoice site/service/shipping/delivery/bill-to.
- Avoid final routing decisions, destination selection, or invented property codes.
- Use asset reference rows from `vw_asset_lookup` only as normalization context for source-visible property, building, alias, asset-type, or address text; the reference list is not routing authority and must not be used to invent unseen invoice facts, destinations, outcomes, recipients, workflow rules, ownership, or final routing.
- Prefer Project, Job, Site, Service Location, Location, Deliver To, Ship To, or Property fields over Bill To when identifying the serviced property.
- Audit `extractor.type = azure_openai`, `extractor.name = azure_openai_foundry`, `extractor.model`, and `extractor.prompt_version`.

Azure OpenAI extraction responses must be validated before any downstream decision processing consumes them. If the LLM returns malformed JSON or JSON that violates `extraction_batch.v1`, the local processor may make one repair retry. The repair prompt must include the original prompt, the invalid response when available, and the exact validation or parse errors, and it must request a complete corrected response rather than a patch. All attempts must be audit logged. If the retry is still invalid, processing must fail at validation with an explicit ESCALATEable error; no partial batch item may route.

Before validation, Azure OpenAI extraction responses may be deterministically normalized by removing `property_lookup.address_candidates` entries where `street`, `city`, `state`, `zipcode`, and `normalized_address` are all null or blank. This normalization must not move project names, property names, tenant names, aliases, or evidence text into address fields, and must not invent missing address components. The validation audit payload must record the removed candidate count and item paths as a `valid_after_normalization` result. All remaining contract defects, including invalid labels, wrong types, non-numeric confidence values, duplicate item keys, malformed batch structure, and missing required fields, must still fail validation and use the normal Azure repair retry path.

Azure OpenAI `.msg` extraction is the local LLM extraction path. Deterministic attachment text extraction must run before this step when attachment content is required to resolve property identity.
Deterministic attachment extractor selection must run before this step for supported non-inline business attachments. PyMuPDF is the PDF fast path when deterministic quality gates pass. Azure Document Intelligence is required when PyMuPDF quality is insufficient, the PDF is scanned/image-only/encrypted/corrupt/low text, layout confidence is not safe, a supported image attachment is encountered, a supported non-PDF business attachment needs content extraction, or PyMuPDF is unavailable. No silent fallback is allowed when selected Document Intelligence cannot run in local non-fixture mode.
Compatibility attachment content evaluation is scoped to PDFs only. Non-PDF attachments are persisted and audit logged but are not content-evaluated by PyMuPDF.

Per attachment, local processing may populate compatibility `pdf_evaluation` metadata before LLM extraction:

```json
{
  "eligible": true,
  "status": "success",
  "reason_code": "text_extracted",
  "page_count": 2,
  "extraction_method": "pymupdf_text",
  "text_excerpt": "excerpt...",
  "text_quality_score": 0.91,
  "evaluation_version": "pdf_eval.v2"
}
```

For non-PDF attachments, `eligible` must be `false`, `extraction_method` must be `none`, and content-derived text must not be produced.
`text_excerpt` from `pdf_evaluation` may be passed to extractor input only when `metadata.extractor_selection.selected_extractor` is `pymupdf`.
Allowed PDF statuses are `success`, `empty_text`, `corrupt_pdf`, `encrypted_pdf`, `extractor_unavailable`, and `low_quality`.

Per attachment, local processing must populate deterministic `extractor_selection` metadata before LLM extraction:

```json
{
  "selected_extractor": "pymupdf",
  "reason_code": "pymupdf_text_quality_passed",
  "selection_version": "pdf_extractor_selection.v1",
  "page_count": 2,
  "text_quality_score": 0.91,
  "text_length": 1234,
  "image_only_or_scanned": false
}
```

Allowed selected extractors are `pymupdf`, `document_intelligence`, and `none`. Select `none` only for inline or unsupported attachments, with an explicit reason.

Per supported non-inline business attachment, local processing must also populate `document_intelligence` metadata before LLM extraction:

```json
{
  "eligible": true,
  "status": "success",
  "reason_code": "document_intelligence_analyzed",
  "model_ids": ["prebuilt-layout", "prebuilt-invoice"],
  "page_count": 2,
  "text_excerpt": "excerpt...",
  "fields": {},
  "confidences": {},
  "artifact_paths": ["local/audit/extractions/{run_id}/document-intelligence/invoice.pdf.prebuilt-layout.json"],
  "latency_ms": 1200,
  "errors": [],
  "analysis_version": "document_intelligence.v1"
}
```

Attachments selected for Document Intelligence must run `prebuilt-layout`. Selected PDF/image invoice-like attachments must also run `prebuilt-invoice`. Readability for DI-selected attachments is determined from `document_intelligence`: `eligible=true`, `status=success`, and non-empty normalized `text_excerpt` means readable. Readability for PyMuPDF-selected attachments is determined from `pdf_evaluation.status=success` with non-empty normalized `text_excerpt`. `configuration_required`, `analyzer_unavailable`, `error`, empty layout text, missing required selected-extractor metadata, or unsupported required attachments are unreadable for required invoice attachments. `prebuilt-invoice` failure alone must not make an attachment unreadable when `prebuilt-layout` succeeds with non-empty text. Unsupported files must be explicit with `selected_extractor=none`. Service failures must be explicit and auditable. Local non-fixture extraction must fail loudly when selected Document Intelligence cannot run because `AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT` or `AZURE_DOCUMENT_INTELLIGENCE_KEY` is missing or blank.

## LLM Interpretation Contract

LLM interpretation is separate from `extraction.v1`. For property lookup, it must be used as the final review whenever Postgres returns one or more property candidates. The LLM may identify the best candidate from the provided Postgres candidate list only; deterministic code must still validate the selected candidate and authorize the final outcome through workflow rules.

The validated interpretation payload must be JSON with this top-level shape:

```json
{
  "schema_version": "llm_interpretation.v1",
  "candidate_property_matches": [
    {
      "asset_id": "246",
      "asset_alias": "HC2",
      "confidence": 0.91,
      "evidence": [
        {
          "source": "attachment:invoice.pdf",
          "page": 1,
          "text": "Bill To: HC 2"
        }
      ]
    }
  ],
  "candidate_rule_matches": [],
  "ambiguity_flags": [],
  "recommended_outcome": null,
  "reason": "HC 2 appears to match configured property HC2."
}
```

Requirements:

- `schema_version` must be `llm_interpretation.v1`.
- `candidate_property_matches` must be a list.
- Candidate property matches must use `asset_id` values from the provided Postgres candidate dataset only.
- `asset_alias` is optional audit context only and must not be used as the primary identifier.
- Every candidate property match must include confidence and non-empty evidence.
- Candidate rule matches, when present, must refer only to configured workflow rules supplied to the prompt.
- `recommended_outcome` is optional, non-authoritative, and must be ignored for final routing authorization.
- The interpretation must not create destinations, email recipients, workflow rules, asset IDs, aliases, or high-risk labels.
- Invalid, missing, unavailable, ambiguous, or low-confidence LLM interpretation must prevent automatic property routing and remain ESCALATEable.
- A single valid interpreted candidate may select one candidate from the already-returned Postgres list.
- A valid interpreted bill-to-only or customer-account address candidate may select one active candidate only when the source evidence explicitly cites the bill-to/customer-account address, SQL returned one clear candidate above `property_match_min_score`, and no stronger property/site/service/deliver-to/ship-to signal identifies a different active asset.
- Unresolved project or job text does not make bill-to fallback ambiguous unless it identifies a different configured asset, address, property name, or property code.
- The deterministic rules engine must still authorize the final outcome and destination.
- For batch extraction, local processing should review property candidates for all extracted invoice items in one LLM interpretation call and map validated advisory results back by `item_key`.
- LLM interpretation responses may be retried once for malformed JSON or contract-shape defects. Retry must not repair invented asset IDs, aliases, destinations, workflow decisions, or rule references by guessing. If interpretation remains invalid, unavailable, ambiguous, or selects no validated candidate, deterministic routing must proceed without the advisory match and remain ESCALATEable when required.

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
- Multifamily invoice following non-ALC routing policy
- multi-invoice PDF
- invoice plus lien waiver
- link-only invoice
- contract or pay application
- check request
- wrong-destination reply
- invoice over configured amount threshold
- duplicate invoice
- statement or account summary
- ACH or auto-draft notice
- Ben E Keith notice
- automated non-AP system digest
- unknown building
- low-confidence extraction
- property address present only in attachment text

## Acceptance Criteria

- Local processing uses the folder layout in this spec.
- Missing local artifact folders are created before processing writes files.
- Attachment records store paths under `local/attachments/{email_id}/`.
- Audit trace records store paths under `local/audit/traces/`.
- Actions create manifests under `local/audit/actions/`.
- Extraction payloads validate against the required top-level shape before the decision engine uses them.
- Invalid extraction payloads produce explicit validation errors and a ESCALATEable outcome.
- Missing required automatic-routing fields produce `ESCALATE`.
- Multi-invoice, link-only, and invoice-plus-lien-waiver cases are represented through explicit observed facts and normalized by Python into internal flags.
- Utility, service, and account bill notices with no invoice attachment, current bill facts, and a preserved portal/payment/view-bill link are normalized to `link_only_invoice` and route to `ESCALATE`.
- Link-only invoices with no property code, property name, service address, bill-to, or business-unit signal route through `hard_link_only_invoice` to `ESCALATE_LINK_ONLY`.
- Body-embedded invoices with no attachment, usable invoice facts, and preserved print/save/view/pay links are not normalized to `link_only_invoice`.
- Confidence threshold comparisons are recorded for audit and observability.
- Golden fixture tests cover every scenario listed in this spec.
- Regression tests prove a FiberFirst-style utility or service bill labeled with statement-like text but containing invoice number `628308`, current amount due `172.89`, due date, service charges, and Hillwood Commons I / `HWC1` property facts is classified as `invoice` and routes `AUTO` to `MEDIUS_PROPERTIES` through `property_routing_match`.
- Document Intelligence layout failures for required invoice attachments do not silently auto-route; unresolved property identity remains ESCALATEable.
- Compatibility PDF evaluation outcomes are explicit and auditable with statuses `success`, `empty_text`, `corrupt_pdf`, `encrypted_pdf`, `extractor_unavailable`, and `low_quality`.
- Non-PDF attachments are persisted and audit logged but are not content-evaluated.
- Extractor attachment input includes primary `text_excerpt` from the selected extractor only.
- Extractor attachment input may include PyMuPDF excerpts only when `extractor_selection.selected_extractor=pymupdf` and `pdf_evaluation.status=success`.
- LLM interpretation validation rejects invented asset IDs, unsupported outcomes, and property candidates without evidence.
- Property matching uses LLM interpretation as a required final review whenever Postgres returns candidates; if the review is unavailable, invalid, ambiguous, or selects no validated candidate, automatic property routing does not occur.
- LLM final property review selects candidates by Postgres `asset_id`, not by legacy `property_code`.
- LLM final property review prompt tests prove bill-to/customer-account address fallback is allowed when it is the only clear active address candidate and prove stronger serviced-property conflicts still block bill-to fallback.
- Multi-item local processing performs one property review LLM call for all eligible item property candidate sets, not one call per item.
- Golden tests prove LLM-assisted interpretation cannot auto-route high-risk cases that match higher-priority deterministic rules.
- Extraction validation tests cover multiple structured address candidates, including a stronger `DELIVER TO 2451 WESTLAKE PKWY` candidate before a customer/bill-to address.
- Extraction validation tests prove visible structured `invoice.bill_to_*` address components are mirrored into `property_lookup.address_candidates` and flat address arrays when the extractor omitted the bill-to address candidate.
- Prompt tests prove the Azure prompt requests all plausible addresses, rank/label/confidence/evidence fields, label-strength ordering, and compressed `invoice.bill_to` formatting.
- Prompt tests prove the Azure extraction prompt distinguishes progress-billing professional-services invoices from explicit pay applications, does not infer `pay_application` from progress-billing columns alone, and reserves `pay_application` for explicit application/draw-request evidence.
- Prompt tests prove vendor-question guidance includes positive AP response/research examples and the routine invoice-payment collection negative rule.
- Prompt tests prove the Azure extraction prompt includes `asset_reference` rows with `asset_name`, `asset_alias`, `asset_type`, and `address`, and describes them as normalization context rather than routing authority.
- Asset reference rows come from `vw_asset_lookup` so canonical and custom assets are available as read-only normalization context.
- Prompt tests prove the Azure extraction prompt sets `mentions_separate_backup_document` only for invoice packages with distinct supporting-document items, distinguishes embedded invoice detail/images from separate backup, and states that explicit merge instructions are not required.
- Extraction validation tests prove structured `evidence.source_refs` are accepted and legacy filename/page strings in `evidence.source_pages` are normalized without stopping deterministic processing.
- Extraction validation tests prove Python derives the internal `past_due` flag for a payable invoice only from explicit current-invoice past-due evidence: `observed_facts.current_invoice_is_past_due=true`, `past_due_notice` classification, or an explicitly labeled due/payment date before `email.received_at.date()`. They prove payable-upon-receipt terms, copied invoice dates, unlabeled due dates, and valid `statement` or `account_summary` classifications do not derive the internal `past_due` flag from invoice-like fields, due-date comparisons, account-level aging balances, or an erroneous `observed_facts.current_invoice_is_past_due=true`.
- Extraction validation tests prove receipt-based terms such as `Payment Due: Due On Receipt` do not populate `invoice.due_date` and do not derive the internal `past_due` flag.
- Extraction validation tests cover `extraction_batch.v1`, multiple attachment items, email-level exception items, duplicate item keys, and invalid nested item failure.
- Extraction validation tests cover valid and invalid batch-level `excluded_attachments`, including rejection when an excluded attachment is cited by an item.
- Local processor tests prove extractor-excluded attachments are audit metadata only, appear in `not_selected_attachments` as `excluded_by_extractor` with `extractor_exclusion.reason_code`, and do not create document items or participate in item decision aggregation.
- Local processor tests prove standalone payment-instruction support PDFs excluded with `payment_instruction_support` are audited in `not_selected_attachments`, while payment-instruction documents with vendor questions, disputes, missing-remittance questions, unsupported invoice evidence, statements, lien waivers, contracts, or pay applications remain document items and follow existing deterministic outcomes.
- Regression tests prove a JPEG or other unsupported attachment wrongly returned as a document item still follows existing `hard_wrong_file_type` escalation behavior.
- Azure OpenAI extraction validation tests prove invalid LLM contracts are retried once, every attempt is audited, and exhausted retries fail validation without partial routing.
- Azure OpenAI prompt tests prove the initial extraction prompt places a compact `extraction_batch.v1` contract checklist before long business-rule guidance.
- Azure OpenAI prompt tests prove the initial extraction prompt requires every `items[].extraction` to be a complete `extraction.v1` object with required sections present.
- Azure OpenAI prompt tests prove the initial extraction prompt includes compact required-key and type guidance for string-or-null invoice fields, numeric confidence fields, boolean observed facts, array-based `property_lookup` fields, allowed document types, and allowed address labels.
- Azure OpenAI repair prompt tests prove exact validation or parse errors are included with a canonical contract checklist and compact `extraction_batch.v1` skeleton/type guidance.
- Azure OpenAI extraction repair remains limited to one retry; if the repaired batch is invalid, processing fails validation with an explicit ESCALATEable error and no partial item routes.
- Azure OpenAI extraction validation tests prove componentless `property_lookup.address_candidates` are removed before validation, processing continues, and the validation audit records the normalization count and item paths.
- LLM interpretation tests prove malformed advisory responses are retried once, while invented candidate IDs remain rejected without retry-based routing authorization.
- Multi-attachment golden tests prove same-destination items aggregate to one final route, mixed destinations escalate, and separate invoice PDFs do not trigger `multi_invoice_pdf`.
- Multi-attachment golden tests prove a valid invoice PDF can route when an unrelated unsupported attachment is present but excluded from the invoice item's evidence, while a single unsupported required invoice attachment or mixed supported/unsupported invoice evidence escalates.
