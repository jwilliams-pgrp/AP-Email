# AGENTS.md

# AP Processing Agent - Engineering Contract

This document defines how AI agents and developers must build, modify, and maintain the AP Processing system.

This system processes financial documents and routes invoices. Incorrect behavior can result in financial loss, audit failure, or operational disruption.

This file is the source of truth for engineering behavior.

---

# 1. Core Principles

## 1.1 Spec-Driven Development
- No feature may be implemented without a corresponding spec in `/specs`.
- Specs must be updated before behavior changes.
- All specs must include acceptance criteria.
- All acceptance criteria must be covered by tests.

## 1.2 Local-First Development
- Default runtime is `LOCAL`.
- Local mode may mutate the configured Graph Intake mailbox according to routing destinations.
- Code must run locally before cloud or production deployment.
- Production behavior must be enabled explicitly through runtime config.

## 1.3 Safety Over Automation
- When uncertain, choose `ESCALATE`.
- Never prioritize automation rate over correctness.
- No silent failures, silent drops, or hidden fallback behavior.
- Unsupported or ambiguous inputs must produce explicit ESCALATEable outcomes.

## 1.4 Deterministic Control
- LLMs may extract and classify only.
- Business decisions must be made by deterministic code.
- Routing decisions must be explainable and reproducible.
- Workflow policy must come from Postgres configuration tables, not hard-coded rule constants.

## 1.5 Full Auditability
Every processed email must produce a decision record including:
- outcome
- reason
- confidence
- extracted fields
- routing match, if any
- matched workflow rule, if any
- model and prompt version, if an LLM was used

---

# 2. System Architecture Rules

## 2.1 Decision Pipeline

All processing must follow:

Ingestion -> Extraction -> Validation -> Rules Engine -> Decision -> Action -> Audit Log

No step may be skipped.

## 2.2 LLM Constraints

Allowed:
- Extract structured invoice data.
- Classify email or document type.
- Provide reasoning for uncertainty.

Not allowed:
- Making final routing decisions.
- Generating destination emails.
- Overriding routing tables.
- Performing actions.

All LLM outputs must:
- Be structured JSON.
- Validate against defined schemas.
- Include confidence signals where applicable.
- Be audit logged before use.

Local mode LLM extraction must use the configured Azure OpenAI extractor unless a test fixture is explicitly supplied.

## 2.3 Table-Driven Workflow Rules

Workflow behavior must be table driven from Postgres wherever business users may need to change it later.

Table-driven configuration includes:
- routing destinations
- property ownership and management classification
- hard exception rules
- confidence thresholds
- amount thresholds
- statement handling
- duplicate detection policy
- enabled or disabled rule status
- effective dates and versioning

Application code may define the evaluation engine, schema validation, and safety invariants. Application code must not hard-code business-maintained routing rows or mutable workflow policy.

## 2.4 Routing Rules Hierarchy

Routing decisions must follow this priority:

1. Hard exception rules
2. Duplicate detection
3. Routing table match
4. Confidence threshold
5. ESCALATE fallback

## 2.5 Allowed Outcomes

Every email must resolve to one of:

- `AUTO`: automatically routed
- `ESCALATE`: requires human decision
- `FILE`: stored without routing
- `FLAG`: critical issue or misdirected
- `DISCARD`: non-actionable, still logged

---

# 3. Safety and Guardrails

## 3.1 Action Execution
- Runtime behavior must be uniform; do not implement alternate non-mutating execution paths.
- Graph Intake mailbox actions must follow configured routing destinations.
- All actions must create audit records.

## 3.2 Confidence Handling
- If confidence is below the configured threshold, return `ESCALATE`.
- If required data is missing, return `ESCALATE`.
- If extracted signals conflict, return `ESCALATE`.

## 3.3 Duplicate Protection
- Duplicate detection must run before any routing.
- Suspected duplicates must go to `ESCALATE`.
- Reprocessing must not create duplicate actions.

## 3.4 High-Risk Conditions

High-risk conditions must default to `ESCALATE` unless a spec explicitly defines a safer non-routing outcome such as `FILE`.

Examples:
- Multi-invoice PDFs
- Invoice plus lien waiver
- Link-only invoices
- Contracts or pay applications
- Vendor inquiries
- Unknown buildings
- Amount thresholds, such as invoices greater than 10000

---

# 4. Data and Persistence Rules

## 4.1 Postgres Is Source of Truth

Postgres must store:
- emails
- attachments metadata
- routing entities
- workflow rules and config
- decisions
- ESCALATE queue entries
- audit runs and audit steps
- seed and reference data lineage

## 4.2 Blob or File Storage

Blob or local file storage is used for:
- raw emails
- attachments
- processed artifacts
- generated audit trace artifacts

Never store large binaries in Postgres.

## 4.3 Idempotency
- Processing must be idempotent per email.
- Reprocessing must not create duplicate actions.
- Idempotency keys must be persisted.

## 4.4 Database Documentation
- `db/SCHEMA.md` is the canonical database data dictionary.
- Every database schema change must update `db/SCHEMA.md` in the same change.
- Database setup and seed command changes must update `db/README.md`.
- Every SQL behavior, schema, or configuration change must update `db/schema.sql` and/or `db/seed.sql`.
- `db/schema.sql` must remain a complete replayable DDL baseline using `create` and `alter` statements.
- `db/seed.sql` must remain a complete replayable configuration and reference-data baseline using deterministic `insert ... on conflict`, replacement, or equivalent idempotent statements.
- One-time targeted SQL files may be added only for narrowly named changes to existing databases; after acceptance, their final state must be folded back into `db/schema.sql` and/or `db/seed.sql`.
- Database changes must preserve table-driven workflow policy and audit replay unless a spec explicitly changes that behavior.

---

# 5. Code Organization Rules

## 5.1 Separation of Concerns

- `agents/`: LLM interaction only
- `services/`: business logic and deterministic workflow evaluation
- `repositories/`: database access only
- `models/`: schemas and validation
- `config/` or database seed files: default local workflow policy

No cross-layer leakage allowed.

## 5.2 Service Rules
- All external mutations must execute through explicit service paths.
- All external mutations must produce logs.
- All external mutations must create audit records.

## 5.3 Repository Rules
- No business logic.
- No LLM calls.
- No external API calls.
- No hidden defaults that bypass persisted configuration.

---

# 6. Testing Requirements

## 6.1 Required Test Types
- Unit tests for deterministic rules.
- Golden tests for email scenarios.
- Integration tests for persistence.
- Schema validation tests for LLM output.
- Seed data tests for required local workflow configuration.

## 6.2 Golden Scenarios
Must include:
- Clean invoice for Hillwood-owned property
- Clean invoice for external PM property
- ALC routing
- Multifamily routing
- Multi-invoice PDF
- Invoice over configured amount threshold
- Duplicate invoice
- Link-only invoice
- Statement
- Unknown building
- Low confidence extraction

## 6.3 Rule
No test means no merge.

---

# 7. Observability

## 7.1 Logging
All major steps must log:
- input identifiers
- decisions
- reasons
- errors
- config or rule version used

Logs must be structured.

## 7.2 Metrics

Future production metrics include:
- automation rate
- ESCALATE rate
- misroute rate
- confidence distribution
- rule hit distribution
- duplicate rate

---

# 8. Security and Access

## 8.1 Authentication
- Prefer Managed Identity and RBAC for production.
- Local mode uses the developer machine's local Postgres database.
- Production mode uses Azure-hosted Postgres with Azure identity and RBAC.
- No secrets in code.
- Local secrets must use ignored environment files or local secret stores.

## 8.2 Data Handling
- Treat all invoice data as sensitive.
- Do not log raw PII unnecessarily.
- Reference materials are business context, not application source code.

---

# 9. Development Workflow

## 9.1 Required Loop

1. Read applicable specs.
2. Write or update tests.
3. Implement the minimal change.
4. Validate locally.
5. Ensure audit logging.
6. Update docs if behavior changed.

## 9.2 Repository Navigation

- Avoid broad recursive scans from the repository root. This workspace can contain large dependency, data, and generated-output directories.
- Start with exact root-level paths, then inspect only the relevant subdirectory.
- Project map:
  - `specs/`: behavior specs; read `000-product.md`, then the numbered spec for the area being changed.
  - `src/ap_automation/`: Python package for models, services, repositories, and LLM extraction adapters.
  - `app/api/`: FastAPI dashboard and workflow-management API.
  - `app/web/`: React dashboard frontend.
  - `db/schema.sql`, `db/seed.sql`, `db/SCHEMA.md`, `db/README.md`: database source of truth and setup docs.
  - `tests/`: backend unit, golden, repository, and local processor tests.
  - `local/`, `app/web/node_modules/`, `app/web/dist/`, `db/introspection/`, caches, and `src/ap_automation.egg-info/`: generated or local-only output; avoid reading unless directly relevant.
  - `reference/`: preserved business corpus; do not scan unless explicitly needed.
- The local React app lives at `app/web`. Use `launch-react-app.ps1` from the repository root for local UI testing.
- The local dashboard API lives at `app/api` and should run on `127.0.0.1:8001`; `127.0.0.1:8000` may be occupied by unrelated local services.
- Use `launch-local-dashboard.ps1` from the repository root when the UI needs Postgres-backed API data and local artifact access.
- Run backend tests with `pytest`.
- Run frontend validation with `npm --prefix app/web run build`.
- Prefer bounded file discovery commands with explicit names and shallow paths before using recursive search.

## 9.3 Planned Build Sequence

1. Finalize `AGENTS.md` and specs.
2. Design Postgres schema and seed strategy.
3. Seed local workflow tables from approved reference material.
4. Build deterministic decision engine.
5. Add local ingestion and attachment handling.
6. Add LLM extraction behind schema validation.
7. Add production integrations only after local behavior is tested.

---

# 10. Maintaining This File

## 10.1 When to Update
Update this file when:
- repeated implementation mistakes occur
- new architectural constraints are introduced
- new safety risks are identified
- patterns emerge across modules

## 10.2 How to Update
- Keep rules concise and enforceable.
- Avoid duplication with specs.
- Do not include detailed implementation design.
- Prefer rules over explanations.

## 10.3 Ownership Model
- Humans define principles and constraints.
- Agents may propose improvements.
- Changes must preserve safety guarantees.

---

# 11. Non-Negotiable Rule

When in doubt, return `ESCALATE`.

Never guess. Never assume. Never silently proceed.
