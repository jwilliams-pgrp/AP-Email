# 040 - Postgres Data Strategy Spec

## Purpose

Define how Postgres acts as the local source of truth for workflow policy, routing data, processing state, and audit history.

The initial project is local-first, but the schema should avoid choices that block future production use.

## Data Strategy Principles

- Workflow policy must be table driven.
- Business-maintained values must live in data, not code.
- Code may define supported rule types and safety invariants.
- Seed data must be deterministic and repeatable.
- Reference-derived data must record source lineage.
- Tables must support future business management through an app.
- Production enablement must not require replacing the local data model.

## Data Categories

### Operational Records

Operational records describe processed work:
- emails
- attachments
- extracted fields
- decisions
- actions
- review queue entries
- execution runs
- audit steps

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

### Reference Data

Reference data supports matching:
- properties
- ownership or management classification
- property aliases
- vendors
- bill-to aliases
- sold property indicators
- business units such as ALC and Multifamily

### Lineage and Versioning

Lineage records where configuration came from:
- seed batch
- source file
- source worksheet or slide, when applicable
- imported_at
- imported_by
- active version

## Local Seeding Requirements

- Local seed scripts must be idempotent.
- Seeds must be safe to rerun.
- Seeded rows must include stable natural keys or explicit codes.
- Seeded workflow rules must include enabled status and effective dates.
- Seeded rows must record whether they are sample data or business-derived data.
- Seed logic must not depend on production systems.

## Future Business App Requirements

The schema should support a future app that can:
- view workflow rules
- enable or disable rules
- edit routing destinations
- edit thresholds
- manage aliases
- review effective dates
- see who changed configuration
- preserve historical rule versions for audit replay

The app itself is out of scope for the initial local build.

## Minimum Initial Tables

The first schema should include:
- `emails`
- `attachments`
- `extractions`
- `decisions`
- `actions`
- `review_queue`
- `audit_runs`
- `audit_steps`
- `workflow_rules`
- `workflow_rule_versions`
- `workflow_rule_conditions`
- `management_audit_events`
- `routing_destinations`
- `properties`
- `property_aliases`
- `business_units`
- `runtime_config`
- `seed_batches`

## Production Readiness Constraints

- Use UUID primary keys for operational records.
- Use stable text codes for business-managed configuration rows.
- Preserve created and updated timestamps.
- Preserve effective dating for workflow rules.
- Never delete configuration rows that may be needed for audit replay; disable or supersede them instead.
- Store binary files outside Postgres and keep only metadata plus storage paths in Postgres.

## Acceptance Criteria

- The schema can represent routing without hard-coded destinations.
- The schema can represent thresholds without hard-coded constants.
- The schema can represent enabled and disabled rules.
- The schema can preserve historical rule versions for audit replay.
- Local seed data can be rerun without duplicating rows.
- Local seed data can populate enough rules to run all golden decision scenarios.
- Each decision can reference the workflow rule version used.
- Workflow rule edits can be audited with before and after values.
- Workflow rule version snapshots can preserve replay after local management edits.
- Each seed batch records source lineage.
