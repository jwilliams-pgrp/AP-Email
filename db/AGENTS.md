# AGENTS.md

Database files define the AP Automation source-of-truth schema, seed data, and reference-data import process.

When changing anything in this directory:
- Update `db/SCHEMA.md` for every table, column, enum, index, constraint, function, or seed behavior change.
- Keep `db/schema.sql`, `db/seed.sql`, `db/resolve-property-routes.sql`, and `db/README.md` consistent.
- Prefer idempotent SQL that is safe to rerun locally.
- Do not hard-code workflow policy in application code when it belongs in Postgres tables.
- Preserve audit replay: do not remove fields or overwrite historical meaning without a migration and documentation note.
- Treat reference workbook data as business-maintained source data; document any normalization or interpretation in SQL comments and `db/SCHEMA.md`.
