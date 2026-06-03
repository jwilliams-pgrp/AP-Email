# AGENTS.md

Database files define the AP Automation source-of-truth schema, seed data, and migration-safe local setup.

When changing anything in this directory:
- Update `db/SCHEMA.md` for every table, column, enum, index, constraint, function, or seed behavior change.
- Keep `db/schema.sql`, `db/seed.sql`, `db/SCHEMA.md`, and `db/README.md` consistent.
- Every SQL behavior, schema, or configuration change must update `db/schema.sql` and/or `db/seed.sql`.
- Keep `db/schema.sql` as the complete replayable DDL baseline using `create` and `alter` statements.
- Keep `db/seed.sql` as the complete replayable configuration and reference-data baseline using deterministic `insert ... on conflict`, replacement, or equivalent idempotent statements.
- Add one-time targeted SQL files only for narrowly named changes to existing databases; fold the accepted final state back into `db/schema.sql` and/or `db/seed.sql`.
- Prefer idempotent SQL that is safe to rerun locally.
- Do not hard-code workflow policy in application code when it belongs in Postgres tables.
- Preserve audit replay: do not remove fields or overwrite historical meaning without a migration and documentation note.
- Treat reference workbook data as business-maintained source data; document any normalization or interpretation in SQL comments and `db/SCHEMA.md`.
- Avoid generated introspection output under `db/introspection/` unless the task explicitly requires it.
