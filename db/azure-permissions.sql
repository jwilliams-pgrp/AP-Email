-- Azure PostgreSQL runtime permissions for the AP Automation Function App.
-- Register the managed identity as an Entra principal in the postgres database before running this file.
-- Run with psql against the application database and pass: -v function_identity_name='id-hw-propertiesapmail-nonprod'

\set ON_ERROR_STOP on

select format('select 1 from pg_roles where rolname = %L;', :'function_identity_name')
where exists (
  select 1
  from pg_roles
  where rolname = :'function_identity_name'
)
\gexec

select format('missing required Azure Postgres Entra role: %s', :'function_identity_name') as error
where not exists (
  select 1
  from pg_roles
  where rolname = :'function_identity_name'
)
\gset

grant usage on schema public to :"function_identity_name";
grant select, insert, update, delete on all tables in schema public to :"function_identity_name";
grant usage, select, update on all sequences in schema public to :"function_identity_name";
alter default privileges in schema public grant select, insert, update, delete on tables to :"function_identity_name";
alter default privileges in schema public grant usage, select, update on sequences to :"function_identity_name";
