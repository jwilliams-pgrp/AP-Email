# Database Setup

See `db/SCHEMA.md` for the canonical data dictionary. Any schema change must update that file in the same change.

Local database:

- database: `apautomation`
- user: `postgres`
- host: `localhost`

If your local Postgres requires a password, set it in the current shell before running commands. Do not commit real passwords:

```powershell
$env:PGPASSWORD='<local-postgres-password>'
```

Apply the replayable schema and seed baselines:

```powershell
psql -h localhost -U postgres -d apautomation -v ON_ERROR_STOP=1 -f db\schema.sql
psql -h localhost -U postgres -d apautomation -v ON_ERROR_STOP=1 -f db\seed.sql
```

`db/schema.sql` is the full replayable DDL baseline exported from local Postgres. `db/seed.sql` seeds canonical `asset`, `asset_custom`, and `ownership` rows exported from the live local `apautomation` database, plus local workflow rules, destinations, runtime config, and no-action patterns. One-time SQL files are not part of the current baseline; accepted changes must be folded back into `schema.sql` and/or `seed.sql`.

Regenerate the local schema baseline from the canonical local database:

```powershell
$env:PGPASSWORD='<local-postgres-password>'
pg_dump -h localhost -U postgres -d apautomation --schema=public --schema-only --no-owner --no-privileges --file db\schema.sql
```

Regenerate `db/seed.sql` from workflow/reference/config tables only: `routing_destinations`, `ownership`, `asset`, `asset_custom`, `runtime_config`, `no_action_email_patterns`, `workflow_rules`, `workflow_rule_versions`, and `workflow_rule_conditions`. Do not include operational or sensitive tables such as `emails`, `attachments`, `invoices`, `extractions`, `decisions`, `actions`, `audit_runs`, `audit_steps`, `llm_interactions`, artifact records, queue entries, or history records.

Deploy the nonprod Azure Postgres baseline:

```powershell
.\deploy-azure-postgres-nonprod.ps1 `
  -Subscription '<subscription-id-or-name>' `
  -ResourceGroup 'rg-hw-propertiesapmail-nonprod' `
  -PostgresServer 'psql-hw-propertiesapmail-nonprod' `
  -DatabaseName 'apautomation' `
  -AdminUser '<entra-admin-user>' `
  -FunctionIdentityName 'id-hw-propertiesapmail-nonprod'
```

The deploy script acquires an Azure PostgreSQL Entra token, creates the database if needed, applies `db/schema.sql`, `db/seed.sql`, and `db/azure-permissions.sql`, then prints verification counts for key workflow/reference tables.

Apply the targeted current-reply no-action sender-domain update to an existing database:

```powershell
psql -h <postgres-host> -U <entra-admin-user> -d apautomation -v ON_ERROR_STOP=1 -f db\update-current-reply-no-action-any-sender.sql
```

For production, use the production Azure PostgreSQL host and an Entra admin token/session. The full production baseline deploy still requires explicit confirmation:

```powershell
.\deploy-azure-postgres-prod.ps1 `
  -Subscription '<subscription-id-or-name>' `
  -AdminUser '<entra-admin-user>' `
  -ConfirmProduction
```

Optional quick verification after seeding:

```sql
select count(*) as assets from asset;
select ownership, destination from ownership order by ownership;
select asset_alias, asset_name, ownership, asset_type, address
from asset
order by asset_alias nulls last, asset_name
limit 20;
select asset_source, asset_lookup_id, asset_alias, asset_name, destination_code
from vw_asset_lookup
order by asset_source, asset_name
limit 20;
select config_key, config_value from runtime_config where config_key like 'property_match_%' order by config_key;
```

Generated live-database visibility artifacts are in `db/introspection/`:

- `schema-only.sql`
- `asset.schema.sql`
- `ownership.schema.sql`
- `asset-ownership.data.sql`

Run local processing from Graph Intake:

```powershell
python -m ap_automation.cli --source-intake --codex-skip-git-repo-check
```
