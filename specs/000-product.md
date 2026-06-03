# 000 - Product Spec: AP Processing Agent

## Purpose

Automate AP inbox processing for Hillwood Properties by classifying emails, extracting invoice data, applying deterministic routing rules, and logging every decision.

## Goals

- Reduce manual invoice routing for clean, low-risk invoices.
- Route clean invoices automatically when deterministic rules allow it.
- Escalate exceptions with clear, ESCALATEable reasons.
- Maintain a permanent audit trail for every processed email.
- Support local-first development before production integrations.
- Keep workflow policy table driven so business-managed configuration can be added later without rewriting decision code.

## Non-Goals for Initial Local Build

- No production mailbox mutation before Azure hosted runtime is explicitly configured.
- No production Blob Storage writes before Azure hosted runtime is explicitly configured.
- No production Postgres writes before Azure hosted runtime is explicitly configured.
- No production business management app for workflow tables before Azure hosted runtime is explicitly configured.
- No autonomous LLM decision making.

## Azure Hosted Runtime Scope

Azure hosted runtime is now in scope under `specs/110-azure-hosted-runtime.md`.

Azure hosting must not change business behavior. The deterministic pipeline, routing hierarchy, allowed outcomes, workflow tables, duplicate protection, extraction validation, action logging, and audit records must remain consistent between `LOCAL` and `AZURE`.

## Local Operations App Scope

A local React operations app is in scope after the initial local processing pipeline exists.

The app may:
- monitor local processing outcomes from Postgres
- inspect email, attachment, extraction, decision, action, and audit records
- open local artifacts referenced by Postgres
- display generated Mermaid trace artifacts
- manage local workflow configuration through explicit, versioned database updates

The app must not:
- mutate external mailboxes, Blob Storage, production databases, or production notification channels
- bypass deterministic workflow rules
- edit historical decisions, audit runs, audit steps, or action records
- silently apply workflow configuration changes without validation

## Allowed Outcomes

- `AUTO`: route automatically.
- `ESCALATE`: send to human ESCALATE.
- `FILE`: store without routing.
- `FLAG`: critical issue or misdirected item.
- `DISCARD`: non-actionable item, still logged.

## Success Metrics

- 0 silent drops.
- 100% decision logging.
- 100% audit trace creation for processed emails.
- All uncertain cases route to `ESCALATE`.
- Local processing works before production integrations are enabled.

## Acceptance Criteria

- The system can process a saved sample email locally.
- Every processed email produces exactly one final decision.
- Every final decision uses one allowed outcome.
- Every final decision includes a human-readable reason.
- Every final decision records the workflow rule or fallback that produced it.
- Clean low-risk invoices can route automatically when seeded local rules match.
- Uncertain, unsupported, or conflicting cases return `ESCALATE`.
- Local processing avoids production Blob Storage, production Postgres, and production notifications.
- Azure hosted runtime can process with Azure Blob Storage, Azure Postgres, and configured Azure integrations only when `APP_ENV=AZURE`.
- Existing golden scenario expectations remain unchanged across runtime modes.

## Core Rule

When uncertain, return `ESCALATE`.
