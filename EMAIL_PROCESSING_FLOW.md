# Email Processing and Classification Flow

## Purpose

This document explains, in human-readable terms, what happens to an AP email from the moment it is picked up for processing until it receives a final classification and action.

The authoritative behavior is defined in:

- `AGENTS.md`
- `specs/000-product.md`
- `specs/010-local-runtime.md`
- `specs/020-routing-rules.md`
- `specs/030-decision-engine.md`
- `specs/050-auditing.md`
- `specs/060-extraction-schema-and-golden-fixtures.md`
- `specs/070-local-msg-ingestion.md`

If this document ever conflicts with a spec, the spec controls.

## Core Processing Rule

Every email must move through the same pipeline:

```text
Ingestion -> Extraction -> Validation -> Rules Engine -> Decision -> Action -> Audit Log
```

In the implemented local flow, this is expanded into these audited steps:

```text
INGESTION
ATTACHMENT_PROCESSING
LLM_EXTRACTION or FIXTURE_EXTRACTION
VALIDATION
DUPLICATE_CHECK
ROUTING_MATCH
RULE_EVALUATION
DECISION
ACTION
FINALIZE
```

No step may be silently skipped. If the system cannot safely classify or route an email, the final outcome must be `ESCALATE`.

## Starting Conditions

Local development starts in a safe dry-run posture:

- `APP_ENV=LOCAL`
- `DRY_RUN=true`
- Local Postgres is the source of truth for decisions, workflow rules, routing rows, and audit records.
- Local filesystem folders under `local/` are used for emails, attachments, audit artifacts, and dry-run outbound manifests.
- Production mailbox mutation, production Blob Storage writes, and production database writes are disabled.

The local processor may use either:

- A test fixture extraction payload for golden scenarios.
- Azure OpenAI extraction for local `.msg` processing when no fixture is supplied.

## Step 1: Email Ingestion

The processor starts with a saved local email, currently expected to be an Outlook `.msg` file for the local workflow.

During ingestion, the system:

- Parses the source email from local disk.
- Extracts email metadata such as subject, sender, received timestamp, body text, and available transport headers.
- Computes or reuses an idempotency key so the same source email can be reprocessed without creating duplicate external actions.
- Creates or updates the email record in Postgres.
- Starts a new audit run for this processing attempt.
- Writes an `INGESTION` audit step.

The email body may also be converted into sanitized browser-readable HTML:

- The preview is written under `local/emails/{email_id}/email.html`.
- The path is stored on the email record.
- The preview must not include executable scripts, unsafe inline handlers, or remote resource loading.

If the email cannot be parsed safely, processing must fail loudly and finalize the audit run as failed. The processor must not continue as if missing data were valid.

## Step 2: Attachment Processing

After ingestion, attachments are extracted and recorded.

For each attachment, the system:

- Writes the binary file under `local/attachments/{email_id}/`.
- Records metadata in Postgres, including filename, content type when known, relative storage path, byte size, SHA-256 hash, and parser metadata.
- Avoids storing large binaries in Postgres.
- Deduplicates attachment records for the same email/path/hash during reprocessing.

PDF attachments receive deterministic content evaluation when possible:

- Whether the PDF is eligible for evaluation.
- Evaluation status such as `success`, `empty_text`, `corrupt_pdf`, `encrypted_pdf`, `extractor_unavailable`, or `low_quality`.
- Page count.
- Extraction method.
- Text excerpt.
- Text quality score.
- Evaluation version.

Non-PDF attachments are still persisted and audited, but their contents are not deterministically evaluated for routing.

The processor writes an `ATTACHMENT_PROCESSING` audit step containing attachment counts, filenames, hashes, storage paths, and PDF evaluation summaries.

Important safety behavior:

- Unsupported image, Word, or Excel attachments may trigger a deterministic `ESCALATE` outcome later.
- Corrupt, encrypted, empty, or low-quality PDFs are explicit conditions, not silent failures.
- Multiple invoices inside one PDF are treated differently from multiple separate invoice attachments.

## Step 3: Extraction and Classification

Extraction identifies observable facts from the email and documents. It does not make the final business decision.

The extractor may classify:

- Document type.
- Whether the document appears to be an invoice, statement, check request, contract, pay application, vendor question, ACH notice, auto-draft notice, Ben E Keith notice, lien release, or unknown.
- Whether a usable invoice attachment exists.
- Whether the invoice is link-only.
- Whether one PDF appears to contain multiple invoices.
- Vendor, invoice number, dates, amount, bill-to, property code, property name, and service address.
- Business signals such as ALC, Multifamily, or property-related hints.
- Observed risk facts such as lien waiver language, merge requirements, conflicting signals, or low text quality.
- Confidence values for overall extraction, document type, invoice fields, property identity, and business unit.

Extractor output must be structured JSON matching the extraction schema. Free-form prose is not valid input to the decision engine.

The processor writes an extraction audit step:

- `LLM_EXTRACTION` when Azure OpenAI or another configured LLM extractor is used.
- A fixture-identified extraction step when a test fixture is used.

When an LLM is used, the audit trail must include model name, prompt version, prompt artifact path or rendered prompt, raw response, parsed JSON, validation result, and confidence signals.

## Step 4: Extraction Validation

Before the decision engine can use extracted data, the payload is validated against the extraction contract.

Validation checks include:

- Required top-level fields are present.
- Document type is one of the allowed document types.
- Required observed facts are present.
- Confidence fields are present.
- Invoice auto-routing requirements are satisfied when the document is an invoice.
- Optional fields remain null when absent rather than being invented.

For automatic invoice routing, the system needs enough usable facts to make a deterministic match. At minimum, an invoice needs vendor and amount data plus at least one usable property or business-unit signal, such as bill-to, property code, property name, service address, or business unit.

If validation fails, or if required automatic-routing facts are missing, the email must not be guessed into a destination. It must resolve to `ESCALATE` or fail loudly depending on the failure type, and the validation result must be audited.

The processor writes a `VALIDATION` audit step.

## Step 5: Workflow Configuration Load

The rules engine needs active workflow configuration from Postgres before it can decide what to do.

Postgres owns mutable business policy, including:

- Routing destinations.
- Property ownership and management classification.
- Hard exception rules.
- Confidence thresholds.
- Amount thresholds.
- Statement handling.
- Duplicate detection policy.
- Enabled or disabled rule status.
- Effective dates and rule versions.

Application code owns only the evaluation engine, supported condition types, schema validation, deterministic matching, and safety invariants.

If required workflow configuration is missing, the engine must fail loudly. It must not silently substitute hard-coded business defaults.

## Step 6: Duplicate Check

Duplicate detection runs before normal routing.

The duplicate check may use persisted email, attachment, invoice, vendor, amount, hash, and invoice fact history from Postgres.

If the email or invoice is suspected or confirmed to be a duplicate:

- The final outcome is `ESCALATE`.
- The reason must identify duplicate suspicion.
- The escalation label is `DUPLICATE-SUSPECTED` when configured.
- Reprocessing must not create duplicate actions.

The processor writes a `DUPLICATE_CHECK` audit step with the duplicate status and relevant candidate information.

## Step 7: Routing Match

If duplicate detection does not stop the flow, the system attempts to match the email to routing data.

Routing match may use:

- Extracted property code.
- Extracted property name.
- Extracted service address.
- Email body fields.
- Attachment text fields.
- Bill-to business unit indicators.
- Standardized aliases.
- Active rows from the `properties` table.

The matching process has two parts:

- SQL retrieval returns a ranked candidate set from active property rows.
- Deterministic gate checks decide whether a candidate is safe enough to use.

The deterministic gate is the final authority. Fuzzy retrieval quality alone cannot approve a route.

Property routing must fall back to `ESCALATE` when:

- No candidate exists.
- The match score is below the configured minimum.
- The margin between top candidates is too small.
- There is a tie or near tie.
- Duplicate property codes create ambiguity.
- Required gate checks fail.
- The building or property is unknown.

The processor writes a `ROUTING_MATCH` audit step containing raw input signals, standardized signals, candidate list, scores, gate checks, selected candidate when present, and explicit `ESCALATE` reason when matching fails.

## Step 8: Rule Evaluation

The rules engine evaluates deterministic workflow rules in priority order.

The required priority is:

1. Hard exception rules.
2. Duplicate detection.
3. Routing table match.
4. Confidence threshold audit comparison.
5. `ESCALATE` fallback.

Hard exceptions are evaluated before normal routing because they represent known risk patterns.

Examples that normally resolve to `ESCALATE`:

- Multi-invoice PDF.
- Invoice plus lien waiver that requires merge handling.
- Link-only invoice with no usable attachment.
- Unsupported image, Word, or Excel attachment.
- Contract or pay application.
- Check request.
- Vendor question or payment inquiry when configured as ESCALATEable.
- Unknown or unmatched building.
- Ambiguous property match.

Examples with configured non-routing outcomes:

- Automated non-AP notification may resolve to `DISCARD`.
- Statement or account summary may resolve to configured `FILE` or `DISCARD`.
- ACH or auto-draft notice may resolve to `FILE`.
- Ben E Keith notice may resolve to `FILE`.
- Invoice over the configured amount threshold may resolve to `FILE` in the lien release location.

Examples eligible for `AUTO` routing:

- Clean Hillwood-owned property invoice with a deterministic property match.
- Clean external property manager invoice with a configured destination.
- ALC invoice with a configured ALC destination.
- Multifamily invoice with a configured Multifamily destination.

Low confidence is recorded and compared against the configured threshold for audit and observability. Under the current routing spec, low confidence by itself does not force `ESCALATE`; however, missing data, conflicting signals, or failed deterministic gates do force safe fallback behavior.

The processor writes a `RULE_EVALUATION` audit step. Each rule evaluation should record whether it matched, skipped, or failed, and which rule version was used.

## Step 9: Final Decision

Every processed email must produce exactly one final decision.

Allowed outcomes are:

- `AUTO`: automatically routed.
- `ESCALATE`: requires human decision.
- `FILE`: stored without routing.
- `FLAG`: critical issue or misdirected item.
- `DISCARD`: non-actionable item, still logged.

The decision object must include:

- Outcome.
- Destination, when applicable.
- Human-readable reason.
- Confidence.
- Extracted fields used by the decision.
- Routing match, if any.
- Matched workflow rule, if any.
- Matched rule version, if any.
- Model and prompt version, if an LLM was used.
- Whether the run was dry-run.

Decision reasons must be explicit and ESCALATEable.

Good reason:

```text
Invoice amount 15000 exceeds configured threshold 10000 -> FILE to lien release folder.
```

Bad reason:

```text
Seems high value.
```

The processor writes a `DECISION` audit step and persists the final decision in Postgres.

## Step 10: Action

After the decision is persisted, the action layer handles the outcome.

In local dry-run mode, actions must not mutate external systems. Instead, the system writes local artifacts describing what would have happened.

Examples:

- `AUTO` creates a dry-run route manifest rather than forwarding or moving a real email.
- `ESCALATE` creates a local ESCALATE artifact under `local/outbound/ESCALATE/`.
- `FLAG` creates a local ESCALATE or flag artifact for critical handling.
- `FILE` creates or records a local file destination artifact such as statements, ACH, Ben E Keith, or lien release handling.
- `DISCARD` records that no business action is required, while still preserving the audit trail.

External actions must support `dry_run`, produce logs, and create audit records.

The processor writes an `ACTION` audit step showing whether an action was taken or skipped because of dry-run.

## Step 11: Finalize

The processor finalizes the run after action handling.

Finalization includes:

- Writing a `FINALIZE` audit step.
- Marking the audit run as completed or failed.
- Recording the final outcome on the audit run.
- Populating completion timestamps.
- Producing a local visual trace artifact, such as a Mermaid file, under `local/audit/traces/`.

If processing fails after an audit run has started, finalization still matters. The processor must:

- Write the failure to the relevant audit step.
- Preserve available extraction input and output when extraction was attempted.
- Write a failed `FINALIZE` step.
- Mark the audit run as failed with `completed_at` populated.

Failed runs must not remain stuck in `started` status.

## End-to-End Example: Clean Invoice

For a clean invoice that can be automatically routed:

1. The `.msg` email is parsed from local disk.
2. Metadata and sanitized HTML preview are persisted.
3. Attachments are extracted and hashed.
4. PDF text evaluation succeeds.
5. The extractor returns valid JSON identifying an invoice, vendor, amount, and property or business unit signals.
6. Validation accepts the payload.
7. Duplicate detection finds no suspected duplicate.
8. Property or business-unit matching finds one safe configured route.
9. Hard exception rules do not match.
10. Normal routing rule matches a configured destination.
11. The final decision is `AUTO`.
12. Local dry-run writes a manifest describing the intended route.
13. Audit steps and visual trace artifacts are finalized.

## End-to-End Example: Ambiguous or High-Risk Email

For an ambiguous or high-risk email:

1. The email and attachments are still ingested and audited.
2. Extraction and validation still run when possible.
3. The risky fact is normalized, such as link-only invoice, multi-invoice PDF, unsupported attachment, conflicting signals, duplicate suspicion, unknown building, or failed property gate.
4. Rule evaluation matches a hard exception or falls back safely.
5. The final decision is usually `ESCALATE`, unless a spec defines a safer non-routing outcome such as `FILE`, `FLAG`, or `DISCARD`.
6. Local dry-run creates a ESCALATE, flag, file, or discard artifact.
7. Audit records explain exactly why the email did not auto-route.

## What The LLM May And May Not Do

LLMs may:

- Extract structured invoice data.
- Classify email or document type.
- Provide uncertainty reasoning.

LLMs may not:

- Make final routing decisions.
- Generate destination emails.
- Override routing tables.
- Perform actions.

The decision engine, not the LLM, is responsible for final outcomes.

## Audit Trail Summary

For every processed email, the audit trail must answer:

- What email was processed?
- What attachments were found?
- What extraction method was used?
- What facts were extracted?
- Did validation pass?
- Was a duplicate found?
- What routing candidates were considered?
- Which rules were evaluated?
- Which rule matched?
- What final decision was made?
- What action was taken or skipped?
- Was the run completed or failed?

This is required for debugging, replay, operational ESCALATE, and financial auditability.

## Safety Summary

The processor must never silently drop, guess, or route an uncertain email.

The safe default is:

```text
When in doubt, return ESCALATE.
```
