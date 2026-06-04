# Database Schema

Canonical data dictionary for local `apautomation` Postgres.

## Core Design

- Runtime routing is table-driven from Postgres.
- Asset routing resolves through `vw_asset_lookup`; canonical `asset` rows use `ownership.destination`, while `asset_custom` rows use a direct `destination_code`.
- Fuzzy retrieval uses `vw_asset_lookup` identity, tenant, and address signal columns directly.
- `db/schema.sql` is the complete replayable DDL baseline exported from live local Postgres.
- `db/seed.sql` is the complete replayable workflow/reference/config baseline.
- Generated visibility artifacts from live local Postgres are stored under `db/introspection/`.

## Configuration Tables

### `runtime_config`
- Asset matching keys: `property_match_top_n`, `property_match_min_score`
- Runtime config does not include action-suppression mode switches; processing behavior is uniform.

### `routing_destinations`
- `destination_code` (PK)
- `display_name`
- `email_address`
- `parent_folder` (Graph destination mail folder id for move)
- `label` (Graph category label to append when set)
- `send_teams_message`
- `send_email` (whether an email action should use `email_address`)
- `active`
- `created_at`, `updated_at`

### `ownership`
- `ownership` (unique)
- `destination` (FK to `routing_destinations.destination_code`)
- `created_at`

### `asset`
- `id` (bigint PK)
- `asset_name`
- `ownership` (FK to `ownership.ownership`)
- `asset_type`
- `asset_alias`
- `market_name`
- `market_area`
- `tenants`
- `address`
- `created_at`

### `asset_custom`
- `id` (bigint PK)
- `asset_alias`
- `asset_name` (required)
- `address`
- `destination_code` (nullable direct routing destination code)
- `comment`
- `created_at`

`asset_custom` is for management-maintained direct-destination lookup rows that do not belong in canonical asset ownership data. Its destination is explicit on the row.

### `vw_asset_lookup`
- `asset_source` (`asset` or `asset_custom`)
- `asset_lookup_id` (`asset:{id}` or `asset_custom:{id}`)
- `source_id`
- `asset_alias`
- `asset_name`
- `address`
- `tenants`
- `asset_type`
- `ownership`
- `market_name`
- `market_area`
- `comment`
- `destination_code`
- `destination_active`
- `created_at`

Repository matching, asset reference rows, and dashboard lookup reads use this view as the combined read surface.

Indexes:
- Trigram fuzzy indexes on normalized `asset_alias`, `asset_name`, `tenants`, and `address`.
- Trigram fuzzy indexes on normalized `asset_custom.asset_alias`, `asset_custom.asset_name`, and `asset_custom.address`.
- Filter indexes on `asset.ownership` and `ownership.destination`.

## Operational Tables

`emails`, `attachments`, `document_items`, `invoices`, `extractions`, `audit_runs`, `audit_steps`, `llm_interactions`, `decisions`, and `actions` store processed work and audit history. `escalate_queue` is an informational live mirror of the current Graph `ESCALATE` folder after mailbox sync; historical auditability remains in the processing and audit tables.

### `emails`
- `email_id` (UUID PK)
- `source_system`
- `source_message_id`
- `idempotency_key`
- `subject`
- `sender_email`
- `received_at`
- `raw_storage_path`
- `html_storage_path`
- `office_web_link`
- `metadata`
- `created_at`

### `invoices`
- `invoice_id` (UUID PK)
- `email_id` (FK)
- `document_item_id` (nullable FK to `document_items`)
- `vendor_name`
- `invoice_number`
- `invoice_date`
- `amount`
- `currency`
- `duplicate_fingerprint`
- `metadata`
- `created_at`

Indexes:
- Normalized-expression index for `(vendor_name, invoice_number, invoice_date)` suspected duplicate checks.
- `duplicate_fingerprint`.

### `attachments`
- `attachment_id` (UUID PK)
- `email_id` (FK)
- `file_name`
- `content_type`
- `storage_path`
- `file_size_bytes`
- `sha256`
- `metadata`
- `created_at`

`attachments.metadata` stores parser metadata plus attachment analysis summaries. `metadata.pdf_evaluation` contains deterministic PyMuPDF status, page count, quality, text excerpt, `extraction_method=pymupdf_text`, and `evaluation_version=pdf_eval.v2`. `metadata.extractor_selection` contains `selected_extractor` (`pymupdf`, `document_intelligence`, or `none`), reason code, selection version `pdf_extractor_selection.v1`, and key PyMuPDF signals used for selection. `metadata.document_intelligence` contains Azure Document Intelligence status, eligibility, reason code, model ids, page count, concise text excerpt, extracted fields, field confidences, raw artifact paths, latency, errors, and `analysis_version=document_intelligence.v1` only when DI was selected or explicitly analyzed. Full Document Intelligence responses are stored as local artifacts under `local/audit/extractions/{run_id}/document-intelligence/`; no raw binary or full DI payload is stored in Postgres.

Indexes:
- Unique `(email_id, storage_path, sha256)` for idempotent attachment persistence.

### `document_items`
- `document_item_id` (UUID PK)
- `email_id` (FK)
- `item_kind` (`attachment` or `email`)
- `attachment_id` (nullable FK to `attachments`)
- `item_key` (stable per email, such as `attachment:{sha256}` or `email:body`)
- `display_name`
- `metadata`
- `created_at`

`document_items` represents independently extracted and decided business documents inside one email. The original email remains the unit of Graph action.

Indexes:
- Unique `(email_id, item_key)` for idempotent item persistence.
- Lookup indexes on `email_id` and nullable `attachment_id`.

### `extractions`
- `extraction_id` (UUID PK)
- `email_id` (FK)
- `document_item_id` (nullable FK to `document_items`)
- `extractor_type`
- `model_name`
- `prompt_version`
- `raw_output`
- `parsed_output`
- `confidence`
- `validation_status`
- `validation_errors`
- `created_at`

`extractions` stores attempts to produce the validated `extraction.v1` payload. It is not the source of truth for every LLM call.

For `extraction_batch.v1`, each validated nested `extraction.v1` item is stored as its own extraction row linked to `document_items`; the raw batch response remains available through audit artifacts and LLM interaction artifacts.

### `audit_runs`
- `run_id` (UUID PK)
- `email_id` (FK)
- `status`
- `started_at`, `completed_at`
- `final_outcome`
- `trace_artifact_path`
- `metadata`

### `decisions`
- `decision_id` (UUID PK)
- `email_id` (FK)
- `run_id` (FK)
- `document_item_id` (nullable FK to `document_items`)
- `outcome`
- `destination_code`
- `destination_email`
- `reason`
- `confidence`
- `matched_rule_code`
- `matched_rule_version`
- `extracted_fields`
- `routing_match`
- `created_at`

Batch processing stores item-level decisions with `document_item_id` and one final email-level decision with `document_item_id = null`. The final decision controls the single external action and includes item decisions under `routing_match.aggregation.item_decisions`.

### `escalate_queue`
- `escalate_id` (UUID PK)
- `email_id` (FK)
- `decision_id` (nullable FK)
- `document_item_id` (nullable FK)
- `status`
- `priority`
- `reason`
- `assigned_to`
- `created_at`, `resolved_at`
- `source_message_id`
- `office_web_link`
- `last_seen_in_escalate_at`
- `active`

Mailbox sync treats `escalate_queue` as a live folder mirror: each sync deletes existing rows, upserts related `emails` rows for messages currently in the Graph `ESCALATE` folder, and inserts one queue row for each current folder message. Rows absent after a sync mean the message is no longer currently in the `ESCALATE` folder, not that historical processing data was deleted.

### `audit_steps`
- `step_id` (UUID PK)
- `run_id` (FK)
- `sequence_number`
- `step_type` (text; audit step names are validated by specs/tests rather than a Postgres enum)
- `input_summary`
- `output_summary`
- `decision`
- `reason`
- `confidence`
- `error`
- `created_at`

`DOCUMENT_EXTRACTION_SELECTION` step output summarizes selected extractor counts and reason codes. `DOCUMENT_INTELLIGENCE` step output summarizes page-based usage for DI-selected attachments: attachment counts, model call counts, pages analyzed, per-model pages, latency, statuses, and artifact paths. Document Intelligence usage is not recorded in `llm_interactions`.

### `llm_interactions`
- `llm_interaction_id` (UUID PK)
- `email_id` (FK)
- `run_id` (FK to `audit_runs`, required)
- `step_id` (FK to `audit_steps`, nullable)
- `extraction_id` (FK to `extractions`, nullable)
- `interaction_type`
- `provider`
- `model_name`
- `deployment_name`
- `api_version`
- `prompt_template_name`
- `prompt_version`
- `prompt_artifact_path`
- `response_artifact_path`
- `request_parameters`
- `prompt_tokens`
- `completion_tokens`
- `total_tokens`
- `cached_prompt_tokens`
- `reasoning_tokens`
- `raw_usage`
- `latency_ms`
- `status`
- `error`
- `created_at`

`llm_interactions` is the per-call source of truth for LLM usage and traceability. Every LLM call must link to its audit run, should link to its ordered audit step when one exists, and must link to `extractions` when the call produced or attempted an extraction payload.

Indexes:
- `(run_id, created_at)` for run-level usage rollups.
- Partial index on `step_id` for audit-step drilldown.
- Partial index on `extraction_id` for extraction-specific lookup.

## Seed Ownership

`db/seed.sql` seeds current local `asset` and `ownership` rows exported from live local Postgres `apautomation` on 2026-05-20. It also seeds workflow rules, runtime config, destinations, no-action patterns, and workflow rule version snapshots. Fresh local databases include `appointment_informational_notice`, which routes LLM-classified appointment confirmations, reminders, and follow-ups to `NO_ACTION`, and `informational_property_notice`, which routes other eligible non-payable property notices to the matched asset's ownership-derived destination. Fresh local databases also include the split-multi-PDF and multifamily escalation destinations and active policy.

One-time `add-*.sql` and `update-*.sql` files are not part of the current baseline. Accepted SQL behavior, schema, or configuration changes must be folded back into `db/schema.sql` and/or `db/seed.sql`.
