# 100 - Workflow Management UI Spec

## Purpose

Define how the local operations app manages table-driven workflow configuration.

The management page exists so authorized local users can inspect and update workflow rules, destinations, and runtime configuration without editing SQL manually.

## Managed Data

The first local management page may manage:
- `workflow_rules`
- `workflow_rule_conditions`
- `routing_destinations`
- `runtime_config`

Future management may include:
- `properties`
- `property_aliases`
- `property_routes`
- `business_units`

Future managed areas require a spec update before implementation.

## Safety Principles

- Workflow policy remains in Postgres.
- The UI must never hard-code mutable workflow policy.
- Historical decisions, actions, audit runs, and audit steps are read-only.
- Configuration changes must be explicit and reviewable before save.
- Rule changes must preserve audit replay through effective dating and versioning.
- Invalid configuration must fail with actionable validation errors.

## Management Page Requirements

The management page must show:
- workflow rules sorted by priority
- enabled/disabled status
- rule version
- effective start and end dates
- condition type
- condition values
- outcome
- destination
- reason template
- created and updated timestamps
- recent management audit events for selected configuration records

The page must support:
- search and filters for enabled status, outcome, destination, and condition type
- viewing rule conditions as structured JSON
- editing enabled status
- editing priority
- editing outcome where allowed by deterministic evaluator constraints
- editing destination where destination is applicable
- editing reason template
- editing effective dates
- editing runtime config thresholds and local handling choices
- viewing destinations and whether they are active
- viewing management audit history for rule, destination, and runtime config changes

## Versioning Requirements

Rule edits must not overwrite historical meaning needed for audit replay.

When a rule behavior field changes, the system must create a new version or effective-dated replacement according to the database strategy defined before implementation.

Behavior fields include:
- priority
- enabled
- condition type
- condition values
- outcome
- destination
- reason template
- effective dates

The implementation must either:
- add schema support for immutable rule versions, or
- document and test how the current schema preserves replay before allowing edits.

The database must include `workflow_rule_versions` so decisions that record `matched_rule_code` and `matched_rule_version` can be replayed against the rule snapshot that existed at decision time.

Immediate rule editing is in scope. Each accepted behavior-changing edit must:
- create a `management_audit_events` row
- create a new `workflow_rule_versions` row with the resulting rule and condition snapshot
- update the current editable `workflow_rules` and `workflow_rule_conditions` rows
- increment `workflow_rules.version`

## Validation Requirements

Before saving, the API must validate:
- rule code exists or is unique for creates
- condition type is supported by deterministic code
- condition JSON matches the expected shape for the condition type
- outcome is one of the allowed decision outcomes
- destination exists and is active when required
- effective end is null or on/after effective start
- priority does not create ambiguous ordering for active overlapping rules unless explicitly allowed by spec
- runtime config values have the expected JSON type

The UI must display validation errors next to the affected field or section.

## Audit Requirements

Every configuration mutation must create a management audit record before the feature is considered complete.

The audit record must include:
- changed table
- changed key
- old value
- new value
- changed by
- changed at
- reason or note when provided

The existing `audit_runs` and `audit_steps` tables are for per-email processing traces. They must not be reused as the primary audit log for workflow management changes.

The primary management audit table is `management_audit_events`.

## Local-Only Scope

Initial management is local-only.

The app must make local mode visible and must not imply production configuration is being changed.

Production authentication, authorization, approval workflows, and deployment are out of scope until separate specs define them.

## Testing Requirements

Tests must cover:
- listing workflow rules
- listing destinations
- listing runtime config
- listing management audit events
- validation for invalid condition JSON
- validation for missing required destination
- validation for invalid effective dates
- versioning or effective-dated replacement behavior
- management audit record creation
- read-only protection for historical operational records

## Acceptance Criteria

- The management page lists workflow rules from Postgres.
- The management page lists routing destinations and runtime config from Postgres.
- Editable fields use explicit save/cancel behavior.
- Invalid edits are rejected with visible validation errors.
- Rule behavior changes preserve audit replay through versioning or effective dating.
- Every accepted configuration change records a management audit entry.
- Management audit history is visible for selected workflow configuration records.
- Historical processing records cannot be edited through the management UI or API.
- Tests cover workflow listing, validation, versioning/effective dating, and management audit logging.
