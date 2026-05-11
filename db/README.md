# Database Setup

See `db/SCHEMA.md` for the full table-by-table and field-by-field data dictionary. Any schema change must update that file in the same change.

Local database:

- database: `apautomation`
- user: `postgres`
- host: `localhost`

Set the password in the current shell before running commands:

```powershell
$env:PGPASSWORD='llamas'
```

Apply schema and core seed:

```powershell
psql -h localhost -U postgres -d apautomation -v ON_ERROR_STOP=1 -f db\schema.sql
psql -h localhost -U postgres -d apautomation -v ON_ERROR_STOP=1 -f db\seed.sql
```

The core seed sets the default high-dollar invoice policy to file invoices over `amount_review_threshold` to the local lien release folder for hold.

Import the Medius routing workbook:

```powershell
powershell -ExecutionPolicy Bypass -File db\import-reference-workbook.ps1 -WorkbookPath 'reference\Medius Routing.xlsx' -Database apautomation -User postgres -HostName localhost
```

Resolve imported route hints to final destination codes:

```powershell
psql -h localhost -U postgres -d apautomation -v ON_ERROR_STOP=1 -f db\resolve-property-routes.sql
```

The schema, seed, workbook import, and route-resolution scripts are idempotent. The workbook import upserts source rows and reference-derived properties, aliases, and route hints.

Run local reference test emails:

```powershell
.\run-local-test-emails.ps1
```

The script runs the CLI in `APP_ENV=LOCAL` and `DRY_RUN=true`, uses `reference\test_emails`, and passes `--codex-skip-git-repo-check` for local workspaces that are not Git repositories.
