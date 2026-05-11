# 000 - Product Spec: AP Processing Agent

## Purpose

Automate AP inbox processing for Hillwood Properties by classifying emails, extracting invoice data, applying deterministic routing rules, and logging every decision.

## Goals

- Reduce manual invoice routing for clean, low-risk invoices.
- Route clean invoices automatically when deterministic rules allow it.
- Escalate exceptions with clear, reviewable reasons.
- Maintain a permanent audit trail for every processed email.
- Support local-first development before production integrations.
- Keep workflow policy table driven so business-managed configuration can be added later without rewriting decision code.

## Non-Goals for Initial Local Build

- No production mailbox mutation.
- No production Blob Storage writes.
- No production Postgres writes.
- No production business management app for workflow tables yet.
- No autonomous LLM decision making.

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
- `REVIEW`: send to human review.
- `FILE`: store without routing.
- `FLAG`: critical issue or misdirected item.
- `DISCARD`: non-actionable item, still logged.

## Success Metrics

- 0 silent drops.
- 100% decision logging.
- 100% audit trace creation for processed emails.
- All uncertain cases route to `REVIEW`.
- Local dry-run processing works before production integrations are enabled.

## Acceptance Criteria

- The system can process a saved sample email in local dry-run mode.
- Every processed email produces exactly one final decision.
- Every final decision uses one allowed outcome.
- Every final decision includes a human-readable reason.
- Every final decision records the workflow rule or fallback that produced it.
- Clean low-risk invoices can route automatically when seeded local rules match.
- Uncertain, unsupported, or conflicting cases return `REVIEW`.
- No external system is mutated in local dry-run mode.

## Core Rule

When uncertain, return `REVIEW`.
