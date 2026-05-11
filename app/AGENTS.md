# AGENTS.md

Local operations dashboard code lives here.

Follow the root project contract first. These notes clarify the app boundary.

## Structure

- `api/`: FastAPI routes, dashboard read models, artifact safety, and workflow management API.
- `web/`: React frontend.

## Rules

- The React app must call the FastAPI API. It must not connect directly to Postgres.
- API code must use parameterized SQL.
- Artifact endpoints must only serve files referenced by Postgres and resolved under approved artifact roots.
- Workflow edits must write `management_audit_events` and create `workflow_rule_versions` snapshots.
- Historical operational records are read-only from this app.
- Default UI mode is dark.
