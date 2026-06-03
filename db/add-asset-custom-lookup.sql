-- Adds custom direct-destination asset lookup rows and the combined lookup view.
-- Recreates asset_custom to match the approved table shape.

drop view if exists vw_asset_lookup;
drop table if exists asset_custom;

create table if not exists asset_custom (
  id bigserial primary key,
  asset_alias varchar(255),
  asset_name varchar(255) not null,
  address text,
  destination_code varchar(100),
  comment text,
  created_at timestamptz not null default current_timestamp
);

create index if not exists asset_custom_address_trgm_idx
  on asset_custom using gin (lower(coalesce(address, '')) gin_trgm_ops);

create index if not exists asset_custom_alias_trgm_idx
  on asset_custom using gin (lower(regexp_replace(coalesce(asset_alias, ''), '[^a-zA-Z0-9]+', '', 'g')) gin_trgm_ops);

create index if not exists asset_custom_destination_code_idx
  on asset_custom (destination_code);

create index if not exists asset_custom_name_trgm_idx
  on asset_custom using gin (lower(coalesce(asset_name, '')) gin_trgm_ops);

insert into routing_destinations (
  destination_code,
  display_name,
  email_address,
  parent_folder,
  label,
  send_teams_message,
  send_email
)
values (
  'ESCALATE_SPECIAL_ADDRESS',
  'SPECIAL-ADDRESS',
  null,
  'ESCALATE',
  'Special Address',
  false,
  false
)
on conflict (destination_code) do update
set display_name = excluded.display_name,
    email_address = excluded.email_address,
    parent_folder = excluded.parent_folder,
    label = excluded.label,
    send_teams_message = excluded.send_teams_message,
    send_email = excluded.send_email,
    updated_at = now();

create or replace view vw_asset_lookup as
select
  'asset'::text as asset_source,
  'asset:' || a.id::text as asset_lookup_id,
  a.id::text as source_id,
  a.asset_alias,
  a.asset_name,
  a.address,
  a.tenants,
  a.asset_type,
  a.ownership,
  a.market_name,
  a.market_area,
  null::text as comment,
  o.destination as destination_code,
  rd.active as destination_active,
  a.created_at
from asset a
left join ownership o on a.ownership = o.ownership
left join routing_destinations rd on rd.destination_code = o.destination
union all
select
  'asset_custom'::text as asset_source,
  'asset_custom:' || ac.id::text as asset_lookup_id,
  ac.id::text as source_id,
  ac.asset_alias,
  ac.asset_name,
  ac.address,
  null::text as tenants,
  null::varchar(100) as asset_type,
  null::varchar(255) as ownership,
  null::varchar(255) as market_name,
  null::varchar(255) as market_area,
  ac.comment,
  ac.destination_code::text as destination_code,
  rd.active as destination_active,
  ac.created_at
from asset_custom ac
left join routing_destinations rd on rd.destination_code = ac.destination_code::text;
