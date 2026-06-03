# 080 - Local Operations Dashboard Spec

## Purpose

Define the React operations dashboard used by business users and implementers to monitor AP inbox processing, search processed emails, inspect audit traces, and manage workflow configuration.

This app supports both `LOCAL` development runtime and Azure hosted runtime. In Azure, the app is hosted by Azure Static Web Apps with Microsoft Entra SSO.

## Users

- Business monitors who need to understand volume, automation health, ESCALATE work, and errors.
- AP ESCALATEers who need to find a specific email and understand why it was routed, filed, flagged, discarded, or sent to ESCALATE.
- System maintainers who need to inspect trace artifacts and workflow rule behavior.

## App Structure

New dashboard code must live under top-level `app/` unless a later spec changes that structure.

The app must use a toggle group as the primary page switcher with three top-level pages:
- `Monitor`
- `Email Detail`
- `Management`

The default visual mode is dark mode.

## Visual Direction

Visual thesis: dark glass operations console with restrained Hillwood blue accents, dense readable data, and clear status hierarchy.

Content plan:
- Monitor: system health, operational KPIs, trends, queues, errors, and recent events.
- Email Detail: search, selected email facts, artifacts, extraction, decision, actions, audit steps, and Mermaid trace.
- Management: workflow rule configuration, destinations, runtime thresholds, and effective-dated changes.

Interaction thesis:
- Page switching should feel immediate and preserve filter/search context.
- Drill-in interactions should move from metric, queue row, or search result to a specific email detail view.
- Management edits should use explicit ESCALATE/confirm flows before saving local database changes.

## Monitor Page Requirements

The monitor page must show high-level business monitoring metrics:
- total processed emails
- automation rate
- ESCALATE rate
- file rate
- flag rate
- discard rate
- error or failed-run rate
- current ESCALATE folder email count
- duplicate candidate count when available
- average processing duration when available
- confidence distribution
- top ESCALATE reasons
- top routing destinations
- trended daily throughput as a stacked bar chart for automated, ESCALATE, failed, and filed categories
- ESCALATE emails currently present in the mailbox `ESCALATE` folder
- recent audit events or processing runs

The monitor page must support:
- date range filters, at minimum `7d`, `30d`, and `90d`
- placing the page switcher centered in the app header and the monitor date range selector in the header action area
- outcome filtering
- clicking metric segments, queue rows, or recent events to drill into matching email records
- clear distinction between completed, failed, and in-progress audit runs
- a `ESCALATE Emails` section that lists the informational live mirror of messages currently in the mailbox `ESCALATE` folder
- the primary KPI metrics in a single desktop row when viewport width allows
- ESCALATE reason labels with enough horizontal room to read long reasons without truncating the useful text
- the `ESCALATE Emails` list constrained to roughly five visible rows with internal scrolling for longer queues
- explicit empty states when no local processing data exists

## Email Detail Page Requirements

The detail page must support searching for a specific email by:
- subject
- sender email
- source message id
- idempotency key
- email id
- decision reason text
- vendor, invoice number, property/building, or amount when present in extracted fields

For a selected email, the page must show:
- email metadata from `emails`
- the Microsoft Office web link when the email was ingested from Graph and the link was captured
- Open actions for captured Microsoft Office web links from ESCALATE queue rows and the Email Detail header
- attachment metadata from `attachments`
- sanitized browser-readable email HTML when `emails.html_storage_path` exists
- links or open actions for local raw email and attachment artifacts
- latest extraction and validation result from `extractions`
- final decision from `decisions`
- planned or executed actions from `actions`
- ESCALATE queue status when present
- audit run history from `audit_runs`
- ordered audit steps from `audit_steps`
- Mermaid trace artifact referenced by `audit_runs.trace_artifact_path`

The detail page must:
- auto-search after the user types, with a short debounce rather than requiring a submit button
- show subject, sender, outcome, and decision reason in one cohesive email header instead of separate field summary panels or isolated field cards
- show the email date in search result rows so same-subject emails remain distinguishable
- place the sanitized HTML email preview below the header on the left, with attachment download links appended below the preview when attachments exist
- place audit traces and ordered audit steps to the right of the email preview
- keep the page itself fixed to the viewport where practical, with independent scrolling inside search results, the email viewer, and audit panes

The page must make unsupported or missing data obvious. If an artifact path is missing or does not resolve locally, the UI must show an explicit error state instead of hiding the artifact.

## Artifact Viewing Requirements

The app must never store large binaries in Postgres.

Artifact viewing may use local file-serving endpoints for:
- raw `.msg` email artifacts
- attachment files
- generated extraction JSON artifacts
- generated prompt text artifacts
- Mermaid trace text files
- action manifests

The UI must treat local artifacts as sensitive AP data and avoid logging raw contents to the browser console.

Raw Outlook `.msg` files cannot be reliably rendered in-browser in their original Outlook form. The processing pipeline should convert parsed email content to sanitized HTML and store it as an artifact referenced by `emails.html_storage_path`.

The detail page must render the sanitized HTML preview when available. The preview should preserve useful business context such as subject, sender, recipients when available, received timestamp, body content, and attachment list, but it does not need to reproduce Outlook chrome or exact Outlook rendering.

When sanitized HTML includes inline `cid:` references, the dashboard must resolve those references through API-backed attachment serving so inline logos and embedded body images render in the email preview.

Attachment download/open actions must use artifact-serving endpoints backed by local storage for local mode and future Blob Storage references after productionization. The frontend must not construct direct local or blob paths itself.

## Mermaid Trace Requirements

The detail page must render the Mermaid flow diagram for the selected audit run when a trace artifact exists.

The rendered diagram must show:
- major pipeline steps
- final outcome
- matched rule or fallback
- failed step styling when the run failed

If Mermaid rendering fails, the raw Mermaid text must remain available with an explicit rendering error.

## Non-Goals

- No production authentication model in the first local app.
- No direct production deployment outside `specs/110-azure-hosted-runtime.md`.
- No direct mailbox mutation.
- No direct edit of historical emails, extractions, decisions, actions, audit runs, or audit steps.
- No autonomous decision overrides from the UI.

## Acceptance Criteria

- The app starts locally and defaults to dark mode.
- The app builds for Azure Static Web Apps and protects hosted access with Microsoft Entra SSO.
- The primary navigation is a toggle group with `Monitor`, `Email Detail`, and `Management`.
- The monitor page displays business KPIs from local Postgres, not hard-coded demo data.
- Date range filters update monitor metrics.
- A user can drill from a monitor queue/event/search result to a selected email detail view.
- The detail page can search by email metadata and extracted invoice fields.
- The detail page displays email metadata, attachments, extraction, decision, actions, ESCALATE status, audit runs, audit steps, and Mermaid trace when present.
- The detail page can expose the captured Microsoft Office web link for Graph-ingested emails.
- ESCALATE queue rows and the Email Detail header show an Open action when a captured Microsoft Office web link exists.
- The detail page renders sanitized email HTML when `emails.html_storage_path` exists.
- The detail page renders cleaned sanitized HTML previews without visible Outlook CSS/comment noise while preserving useful business content such as forwarded invoice text, signatures, payment details, and links.
- The detail page renders inline `cid:` images in sanitized HTML when matching attachments exist for the selected email.
- Missing local artifacts produce explicit visible errors.
- Mermaid trace artifacts are rendered when valid and shown as raw text when rendering fails.
- UI tests cover page switching, monitor empty state, email search, detail rendering, and Mermaid fallback.
