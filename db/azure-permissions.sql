-- Azure PostgreSQL runtime permissions for the AP Automation Function App.
-- Run with psql and pass: -v function_identity_name='id-hw-propertiesapmail-nonprod'

\set ON_ERROR_STOP on

select format('select pgaadauth_create_principal(%L, false, false);', :'function_identity_name')
where exists (
  select 1
  from pg_proc
  where proname = 'pgaadauth_create_principal'
)
and not exists (
  select 1
  from pg_roles
  where rolname = :'function_identity_name'
)
\gexec

select format('create role %I login;', :'function_identity_name')
where not exists (
  select 1
  from pg_proc
  where proname = 'pgaadauth_create_principal'
)
and not exists (
  select 1
  from pg_roles
  where rolname = :'function_identity_name'
)
\gexec

grant usage on schema public to :"function_identity_name";
grant select, insert, update, delete on all tables in schema public to :"function_identity_name";
grant usage, select, update on all sequences in schema public to :"function_identity_name";
alter default privileges in schema public grant select, insert, update, delete on tables to :"function_identity_name";
alter default privileges in schema public grant usage, select, update on sequences to :"function_identity_name";
