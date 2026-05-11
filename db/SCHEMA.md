# Database Schema

This is the canonical data dictionary for the local `apautomation` Postgres database.

Future schema changes must update this file in the same change as `db/schema.sql` or any seed/import script that changes table meaning.

## Design Summary

Postgres is the source of truth for:
- table-driven workflow policy
- routing destinations
- property and alias reference data
- processing state
- decisions, actions, review queue records, and audit traces
- seed/import lineage

The application code should evaluate deterministic rules, validate schemas, and enforce safety invariants. Mutable business policy belongs in Postgres rows.

Large binaries do not belong in Postgres. Store raw emails, attachments, and generated artifacts in local file storage or future Blob Storage, then keep paths and metadata in Postgres.

## Enums

### `decision_outcome`

Allowed final outcomes for each processed email.

| Value | Meaning |
| --- | --- |
| `AUTO` | Automatically route or forward when rules are confident and low risk. |
| `REVIEW` | Send to human review. This is the default safe outcome when uncertain. |
| `FILE` | Store/file without routing to Medius or a PM. |
| `FLAG` | Critical issue or misdirected item, such as a sold property. |
| `DISCARD` | Non-actionable item. Must still be logged. |

### `audit_step_type`

Allowed audit step names for the processing pipeline.

| Value | Meaning |
| --- | --- |
| `INGESTION` | Email intake and source identification. |
| `ATTACHMENT_PROCESSING` | Attachment discovery, extraction, hashing, and storage. |
| `LLM_EXTRACTION` | LLM or fixture extraction step. |
| `VALIDATION` | Schema and business input validation. |
| `DUPLICATE_CHECK` | Duplicate lookup before routing. |
| `ROUTING_MATCH` | Property, alias, bill-to, or destination matching. |
| `RULE_EVALUATION` | Deterministic workflow rule evaluation. |
| `DECISION` | Final decision creation. |
| `ACTION` | Action execution or dry-run action recording. |
| `FINALIZE` | Run completion and trace finalization. |

## Functions

### `coalesce_text(value text) returns text`

Returns an empty string for null text values.

Used by the `property_routes_unique_idx` expression index so nullable route labels and destination codes can participate in route uniqueness.

## Tables

### `seed_batches`

Records where seeded or imported data came from. Use this for lineage before trusting any reference-derived route or rule.

| Column | Type | Required | Purpose |
| --- | --- | --- | --- |
| `seed_batch_code` | `text` | Yes, primary key | Stable code for a seed/import batch, such as `core_local_v1`. |
| `description` | `text` | Yes | Human-readable explanation of what this batch inserted or updated. |
| `source_file` | `text` | No | Source document, workbook, spec, or script used for the batch. |
| `source_hash` | `text` | No | Optional hash of the source file when reproducible source tracking is needed. |
| `imported_by` | `text` | Yes | Database user or process that imported the batch. Defaults to `current_user`. |
| `imported_at` | `timestamptz` | Yes | Import timestamp. Defaults to `now()`. |
| `metadata` | `jsonb` | Yes | Extra lineage details, such as environment, import mode, or source interpretation notes. |

### `runtime_config`

Stores environment and threshold configuration that should not be hard-coded.

| Column | Type | Required | Purpose |
| --- | --- | --- | --- |
| `config_key` | `text` | Yes, primary key | Stable config key, such as `dry_run` or `confidence_threshold`. |
| `config_value` | `jsonb` | Yes | Config value stored as JSON so booleans, numbers, and strings preserve type. |
| `description` | `text` | Yes | Explanation of how the config is used. |
| `seed_batch_code` | `text` | No, FK | Seed/import batch that created or last defined the row. |
| `created_at` | `timestamptz` | Yes | Row creation timestamp. |
| `updated_at` | `timestamptz` | Yes | Last update timestamp. |

Current important keys:
- `app_env`: local environment marker.
- `dry_run`: default external side-effect guard.
- `confidence_threshold`: minimum confidence for automatic routing.
- `amount_review_threshold`: invoice amount threshold for filing high-dollar invoices to lien release hold.
- `statement_outcome`: default statement handling.
- `default_review_destination`: review destination code.

### `routing_destinations`

Defines all places the system may route, file, or queue items. Decisions and actions reference these rows instead of embedding destination emails in code.

| Column | Type | Required | Purpose |
| --- | --- | --- | --- |
| `destination_code` | `text` | Yes, primary key | Stable destination code, such as `MEDIUS_PROP` or `PM_TIFFANY_BECK_NUVEEN`. |
| `destination_type` | `text` | Yes | Destination category. Current values include `email`, `folder`, `review_queue`, and `no_action`. |
| `display_name` | `text` | Yes | Human-readable destination name. |
| `email_address` | `text` | No | Email target for forwarding/routing destinations. |
| `folder_path` | `text` | No | Local or future storage path for file-only outcomes. |
| `subject_instruction` | `text` | No | Business instruction for subject line modifications, such as Nuveen or Lex notes. |
| `active` | `boolean` | Yes | Whether the destination is currently usable. |
| `seed_batch_code` | `text` | No, FK | Seed/import batch that created or last defined the row. |
| `created_at` | `timestamptz` | Yes | Row creation timestamp. |
| `updated_at` | `timestamptz` | Yes | Last update timestamp. |

Constraint: each destination must have an email address, folder path, or be a logical `review_queue`/`no_action` destination.

### `business_units`

Defines business groups that can drive bill-to routing, such as Properties, ALC, and Multifamily.

| Column | Type | Required | Purpose |
| --- | --- | --- | --- |
| `business_unit_code` | `text` | Yes, primary key | Stable business unit code, such as `PROP`, `ALC`, or `MF`. |
| `name` | `text` | Yes | Human-readable business unit name. |
| `category` | `text` | Yes | Classification used by rules and UI filters. |
| `cost_center` | `text` | No | Optional accounting cost center. |
| `default_destination_code` | `text` | No, FK | Default destination for this business unit. |
| `active` | `boolean` | Yes | Whether this business unit is active. |
| `seed_batch_code` | `text` | No, FK | Seed/import batch that created or last defined the row. |
| `created_at` | `timestamptz` | Yes | Row creation timestamp. |
| `updated_at` | `timestamptz` | Yes | Last update timestamp. |

### `properties`

Canonical property/building records used for routing. Property rows are derived from the Medius routing workbook and later resolved to destinations.

| Column | Type | Required | Purpose |
| --- | --- | --- | --- |
| `property_id` | `uuid` | Yes, primary key | Internal immutable property identifier. |
| `property_code` | `text` | Yes, unique | Business property/building code, such as `ACC1`, `GW52`, or `6611`. |
| `property_name` | `text` | No | Best known property, tenant, or portfolio name from reference data. |
| `cost_center` | `text` | No | Accounting cost center when available. |
| `ownership_type` | `text` | Yes | Ownership classification, such as `hillwood_owned`, `investor_managed`, `sold`, or `unknown`. |
| `management_type` | `text` | Yes | Management classification, such as `internal`, `external_pm`, `sold`, or `unknown`. |
| `business_unit_code` | `text` | No, FK | Related business unit, such as `PROP` or `MF`. |
| `default_destination_code` | `text` | No, FK | Resolved default destination. This should be populated before automatic routing. |
| `is_sold` | `boolean` | Yes | Sold property flag. Sold properties should hit hard exception handling before normal routing. |
| `active` | `boolean` | Yes | Whether the property is currently active for matching. |
| `seed_batch_code` | `text` | No, FK | Seed/import batch that created or last defined the row. |
| `created_at` | `timestamptz` | Yes | Row creation timestamp. |
| `updated_at` | `timestamptz` | Yes | Last update timestamp. |

### `property_aliases`

Searchable aliases for matching extracted invoice data to properties.

| Column | Type | Required | Purpose |
| --- | --- | --- | --- |
| `alias_id` | `uuid` | Yes, primary key | Internal alias identifier. |
| `property_id` | `uuid` | Yes, FK | Property this alias identifies. Cascades on property delete. |
| `alias_type` | `text` | Yes | Alias kind, such as `tenant`, `address`, `property_name`, or `legal_entity`. |
| `alias_value` | `text` | Yes | Raw alias value to match against extracted invoice fields. |
| `source_sheet` | `text` | No | Workbook sheet where the alias came from. |
| `source_row` | `integer` | No | Workbook row where the alias came from. |
| `seed_batch_code` | `text` | No, FK | Seed/import batch that created the row. |
| `created_at` | `timestamptz` | Yes | Row creation timestamp. |

Uniqueness: `(alias_type, alias_value)` is unique.

Index: `property_aliases_value_lower_idx` supports case-insensitive alias lookup.

### `property_routes`

Resolved route rows for properties. The decision engine should rely on `destination_code`, not on raw labels.

| Column | Type | Required | Purpose |
| --- | --- | --- | --- |
| `property_route_id` | `uuid` | Yes, primary key | Internal route identifier. |
| `property_id` | `uuid` | Yes, FK | Property this route belongs to. Cascades on property delete. |
| `destination_code` | `text` | No, FK | Final resolved destination. Must be non-null before a route can support `AUTO`. |
| `route_label` | `text` | No | Original or normalized route label, such as `Caroline`, `resolved_default`, or `SOLD`. |
| `subject_instruction` | `text` | No | Subject-line instruction for this property route. |
| `source_sheet` | `text` | No | Workbook sheet or resolution source that created the route. |
| `source_row` | `integer` | No | Workbook row that created the route, when applicable. |
| `seed_batch_code` | `text` | No, FK | Seed/import or route-resolution batch. |
| `active` | `boolean` | Yes | Whether this route is usable. |
| `created_at` | `timestamptz` | Yes | Row creation timestamp. |
| `updated_at` | `timestamptz` | Yes | Last update timestamp. |

Index: `property_routes_unique_idx` prevents duplicate routes per property/label/destination combination.

### `reference_rows`

Raw workbook rows retained for auditability and reprocessing. This table intentionally keeps source data even when normalized tables derive cleaner rows from it.

| Column | Type | Required | Purpose |
| --- | --- | --- | --- |
| `reference_row_id` | `uuid` | Yes, primary key | Internal reference row identifier. |
| `source_file` | `text` | Yes | Source workbook or document name. |
| `source_sheet` | `text` | Yes | Source worksheet name. |
| `source_row` | `integer` | Yes | Source row number. |
| `row_data` | `jsonb` | Yes | Raw row content keyed by imported headers. |
| `seed_batch_code` | `text` | No, FK | Seed/import batch that loaded the row. |
| `imported_at` | `timestamptz` | Yes | Last import timestamp. |

Uniqueness: `(source_file, source_sheet, source_row)` is unique.

### `workflow_rules`

Top-level deterministic workflow rules. These rows define priority, enabled status, outcomes, reason templates, and rule versions.

| Column | Type | Required | Purpose |
| --- | --- | --- | --- |
| `rule_code` | `text` | Yes, primary key | Stable rule code used by decisions and tests. |
| `rule_name` | `text` | Yes | Human-readable rule name. |
| `priority` | `integer` | Yes | Evaluation order. Lower numbers run first. |
| `enabled` | `boolean` | Yes | Whether the rule is active. |
| `condition_type` | `text` | Yes | Condition evaluator type implemented by deterministic code. |
| `outcome` | `decision_outcome` | Yes | Outcome if this rule matches. |
| `destination_code` | `text` | No, FK | Destination for rules with fixed destinations. Property routing can leave this null and use matched property route destination. |
| `reason_template` | `text` | Yes | Human-readable reason template for decisions and audit records. |
| `effective_start` | `date` | Yes | First date this rule version is active. |
| `effective_end` | `date` | No | Last date this rule version is active. Null means open-ended. |
| `version` | `integer` | Yes | Rule version recorded on decisions for replay. |
| `seed_batch_code` | `text` | No, FK | Seed/import batch that created or last defined the row. |
| `created_at` | `timestamptz` | Yes | Row creation timestamp. |
| `updated_at` | `timestamptz` | Yes | Last update timestamp. |

Constraint: `effective_end` must be null or on/after `effective_start`.

Index: `workflow_rules_active_idx` supports active rule lookup by enabled status, priority, and effective dates.

### `workflow_rule_conditions`

Condition payloads for workflow rules. These rows make rule values table-driven while code owns supported evaluator types.

| Column | Type | Required | Purpose |
| --- | --- | --- | --- |
| `condition_id` | `uuid` | Yes, primary key | Internal condition identifier. |
| `rule_code` | `text` | Yes, FK | Rule this condition belongs to. Cascades on rule delete. |
| `condition_key` | `text` | Yes | Named condition parameter, such as `document_types` or `runtime_config_key`. |
| `condition_value` | `jsonb` | Yes | JSON condition value used by deterministic rule evaluation. |
| `created_at` | `timestamptz` | Yes | Row creation timestamp. |

Uniqueness: `(rule_code, condition_key)` is unique.

### `management_audit_events`

Audit log for workflow management and configuration changes made through the local management UI or API.

This table is separate from `audit_runs` and `audit_steps`. Those tables record per-email processing traces. `management_audit_events` records configuration mutations such as workflow rule edits, runtime config changes, and destination changes.

| Column | Type | Required | Purpose |
| --- | --- | --- | --- |
| `management_audit_event_id` | `uuid` | Yes, primary key | Internal audit event identifier. |
| `changed_table` | `text` | Yes | Table changed by the management operation, such as `workflow_rules` or `runtime_config`. |
| `changed_key` | `text` | Yes | Stable key for the changed row or logical configuration item. |
| `change_type` | `text` | Yes | Mutation type, such as `create`, `update`, `disable`, or `version`. |
| `old_value` | `jsonb` | No | Snapshot before the change. Null is allowed for creates. |
| `new_value` | `jsonb` | Yes | Snapshot after the change. |
| `changed_by` | `text` | Yes | Local user or service identity that made the change. Defaults to `current_user`. |
| `changed_at` | `timestamptz` | Yes | Change timestamp. Defaults to `now()`. |
| `reason` | `text` | No | Human-provided reason or note for the change. |
| `request_metadata` | `jsonb` | Yes | Request context such as local app mode, client identifier, or validation summary. |

Index: `management_audit_events_lookup_idx` supports lookup by changed table/key and newest changes first.

### `workflow_rule_versions`

Immutable snapshots of workflow rule behavior used to preserve audit replay when rules are edited.

The current `workflow_rules` row represents the editable current rule record. Each behavior-changing edit must also create a `workflow_rule_versions` snapshot with the resulting rule fields and condition payloads. Decisions record `matched_rule_code` and `matched_rule_version`, which can be matched to this table for replay.

| Column | Type | Required | Purpose |
| --- | --- | --- | --- |
| `rule_version_id` | `uuid` | Yes, primary key | Internal rule version identifier. |
| `rule_code` | `text` | Yes, FK | Stable rule code this version belongs to. |
| `version` | `integer` | Yes | Rule version number. Unique per `rule_code`. |
| `rule_name` | `text` | Yes | Rule name at this version. |
| `priority` | `integer` | Yes | Evaluation priority at this version. |
| `enabled` | `boolean` | Yes | Enabled status at this version. |
| `condition_type` | `text` | Yes | Deterministic evaluator type at this version. |
| `condition_snapshot` | `jsonb` | Yes | Snapshot of condition keys and values at this version. |
| `outcome` | `decision_outcome` | Yes | Outcome at this version. |
| `destination_code` | `text` | No, FK | Destination at this version when applicable. |
| `reason_template` | `text` | Yes | Reason template at this version. |
| `effective_start` | `date` | Yes | First date this rule version is effective. |
| `effective_end` | `date` | No | Last date this rule version is effective. Null means open-ended. |
| `management_audit_event_id` | `uuid` | No, FK | Management audit event that created this version snapshot. |
| `created_at` | `timestamptz` | Yes | Snapshot creation timestamp. |

Constraints:
- `(rule_code, version)` is unique.
- `effective_end` must be null or on/after `effective_start`.

Index: `workflow_rule_versions_lookup_idx` supports decision replay by `rule_code` and `version`.

### `emails`

Operational record for each ingested email.

| Column | Type | Required | Purpose |
| --- | --- | --- | --- |
| `email_id` | `uuid` | Yes, primary key | Internal email identifier. |
| `source_system` | `text` | Yes | Source type, such as `local_file` now or Microsoft Graph later. |
| `source_message_id` | `text` | Yes | Source-specific message identifier. |
| `idempotency_key` | `text` | Yes, unique | Stable key used to avoid duplicate processing/actions. |
| `subject` | `text` | No | Email subject. |
| `sender_email` | `text` | No | Sender email address. |
| `received_at` | `timestamptz` | No | Source received timestamp. |
| `raw_storage_path` | `text` | No | Path to raw email artifact outside Postgres. |
| `html_storage_path` | `text` | No | Path to sanitized HTML preview generated from a local `.msg` or future mailbox source. |
| `metadata` | `jsonb` | Yes | Extra source metadata. Avoid raw PII unless needed. |
| `created_at` | `timestamptz` | Yes | Row creation timestamp. |

### `attachments`

Metadata for email attachments. Files are stored outside Postgres.

| Column | Type | Required | Purpose |
| --- | --- | --- | --- |
| `attachment_id` | `uuid` | Yes, primary key | Internal attachment identifier. |
| `email_id` | `uuid` | Yes, FK | Email this attachment belongs to. Cascades on email delete. |
| `file_name` | `text` | Yes | Original or normalized file name. |
| `content_type` | `text` | No | MIME type when known. |
| `storage_path` | `text` | Yes | Path to local or future blob artifact. |
| `file_size_bytes` | `bigint` | No | Attachment size in bytes. |
| `sha256` | `text` | No | Attachment hash for dedupe and integrity checks. |
| `metadata` | `jsonb` | Yes | Extra attachment metadata, such as page count or PDF analysis flags. |
| `created_at` | `timestamptz` | Yes | Row creation timestamp. |

Index: `attachments_email_path_hash_idx` prevents duplicate attachment metadata for the same email, storage path, and content hash during local reprocessing.

### `extractions`

Structured extraction results from an LLM, local fixture, or future extraction service.

| Column | Type | Required | Purpose |
| --- | --- | --- | --- |
| `extraction_id` | `uuid` | Yes, primary key | Internal extraction identifier. |
| `email_id` | `uuid` | Yes, FK | Email this extraction belongs to. Cascades on email delete. |
| `extractor_type` | `text` | Yes | Extraction source, such as `fixture`, `codex_cli`, or `llm`. |
| `model_name` | `text` | No | LLM model name when applicable. |
| `prompt_version` | `text` | No | Prompt/template version when applicable. |
| `raw_output` | `jsonb` | No | Raw extractor audit payload when safe and useful to store. For Codex local runs this includes rendered LLM input, raw LLM output, parsed JSON, model, and prompt version. |
| `parsed_output` | `jsonb` | Yes | Structured extraction payload used by deterministic logic, or the invalid parsed payload when validation fails. |
| `confidence` | `numeric(5,4)` | No | Overall extraction confidence, usually 0.0000 to 1.0000. |
| `validation_status` | `text` | Yes | Validation result, such as `valid` or `invalid`. |
| `validation_errors` | `jsonb` | Yes | Validation error list. Empty array means no errors. |
| `created_at` | `timestamptz` | Yes | Row creation timestamp. |

### `audit_runs`

One processing attempt for an email. A reprocess creates a new run while preserving prior history.

| Column | Type | Required | Purpose |
| --- | --- | --- | --- |
| `run_id` | `uuid` | Yes, primary key | Internal audit run identifier. |
| `email_id` | `uuid` | No, FK | Email being processed. Nullable to allow failed ingestion runs if needed. |
| `status` | `text` | Yes | Run status, such as `started`, `completed`, or `failed`. Failed local runs must be marked `failed` and must not remain `started` once the processor catches the failure. |
| `started_at` | `timestamptz` | Yes | Run start timestamp. |
| `completed_at` | `timestamptz` | No | Run completion timestamp. |
| `final_outcome` | `decision_outcome` | No | Final outcome once decision is complete. |
| `trace_artifact_path` | `text` | No | Path to generated visual trace artifact, such as Mermaid text. |
| `metadata` | `jsonb` | Yes | Extra run metadata. Failed runs may include an `error` field with the failure message. |

### `audit_steps`

Ordered audit trail for a run. Every major pipeline step must be represented.

| Column | Type | Required | Purpose |
| --- | --- | --- | --- |
| `step_id` | `uuid` | Yes, primary key | Internal audit step identifier. |
| `run_id` | `uuid` | Yes, FK | Audit run this step belongs to. Cascades on run delete. |
| `sequence_number` | `integer` | Yes | Step order within the run. |
| `step_type` | `audit_step_type` | Yes | Pipeline step type. |
| `input_summary` | `jsonb` | Yes | Step inputs. Local LLM extraction steps include the rendered prompt and prompt artifact path. |
| `output_summary` | `jsonb` | Yes | Step outputs. Local LLM extraction steps include the raw LLM response and extraction artifact path. |
| `decision` | `jsonb` | No | Decision data if this step produced or evaluated a decision. |
| `reason` | `text` | No | Human-readable reason, rule note, or validation explanation. |
| `confidence` | `numeric(5,4)` | No | Confidence value relevant to the step. |
| `error` | `text` | No | Explicit error if the step failed. |
| `created_at` | `timestamptz` | Yes | Step timestamp. |

Uniqueness: `(run_id, sequence_number)` is unique.

### `decisions`

Final decision records. Every processed email should produce exactly one final decision per completed run.

| Column | Type | Required | Purpose |
| --- | --- | --- | --- |
| `decision_id` | `uuid` | Yes, primary key | Internal decision identifier. |
| `email_id` | `uuid` | Yes, FK | Email this decision belongs to. Cascades on email delete. |
| `run_id` | `uuid` | No, FK | Audit run that produced the decision. |
| `outcome` | `decision_outcome` | Yes | Final allowed outcome. |
| `destination_code` | `text` | No, FK | Final destination code when applicable. |
| `destination_email` | `text` | No | Resolved email address snapshot, if the destination is email-based. |
| `reason` | `text` | Yes | Human-readable decision reason. |
| `confidence` | `numeric(5,4)` | No | Confidence used or produced by the decision. |
| `matched_rule_code` | `text` | No, FK | Workflow rule that produced the decision. |
| `matched_rule_version` | `integer` | No | Version of the matched workflow rule at decision time. |
| `extracted_fields` | `jsonb` | Yes | Snapshot of extracted fields used by the decision. |
| `routing_match` | `jsonb` | Yes | Snapshot of property, alias, route, or destination match details. |
| `dry_run` | `boolean` | Yes | Whether external side effects were disabled. Defaults to true. |
| `created_at` | `timestamptz` | Yes | Decision timestamp. |

### `actions`

Records intended or executed external actions. In local mode these are dry-run action records, not live mutations.

| Column | Type | Required | Purpose |
| --- | --- | --- | --- |
| `action_id` | `uuid` | Yes, primary key | Internal action identifier. |
| `email_id` | `uuid` | Yes, FK | Email this action belongs to. Cascades on email delete. |
| `decision_id` | `uuid` | No, FK | Decision that authorized this action. |
| `action_type` | `text` | Yes | Action category, such as `forward_email`, `file_email`, `queue_review`, or `no_action`. |
| `destination_code` | `text` | No, FK | Destination used for the action. |
| `dry_run` | `boolean` | Yes | Whether the action was suppressed from external mutation. |
| `status` | `text` | Yes | Action status, such as `planned`, `skipped_dry_run`, `completed`, or `failed`. |
| `external_reference` | `text` | No | External system id if an action eventually mutates a live system. |
| `reason` | `text` | No | Human-readable action reason or skip explanation. |
| `created_at` | `timestamptz` | Yes | Action creation timestamp. |
| `completed_at` | `timestamptz` | No | Action completion timestamp. |

### `review_queue`

Human review worklist for decisions requiring manual handling.

| Column | Type | Required | Purpose |
| --- | --- | --- | --- |
| `review_id` | `uuid` | Yes, primary key | Internal review item identifier. |
| `email_id` | `uuid` | Yes, FK | Email requiring review. Cascades on email delete. |
| `decision_id` | `uuid` | No, FK | Decision that created the review item. |
| `status` | `text` | Yes | Review status, such as `open`, `in_progress`, `resolved`, or `closed`. |
| `priority` | `text` | Yes | Review priority, such as `normal`, `high`, or future SLA-based values. |
| `reason` | `text` | Yes | Human-readable reason the item needs review. |
| `assigned_to` | `text` | No | User or queue assignment. |
| `created_at` | `timestamptz` | Yes | Review item creation timestamp. |
| `resolved_at` | `timestamptz` | No | Resolution timestamp. |

## Seed and Import Files

### `db/seed.sql`

Creates core local configuration:
- routing destinations
- business units
- runtime config
- workflow rules
- workflow rule conditions
- initial workflow rule version snapshots

The default high-dollar invoice policy files invoices over `amount_review_threshold` to `FOLDER_LIEN_RELEASE` for lien release hold.

This file should contain stable local defaults and must remain idempotent.

### `db/import-reference-workbook.ps1`

Imports `reference/Medius Routing.xlsx`.

Outputs:
- raw rows into `reference_rows`
- normalized rows into `properties`
- searchable aliases into `property_aliases`
- initial route hints into `property_routes`

The importer intentionally preserves raw reference rows even when only part of a row is normalized.

### `db/resolve-property-routes.sql`

Resolves imported route hints and reference-deck interpretation into final route destinations.

Outputs:
- `properties.default_destination_code`
- `properties.ownership_type`
- `properties.management_type`
- `properties.is_sold`
- `property_routes.destination_code`
- route subject instructions where known

After this script runs, `property_routes.destination_code` should be non-null for all active route rows.

## Maintenance Rules

When changing schema:
- Update this file for every table and column change.
- Update `db/README.md` if setup commands or execution order changes.
- Update specs if behavior or data strategy changes.
- Add or update tests once project tests exist.
- Prefer additive migrations once production data exists.
- Preserve historical rule and decision meaning for audit replay.
