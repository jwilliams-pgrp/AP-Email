# 090 - Local Dashboard API and Data Access Spec

## Purpose

Define the backend boundary used by the local React operations app.

The React client must not connect directly to Postgres or read arbitrary files from disk. It must use a local API that enforces query shapes, artifact path safety, and workflow management validation.

## Architecture

The local dashboard runtime consists of:
- React frontend served locally.
- FastAPI backend running locally with Python.
- Postgres repositories under `src/ap_automation/repositories/`.
- Local artifact service that only serves files referenced by database records and under approved local artifact directories.

New dashboard code must live under top-level `app/`.

Expected structure:
- `app/api/` for FastAPI application code.
- `app/web/` for the React frontend.
- `app/AGENTS.md` for local app-specific engineering guidance before implementation begins.

## API Boundary Rules

- Repository code owns database access only.
- Service code owns dashboard read models, artifact path validation, and workflow management validation.
- API route handlers should remain thin request/response adapters.
- The frontend must not embed database credentials.
- The frontend must not construct SQL.
- The backend must use parameterized queries.
- API failures must return explicit error payloads that the UI can display.

## Required Read Endpoints

Minimum read endpoints:
- `GET /api/health`
- `GET /api/monitor/summary`
- `GET /api/monitor/throughput`
- `GET /api/monitor/review-reasons`
- `GET /api/monitor/destinations`
- `GET /api/monitor/review-emails`
- `GET /api/monitor/recent-runs`
- `GET /api/emails/search`
- `GET /api/emails/{email_id}`
- `GET /api/emails/{email_id}/html`
- `GET /api/emails/{email_id}/runs`
- `GET /api/audit-runs/{run_id}`
- `GET /api/audit-runs/{run_id}/steps`
- `GET /api/artifacts/{artifact_ref}`
- `GET /api/attachments/{attachment_id}/download`
- `PATCH /api/review-queue/{review_id}/complete`
- `GET /api/workflow/rules`
- `GET /api/workflow/rules/{rule_code}`
- `GET /api/workflow/destinations`
- `GET /api/workflow/runtime-config`
- `GET /api/workflow/audit-events`
- `PATCH /api/workflow/rules/{rule_code}`
- `PATCH /api/workflow/rules/{rule_code}/conditions/{condition_key}`
- `PATCH /api/workflow/destinations/{destination_code}`
- `PATCH /api/workflow/runtime-config/{config_key}`

Endpoint names may change during implementation if the spec is updated first.

## Monitor Read Models

Monitor endpoints must return data shaped for business monitoring:
- totals by outcome
- automation, review, file, flag, discard, and failed-run rates
- open review queue count
- daily throughput by monitor category, including automated, review, filed decisions, and failed audit runs
- top review reasons
- top routing destinations
- confidence buckets
- outstanding review emails from `review_queue` where status is `open` or `in_progress`
- recent runs with email subject, sender, outcome, reason, status, and timestamps

Metric queries must handle empty databases by returning zero values and empty arrays, not synthetic demo rows.

## Email Detail Read Model

The email detail endpoint must return a single read model containing:
- email metadata
- attachments
- sanitized HTML artifact reference when `emails.html_storage_path` is populated
- latest extraction
- decisions for the email, newest first
- actions
- review queue item when present
- audit runs, newest first

Audit run detail endpoints must return:
- run metadata
- ordered audit steps
- resolved trace artifact reference when present

## Artifact Serving Rules

The API may serve local artifacts only when:
- the artifact path is stored in Postgres or in an audit record linked to Postgres
- the resolved absolute path is under the project `local/` directory
- the file exists
- the API maps the path to an opaque `artifact_ref` rather than exposing arbitrary filesystem traversal

Requests for missing, unlinked, or out-of-scope artifacts must fail loudly with explicit errors.

`GET /api/emails/{email_id}/html` must serve the sanitized HTML email preview referenced by `emails.html_storage_path`. It must not serve raw `.msg` bytes as HTML. If no sanitized HTML exists, the endpoint must return an explicit not-found response that the UI can display.

Attachment downloads must be served through an attachment-specific API that looks up `attachments.storage_path` by `attachment_id`, validates the local artifact path, and returns a `Content-Disposition` filename based on `attachments.file_name`. The frontend must not use raw storage paths to download attachments.

## Search Requirements

Email search must support:
- free-text query
- date range
- outcome
- run status
- review status
- sender
- destination code
- matched rule code

Search should include extracted fields stored in JSONB when practical:
- vendor name
- invoice number
- property or building
- amount
- document type

Search result rows must include enough data for the UI to help a user choose the correct email before opening details.

Search requests from the UI should be safe for debounced auto-search. Empty queries may return the newest limited records; non-empty queries must apply the provided text filters without requiring a separate submit action.

## Review Queue Requirements

The monitor review email list must return only outstanding review items by default. A review item remains outstanding until a business user completes it through the API.

Completing a review item must:
- set `review_queue.status` to `resolved`
- set `review_queue.resolved_at` to the completion timestamp
- fail explicitly when the review item does not exist
- be idempotent for already resolved or closed review items

## Testing Requirements

Tests must cover:
- monitor queries against an empty database
- monitor queries against seeded completed, failed, and review records
- email search by metadata
- email search by extracted fields
- detail read model assembly
- sanitized email HTML endpoint behavior
- artifact path allow-list behavior
- artifact path traversal rejection
- API error payload shape

## Acceptance Criteria

- The React app uses the local API rather than direct Postgres access.
- Monitor endpoints return real Postgres-derived metrics.
- Email search can find records by metadata and extracted fields.
- Detail endpoints return all data needed by the detail page without frontend SQL knowledge.
- Artifact endpoints serve only approved local artifact paths linked to database records.
- Missing or unsafe artifacts return explicit errors.
- Backend tests cover dashboard read models, search, and artifact safety.
