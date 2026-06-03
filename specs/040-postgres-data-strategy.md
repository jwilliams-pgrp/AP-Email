# 040 - Postgres Data Strategy Spec

## Purpose

Define how Postgres acts as the local source of truth for workflow policy, routing data, processing state, and audit history.

## Data Strategy Principles

- Workflow policy must be table driven.
- Business-maintained values must live in data, not code.
- Code may define supported rule types and safety invariants.
- Seed data must be deterministic and repeatable.
- `db/schema.sql` and `db/seed.sql` are the complete replayable database baseline for local and Azure nonprod setup.
- Reference-derived data must record source lineage.
- Tables must support future business management through an app.
- Production enablement must not require replacing the local data model.

## Property Matching Strategy

- `asset` is the canonical asset identity and matching table for runtime routing.
- `ownership` maps asset ownership classifications to routing destinations.
- `asset_alias` and `asset_name` are canonical property code/name equivalents and must be preserved.
- `asset_type` stores asset classification such as `Multifamily`.
- Active fuzzy SQL queries `asset` identity, tenant, and address signal columns directly.
- App code standardizes extracted inputs before SQL retrieval.
- Deterministic decision gate remains authoritative for final route selection.

## Data Categories

### Operational Records

Operational records describe processed work:
- emails
- attachments
- invoices
- extracted fields
- LLM interactions and usage metrics
- decisions
- actions
- ESCALATE queue entries
- execution runs
- audit steps

Graph-ingested emails store the Microsoft Office web link when Graph provides it so ESCALATEers can open the original message from local ESCALATE notifications and dashboard views.

### Workflow Configuration

Workflow configuration controls behavior:
- workflow rules
- workflow rule versions
- management audit events
- rule conditions
- routing destinations
- confidence thresholds
- amount thresholds
- statement handling policy
- duplicate detection policy
- runtime config

Routing destinations may opt in to Teams notifications with `send_teams_message`. When enabled for the matched destination, the local processor posts a ESCALATE notification to the configured Teams channel after the decision and action plan are persisted. The notification message must be HTML-formatted for Rapid7 webhook rendering, include email subject, routing path, and an Outlook link when available, and escape dynamic values before constructing markup.

Routing destinations may opt in to outbound email handling with `send_email`. When disabled, `email_address` is reference data only and must not be exposed as the decision `destination_email`.

### Reference Data

Reference data supports matching:
- assets
- ownership classification
- vendors
- bill-to aliases
- business units such as ALC and Multifamily

Seeded asset data includes business fields used for routing (`asset_alias`, `asset_name`, `ownership`, `asset_type`, tenant/address signals, and ownership-derived destinations).

## Local Seeding Requirements

- `db/schema.sql` must remain the complete replayable DDL baseline.
- `db/seed.sql` must remain the complete replayable configuration and reference-data baseline.
- One-time targeted SQL files may be used only to apply narrowly scoped accepted changes to existing databases, and the accepted final state must be folded back into the baseline files.
- Local seed scripts must be idempotent.
- Seeds must be safe to rerun.
- Seeded rows must include stable natural keys or explicit source keys that tolerate duplicate `property_code` values.
- Seeded workflow rules must include enabled status and effective dates.
- Seeded rows must record whether they are sample data or business-derived data.
- Seed logic must not depend on production systems.

## Minimum Initial Tables

The first schema should include:
- `emails`
- `attachments`
- `invoices`
- `extractions`
- `llm_interactions`
- `decisions`
- `actions`
- `escalate_queue`
- `audit_runs`
- `audit_steps`
- `workflow_rules`
- `workflow_rule_versions`
- `workflow_rule_conditions`
- `management_audit_events`
- `routing_destinations`
- `asset`
- `ownership`
- `business_units`
- `runtime_config`

## Acceptance Criteria

- The schema can represent routing without hard-coded destinations.
- The schema can store an Office web link for a processed Graph email.
- The schema can represent per-destination Teams notification opt-in without hard-coded destinations.
- Teams notification payloads use formatted HTML message content with escaped dynamic values and an Outlook link when available.
- The schema can represent per-destination outbound email opt-in without hard-coded destinations.
- The schema can represent thresholds without hard-coded constants.
- The schema can represent enabled and disabled rules.
- The schema can preserve historical rule versions for audit replay.
- Local seed data can be rerun without duplicating rows.
- Local seed data can populate enough rules to run all golden decision scenarios.
- Each decision can reference the workflow rule version used.
- Each LLM call can be traced to its audit run, audit step, and extraction record when the call produced or attempted an extraction.
- Each LLM call stores token usage metrics when available from the provider.
- Workflow rule edits can be audited with before and after values.
- Workflow rule version snapshots can preserve replay after local management edits.
- Fuzzy top-N retrieval runs directly over `asset` identity, tenant, and address columns with trigram-backed indexes.
- The local `asset` and `ownership` seed rows match the exported live local Postgres baseline.
