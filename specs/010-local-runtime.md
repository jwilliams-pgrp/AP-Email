# 010 - Local Runtime Spec

## Purpose

The system must support local development and testing while also supporting Azure hosted runtime under `specs/110-azure-hosted-runtime.md`.

## Default Runtime

- `APP_ENV=LOCAL`
- Local Postgres on the developer machine
- Local filesystem artifact storage
- Production Blob Storage and production Postgres disabled unless `APP_ENV=AZURE`

## Local Requirements

- Local Postgres must be available through Docker, a local service, or a documented local connection string.
- When `APP_ENV=LOCAL` and no `DATABASE_URL` is supplied, the CLI should default to the documented local Postgres database.
- Azure mode must use an explicitly configured Azure-hosted Postgres connection with Azure identity and RBAC where supported.
- Local folders must exist for attachment artifacts and audit artifacts.
- Extraction may be represented by local fixture output in tests or by Azure OpenAI Foundry for local `.msg` processing.
- The CLI must support processing from Microsoft Graph Intake in local mode using environment-configured mailbox identity and the mailbox Inbox as the fixed intake folder.
- Local operations must support iterating Graph Intake emails until the folder is empty via one documented local command.
- Graph Intake processing must claim exactly one email per run by selecting the oldest Inbox message with `receivedDateTime asc`, moving it to an existing shared mailbox folder with display name `processing`, and processing only the moved mailbox item.
- Graph Intake processing must use the post-claim Graph message id and Office web link for extraction metadata, audit records, and final mailbox actions.
- The CLI must read Azure OpenAI configuration from `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_VERSION`, and `AZURE_OPENAI_DEPLOYMENT`, with equivalent CLI overrides. API-key authentication is allowed only for local development when identity is unavailable.
- Microsoft Graph mailbox mutations (category/move) run for Graph Intake processing when the destination has a configured `parent_folder`.
- For Graph Intake folder destinations, the runtime must resolve mailbox folder IDs from configured `parent_folder` values during processing; destination IDs must not be hard-coded in application logic.
- For Graph Intake messages that are moved, final routing must move the claimed item from `processing` to the configured destination folder and the persisted `office_web_link` and ESCALATE notifications must use the message link after the final move when Graph returns one.
- For Graph Intake messages with a configured label and destination folder, final routing must apply the label to the claimed message before moving it to the destination folder. This avoids relying on immediate post-move mailbox consistency for category updates.
- Graph Intake action-stage failures after the final decision must not replay ingestion, extraction, validation, routing, or decision creation. They must be audited as action failures on the same audit run.
- Local and development runtime must not forward routed emails to destination recipients, even when `routing_destinations.send_email` is true. Outbound email forwarding requires Azure runtime and explicit `AP_ENABLE_OUTBOUND_EMAIL_FORWARDING=true`.
- If Graph Intake processing fails after a message is claimed, the runtime must attempt to move the claimed item to the existing `ESCALATE` folder and must report any failure of that recovery move explicitly.
- Runtime behavior must not branch into a separate non-mutating execution mode.
- Production side effects outside the configured Graph mailbox require explicit production configuration and must not be reachable by default.
- `APP_ENV=AZURE` must preserve the same decision pipeline and business outcomes as `LOCAL`.

## Prohibited Local Side Effects

- Deleting emails.
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
- Action records capture mailbox routing attempts and results.
- Action audit records capture Graph category and move substeps, including failures, without recording raw email body or attachment contents.
- Reprocessing the same sample email does not create duplicate actions.
- A Graph Intake email can be processed with one documented local command.
- Graph Intake processing can iterate through all available intake emails with one documented local command.
- Graph Intake processing reads the oldest Inbox message without requiring a configured intake folder id, claims it by moving it to `processing`, and fetches full body and attachments only from the moved message id.
- Graph Intake routing resolves folder IDs at runtime from configured destination names/path hints before move.
- Graph Intake routing persists and notifies with the post-move Office web link when Graph returns one.
- Graph Intake routing does not forward outbound email in `LOCAL` or development configuration when `AP_ENABLE_OUTBOUND_EMAIL_FORWARDING` is absent or false.
- Graph Intake idempotency uses `internetMessageId` when Graph supplies it, and falls back to the claimed Graph message id only when no `internetMessageId` is available.
- Local runtime does not require committed secrets or hard-coded passwords.
- Azure runtime is selected only with `APP_ENV=AZURE` and explicit Azure configuration.
