# AGENTS.md

Application code for AP Automation lives under this directory.

Follow the root `AGENTS.md` first. These notes clarify how to work inside `src/`.

## Layer Boundaries

- `ap_automation/models/`: typed data shapes, validation, and schema contracts only.
- `ap_automation/services/`: deterministic business workflow logic and local processing orchestration.
- `ap_automation/repositories/`: persistence and data access only.
- `ap_automation/agents/`: future LLM interaction only. Do not put final routing decisions here.

Do not let repository code make business decisions. Do not let services embed mutable routing destinations, thresholds, or business-maintained rule values that belong in Postgres.

## Local-First Rules

- Default behavior must remain `APP_ENV=LOCAL` and `DRY_RUN=true`.
- Local processing may write local artifacts and local database records.
- Local processing must not mutate external mailboxes, Blob Storage, production databases, or production notification channels.
- Dry-run actions must be explicit records or manifests, not hidden no-ops.

## Decision Safety

- Deterministic code makes final outcomes.
- Missing, ambiguous, unsupported, or low-confidence inputs must resolve to `REVIEW` unless a spec defines a safer non-routing outcome.
- Missing required workflow configuration should raise an explicit error instead of silently substituting defaults.
- Keep final decision outputs audit-friendly: include outcome, reason, confidence, extracted fields, routing match, matched rule, and dry-run status.

## Tests

- Add or update tests with every behavior change.
- Prefer focused unit tests for deterministic rule behavior.
- Add processor or repository tests when changing audit, artifact, or persistence behavior.
- Golden scenarios should stay easy to inspect and should not depend on external systems.
