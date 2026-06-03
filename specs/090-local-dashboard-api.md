# 090 - Local Dashboard API and Data Access Spec

## Purpose

Define the backend boundary used by the React operations app in both `LOCAL` and Azure hosted runtimes.

The React client must not connect directly to Postgres or read arbitrary files or blobs directly. It must use the API that enforces query shapes, artifact safety, authentication, and workflow management validation.

## Architecture

The dashboard runtime consists of:
- React frontend served locally or by Azure Static Web Apps.
- FastAPI-compatible backend running locally or behind Azure Functions HTTP entrypoints.
- Postgres repositories under `src/ap_automation/repositories/`.
- Artifact service that only serves files referenced by database records and under approved local artifact roots or configured Blob containers.

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
- Hosted API requests must require authenticated Static Web Apps identity headers unless `APP_ENV=LOCAL`.

## Required Read Endpoints

Minimum read endpoints:
- `GET /api/health`
- `GET /api/monitor/summary`
- `GET /api/monitor/throughput`
- `GET /api/monitor/ESCALATE-reasons`
- `GET /api/monitor/destinations`
- `GET /api/monitor/ESCALATE-emails`
- `GET /api/monitor/recent-runs`
- `GET /api/emails/search`
- `GET /api/emails/{email_id}`
- `GET /api/emails/{email_id}/html`
- `GET /api/emails/{email_id}/inline/{cid_token}`
- `GET /api/emails/{email_id}/runs`
- `GET /api/audit-runs/{run_id}`
- `GET /api/audit-runs/{run_id}/steps`
- `GET /api/artifacts/{artifact_ref}`
- `GET /api/attachments/{attachment_id}/download`
- `GET /api/workflow/rules`
- `GET /api/workflow/rules/{rule_code}`
- `GET /api/workflow/destinations`
- `GET /api/workflow/runtime-config`
- `GET /api/workflow/audit-events`
- `GET /api/workflow/asset-custom`
- `POST /api/workflow/asset-custom`
- `PATCH /api/workflow/asset-custom/{asset_custom_id}`
- `DELETE /api/workflow/asset-custom/{asset_custom_id}`
- `GET /api/workflow/assets`
- `POST /api/workflow/assets`
- `PATCH /api/workflow/assets/{asset_id}`
- `DELETE /api/workflow/assets/{asset_id}`
- `PATCH /api/workflow/rules/{rule_code}`
- `PATCH /api/workflow/rules/{rule_code}/conditions/{condition_key}`
- `PATCH /api/workflow/destinations/{destination_code}`
- `PATCH /api/workflow/runtime-config/{config_key}`

Endpoint names may change during implementation if the spec is updated first.

The API must not expose legacy `/api/workflow/properties` or `/api/monitor/properties` compatibility routes. Asset management endpoints read and write `asset` rows, resolve destinations through `ownership`, and audit mutations with `management_audit_events.changed_table = 'asset'`. Asset Custom endpoints read and write `asset_custom` rows and audit mutations with `management_audit_events.changed_table = 'asset_custom'`.

## Monitor Read Models

Monitor endpoints must return data shaped for business monitoring:
- totals by outcome
- automation, ESCALATE, file, flag, discard, and failed-run rates
- current ESCALATE folder email count
- daily throughput by monitor category, including automated, ESCALATE, filed decisions, and failed audit runs
- top ESCALATE reasons
- top routing destinations
- confidence buckets
- ESCALATE emails from `escalate_queue`, which mirrors the current Graph `ESCALATE` folder contents after each mailbox sync
- recent runs with email subject, sender, outcome, reason, status, and timestamps
- recent runs must return one row per `audit_runs` row even when batch processing created multiple item-level decisions

Metric queries must handle empty databases by returning zero values and empty arrays, not synthetic demo rows.

## Email Detail Read Model

The email detail endpoint must return a single read model containing:
- email metadata
- attachments
- sanitized HTML artifact reference when `emails.html_storage_path` is populated
- latest extraction
- decisions for the email, newest first
- actions
- ESCALATE queue item when present
- audit runs, newest first

Audit run detail endpoints must return:
- run metadata
- ordered audit steps
- resolved trace artifact reference when present

## Artifact Serving Rules

The API may serve artifacts only when:
- the artifact path is stored in Postgres or in an audit record linked to Postgres
- the resolved local path is under the project `local/` directory in `LOCAL`, or the reference resolves to a configured Blob artifact in `AZURE`
- the artifact exists
- the API maps the path to an opaque `artifact_ref` rather than exposing arbitrary filesystem traversal

Requests for missing, unlinked, or out-of-scope artifacts must fail loudly with explicit errors.

`GET /api/emails/{email_id}/html` must serve the sanitized HTML email preview referenced by `emails.html_storage_path`. It must not serve raw `.msg` bytes as HTML. If no sanitized HTML exists, the endpoint must return an explicit not-found response that the UI can display.

When sanitized HTML contains `cid:` URLs, `GET /api/emails/{email_id}/html` must rewrite those references to API paths and `GET /api/emails/{email_id}/inline/{cid_token}` must resolve matching attachments by `attachments.metadata.content_id` (or exact attachment filename fallback) for the same email only.

Attachment downloads must be served through an attachment-specific API that looks up `attachments.storage_path` by `attachment_id`, validates the local artifact path, and returns a `Content-Disposition` filename based on `attachments.file_name`. The frontend must not use raw storage paths to download attachments.

## Search Requirements

Email search must support:
- free-text query
- date range
- outcome
- run status
- ESCALATE status
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

## ESCALATE Queue Requirements

The monitor ESCALATE email list must return the current contents of `escalate_queue`. After each mailbox sync, `escalate_queue` must mirror the current Graph `ESCALATE` folder by clearing prior mirror rows and reloading one row per current folder message.

Completion is handled outside the app by moving mail out of `ESCALATE`, normally to `ESCALATE_COMPLETE`. The API must not expose a manual completion endpoint.

## Testing Requirements

Tests must cover:
- monitor queries against an empty database
- monitor queries against seeded completed, failed, and ESCALATE records
- email search by metadata
- email search by extracted fields
- detail read model assembly
- sanitized email HTML endpoint behavior
- inline `cid:` attachment resolution behavior for sanitized email HTML
- artifact path allow-list behavior
- artifact path traversal rejection
- API error payload shape

## Acceptance Criteria

- The React app uses the local API rather than direct Postgres access.
- Monitor endpoints return real Postgres-derived metrics.
- Recent processing runs show one row per processing run execution.
- Email search can find records by metadata and extracted fields.
- Detail endpoints return all data needed by the detail page without frontend SQL knowledge.
- Artifact endpoints serve only approved local artifact paths linked to database records.
- In Azure, artifact endpoints serve only approved Blob artifacts linked to database records.
- Missing or unsafe artifacts return explicit errors.
- Hosted API requests without authenticated SWA identity return explicit `401` or `403`.
- Backend tests cover dashboard read models, search, and artifact safety.
