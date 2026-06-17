# Azure Deployment

This folder contains the Bicep deployment for the Azure hosted runtime.

Resource names are controlled by parameter files:

- `main.parameters.nonprod.json` targets existing resource group `rg-hw-propertiesapmail-nonprod` and existing Key Vault `kv-hw-propapmail-nonprod`.
- `main.parameters.prod.json` contains the PROD naming pattern and assumes the named PROD Key Vault already exists.
- `main.parameters.example.json` is a placeholder template.

Deploy NONPROD:

```powershell
.\deploy-azure-nonprod.ps1
```

Preview NONPROD changes without applying them:

```powershell
.\deploy-azure-nonprod.ps1 -WhatIf
```

For PROD, preview first:

```powershell
.\deploy-azure-prod.ps1 -WhatIf
```

Then deploy with explicit production confirmation:

```powershell
.\deploy-azure-prod.ps1 -ConfirmProduction
```

Both scripts accept `-Subscription <subscription-id-or-name>` and `-ValidateOnly`.

The Logic App is the intake scheduler. It runs every 30 seconds by default and calls the Function App `POST /api/process-graph-intake` endpoint. That Function invocation processes at most one Outlook inbox message per call; the next Logic App recurrence picks up the next message.

The Function App HTTP surface is protected by App Service Authentication / EasyAuth. Each environment needs one Microsoft Entra app registration for the Function API audience. NONPROD uses:

- client id: `fef14fc0-c4f9-4c97-b900-8f31d44681c0`
- allowed audience: `api://fef14fc0-c4f9-4c97-b900-8f31d44681c0`

Set those values with `functionAuthClientId` and `functionAuthAllowedAudience` in the environment parameter file. Do not create or store a client secret for this Function API app registration.

Do not commit real secrets, database passwords, client secrets, or webhook URLs. Parameter files may contain resource names, placeholder object IDs, mailbox/model values, and non-secret Teams team/channel names only.

The Function App receives Graph mailbox credentials and the Properties AP Teams webhook through Key Vault references. Before deploying the Function App, create these secrets in the environment Key Vault:

- `AZURE-CLIENT-ID-MAIL`
- `AZURE-CLIENT-SECRET-MAIL`
- `AZURE-TENANT-ID`
- `TEAMS-WEBHOOK-URL-PROPERTIES-AP`

The Teams webhook app setting uses the Azure-safe name `TEAMS_WEBHOOK_URL_PROPERTIES_AP` and references the `TEAMS-WEBHOOK-URL-PROPERTIES-AP` Key Vault secret. The Teams notification destination names are not secrets. Set them with the `teamsTeamNamePropertiesAp` and `teamsChannelNamePropertiesAp` deployment parameters; they are exposed as `TEAMS_TEAM_NAME_PROPERTIES_AP` and `TEAMS_CHANNEL_NAME_PROPERTIES_AP`.

After PostgreSQL Flexible Server is deployed with Microsoft Entra authentication, connect as the configured Entra administrator and create the database role for the Function managed identity before processing mail:

```sql
select * from pgaadauth_create_principal('<function-managed-identity-name>', false, false);
grant connect on database apautomation to "<function-managed-identity-name>";
grant usage on schema public to "<function-managed-identity-name>";
grant select, insert, update, delete on all tables in schema public to "<function-managed-identity-name>";
grant usage, select, update on all sequences in schema public to "<function-managed-identity-name>";
```

Schema and seed data remain owned by `db/schema.sql`, `db/seed.sql`, and `db/SCHEMA.md`.
