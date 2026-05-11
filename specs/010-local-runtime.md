# 010 - Local Runtime Spec

## Purpose

The system must run locally before being deployed to production infrastructure.

## Default Runtime

- `APP_ENV=LOCAL`
- `DRY_RUN=true`
- Local Postgres on the developer machine
- Local filesystem artifact storage
- Production Microsoft Graph, Blob Storage, and Postgres disabled

## Local Requirements

- Local Postgres must be available through Docker, a local service, or a documented local connection string.
- When `APP_ENV=LOCAL` and no `DATABASE_URL` is supplied, the CLI should default to the documented local Postgres database.
- Production mode must use an explicitly configured Azure-hosted Postgres connection with Azure identity and RBAC.
- Local folders must exist for ingest, processed artifacts, outbound dry-run actions, and audit artifacts.
- Extraction may be represented by local fixture output in tests or by `codex exec` through the CLI for local `.msg` processing.
- The CLI must support processing a folder of saved local emails in deterministic sorted order so reference test emails can be run without ad hoc shell loops.
- The CLI must expose a documented way to pass `--skip-git-repo-check` to `codex exec` for local workspaces that are not Git repositories.
- Microsoft Graph must be disabled or read-only by default.
- External side effects require explicit production configuration and must not be reachable by default.

## Prohibited in Local Dry Run

- Forwarding emails.
- Moving emails in live mailboxes.
- Deleting emails.
- Filing emails in live folders.
- Writing to production Blob Storage.
- Writing to production Postgres.
- Sending notifications to production recipients.

## Local Data Requirements

- Local Postgres must contain seed data for workflow rules, routing destinations, thresholds, and runtime config.
- Seed data must be deterministic and repeatable.
- Seed data must distinguish between sample/test rows and business-approved rows.
- Local seed data may be derived from reference materials, but source lineage must be recorded.

## Acceptance Criteria

- A saved sample email can be processed locally.
- Attachments are stored in local artifact storage.
- Workflow rules are read from local Postgres, not hard-coded.
- A decision is written to local Postgres.
- Audit run and audit step records are written to local Postgres.
- Dry-run action records are created without mutating external systems.
- No external mailbox mutation occurs.
- Reprocessing the same sample email does not create duplicate external actions.
- A folder of saved `.msg` test emails can be processed with one documented local command.
