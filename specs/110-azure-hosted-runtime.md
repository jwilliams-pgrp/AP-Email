# 110 - Azure Hosted Runtime Spec

## Purpose

Define the Azure-hosted runtime for AP Automation while preserving the existing `LOCAL` runtime for development, tests, fixtures, and debugging.

The Azure migration must not change AP business behavior. The same deterministic decision engine, workflow tables, routing hierarchy, allowed outcomes, extraction validation, duplicate protection, action logging, and audit records must be used in `LOCAL` and `AZURE`.

## Target Architecture

Azure-hosted runtime uses:
- Azure Static Web Apps for the React operations dashboard.
- Microsoft Entra SSO in front of the Static Web App.
- Azure Functions on Flex Consumption for the dashboard API and AP processing entrypoints.
- Azure Logic Apps as the hosted intake scheduler.
- Azure Database for PostgreSQL Flexible Server as the source of truth.
- Azure Blob Storage for raw emails, attachments, processed artifacts, and audit artifacts.
- Azure AI Foundry / Azure OpenAI for LLM extraction.
- Azure Document Intelligence for selected attachment analysis.
- Azure Key Vault only for unavoidable secrets that cannot use RBAC.
- Application Insights and Log Analytics for runtime telemetry.

## Runtime Modes

Supported modes:
- `APP_ENV=LOCAL`
- `APP_ENV=AZURE`

`LOCAL` requirements:
- May use local Postgres or an explicitly configured development Postgres DSN.
- May use local filesystem artifact storage.
- May use test fixtures.
- Must not require committed secrets or hard-coded passwords.

`AZURE` requirements:
- Must use Azure-hosted Postgres.
- Must use Blob Storage for artifacts.
- Must use managed identity or Microsoft Entra authentication for Azure resource access wherever supported.
- Must load unavoidable secrets from Key Vault by secret name only.
- Must not rely on local filesystem artifact paths for persisted operational artifacts.

## Identity and Secret Handling

Azure resource communication must use RBAC and managed identity wherever supported.

Required identity behavior:
- Function App managed identity reads and writes Blob artifacts.
- Function App managed identity reads Key Vault secrets only when a secret is unavoidable.
- Function App uses identity-first credentials for Azure OpenAI / Foundry and Document Intelligence where supported by the SDK/API.
- Function App uses Microsoft Entra authentication for Azure Postgres where supported by the deployment.
- Function App currently uses `GRAPH_AUTH_MODE=client_secret` for Microsoft Graph mailbox access with `AZURE_CLIENT_ID_MAIL`, `AZURE_TENANT_ID`, `AZURE_CLIENT_SECRET_MAIL`, and `USER_PRINCIPAL_NAME_MAIL` app settings. Graph mailbox credentials must be exposed to the Function App only through Key Vault references for `AZURE-CLIENT-ID-MAIL`, `AZURE-TENANT-ID`, and `AZURE-CLIENT-SECRET-MAIL`. Local development may continue to use the existing hyphenated `.env` names.
- Function App Teams webhook configuration must expose `TEAMS_WEBHOOK_URL_PROPERTIES_AP` only through a Key Vault reference to the `TEAMS-WEBHOOK-URL-PROPERTIES-AP` secret. Non-secret Teams team and channel names may be provided as deployment parameters and exposed through Azure-safe underscored app setting names.
- Azure nonprod Postgres must be initialized through `deploy-azure-postgres-nonprod.ps1`, which applies `db/schema.sql`, `db/seed.sql`, and `db/azure-permissions.sql`.
- Static Web App requires Microsoft Entra authentication and assignment to the custom SWA `user` role before users can access the dashboard.

Repository-pushed files must not contain:
- real passwords
- API keys
- client secrets
- webhook URLs
- production connection strings
- mailbox secrets

Placeholders and secret names are allowed.

## Dashboard and API Security

The dashboard API must reject unauthenticated hosted requests.

In Azure:
- SWA must require authenticated Microsoft Entra users assigned the custom SWA `user` role for dashboard routes and `/api/*`.
- SWA protected routes must not allow the broad built-in `authenticated` role.
- API requests must include authenticated SWA identity headers.
- Missing or invalid identity must return explicit `401` or `403`.

In local development:
- API authentication may be bypassed only when `APP_ENV=LOCAL`.
- Local auth bypass must be explicit in runtime configuration and must never be the Azure default.

## Intake Scheduling

In `AZURE`, the Outlook inbox processing loop is triggered by Logic Apps.

Required behavior:
- The Logic App must run on a 30-second recurrence by default.
- The Logic App recurrence trigger must limit concurrent runs to 1.
- Each recurrence must call the Function App intake endpoint.
- Each Function invocation must pull and process at most one available Outlook inbox email.
- Repeated recurrences continue processing the inbox until no message is available.
- The Function endpoint must reject unauthorized callers in Azure.
- Disabling intake must be controlled by deployment configuration, not code changes.

## Artifact Storage

Artifact access must go through the configured artifact store.

In `LOCAL`:
- Local artifact paths must remain constrained to approved local artifact roots.

In `AZURE`:
- Artifact references persisted in Postgres must identify Blob artifacts.
- API artifact endpoints must resolve artifacts through Blob Storage and must not expose arbitrary filesystem paths.
- Missing, unlinked, or unauthorized artifacts must fail loudly.

## Infrastructure as Code

Azure infrastructure must be defined with Bicep.

The Bicep deployment must include:
- Static Web App
- Azure Function App(s) on Flex Consumption
- Function managed identity
- Storage Account and Blob containers
- Azure Database for PostgreSQL Flexible Server
- Key Vault
- Application Insights
- Log Analytics workspace
- RBAC assignments required by the Function identity

Parameter files must contain placeholders only.

## Acceptance Criteria

- `APP_ENV=LOCAL` and `APP_ENV=AZURE` select distinct runtime configuration without changing business logic.
- Existing golden scenario outcomes remain unchanged.
- Azure runtime stores artifacts in Blob Storage.
- Local runtime can still run tests and fixture processing without Azure services.
- Dashboard API rejects unauthenticated Azure requests.
- SWA configuration requires Microsoft Entra authentication and the custom SWA `user` role for dashboard routes and `/api/*`.
- SWA deployment runs from the built dashboard output directory with the committed SWA config file included, without scanning repository cache or generated-output directories.
- Function App deployment packages must include the local `ap_automation` wheel as a root-relative `wheels/...` requirement that remote Linux builds can install.
- Azure clients prefer managed identity / RBAC over static secrets except Microsoft Graph mailbox access, which currently uses the documented Entra client-secret path through Key Vault references.
- Graph mailbox credentials and Teams webhook URL are exposed to the Function App only through Key Vault references.
- Azure nonprod database deployment applies the replayable schema and seed baseline and grants runtime access to the Function App managed identity.
- Bicep validates successfully.
- Logic App recurrence calls the Function intake endpoint every 30 seconds by default.
- Logic App recurrence does not start overlapping intake runs.
- Function intake invocation returns an explicit empty/disabled/processed status.
- Secret hygiene tests fail if tracked source or docs contain obvious real secrets.
