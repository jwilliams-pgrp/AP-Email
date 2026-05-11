-- Resolve imported property route hints to final routing destinations.
-- This is business data cleanup, not application logic.

insert into seed_batches (seed_batch_code, description, source_file, metadata)
values (
  'property_route_resolution_v1',
  'Resolve property routes to destination codes from reference deck and Medius Routing workbook.',
  'reference/AP_Inbox_Automation_Properties_Team_v4.pptx + reference/Medius Routing.xlsx',
  '{"environment":"LOCAL","source":"manual_reference_interpretation"}'::jsonb
)
on conflict (seed_batch_code) do update
set description = excluded.description,
    source_file = excluded.source_file,
    metadata = excluded.metadata;

-- Baseline: properties are Hillwood-owned unless explicitly listed as external PM,
-- Multifamily, or sold. The reference deck states Medius PROP is the default for
-- Hillwood-owned properties.
update properties
set ownership_type = 'hillwood_owned',
    management_type = 'internal',
    business_unit_code = coalesce(business_unit_code, 'PROP'),
    default_destination_code = 'MEDIUS_PROP',
    updated_at = now()
where active = true
  and coalesce(business_unit_code, '') <> 'MF'
  and is_sold = false;

update properties
set ownership_type = 'hillwood_owned',
    management_type = 'internal',
    business_unit_code = 'MF',
    default_destination_code = 'MEDIUS_MF',
    updated_at = now()
where property_code in ('4561', '6611', '6612', '6614', '6616', '6625', '6629');

-- External PM mapping from the reference deck:
-- Nuveen -> Tiffany Beck.
update properties
set ownership_type = 'investor_managed',
    management_type = 'external_pm',
    default_destination_code = 'PM_TIFFANY_BECK_NUVEEN',
    updated_at = now()
where property_code in (
  'ACC1', 'ACC2', 'ACC4',
  'GW9', 'GW15', 'GW22', 'GW23', 'GW27', 'GW49', 'GW52', 'GW58', 'GW62',
  'WP1', 'WP4', 'WP20'
);

-- Michele Fellers.
update properties
set ownership_type = 'investor_managed',
    management_type = 'external_pm',
    default_destination_code = 'PM_MICHELE_FELLERS',
    updated_at = now()
where property_code in (
  'GW1', 'GW2', 'GW11', 'GW14', 'GW17', 'GW18', 'GW20', 'GW21', 'GW26', 'GW31',
  'GW50', 'GW51', 'GW54', 'GW55', 'GW60',
  'HC2', 'HCX', 'TBN',
  'WP3', 'WP6', 'WP19'
);

-- Lex/NP -> Pattie McClean. The deck calls out Lex plus NP1 through NP5.
update properties
set ownership_type = 'investor_managed',
    management_type = 'external_pm',
    default_destination_code = 'PM_PATTIE_MCCLEAN_LEX_NP',
    updated_at = now()
where property_code in ('LEX', 'NP1', 'NP2', 'NP3', 'NP4', 'NP5');

-- Sold indicators from imported route labels should be handled by the sold-property
-- hard exception rule before normal routing.
update properties p
set is_sold = true,
    ownership_type = 'sold',
    management_type = 'sold',
    default_destination_code = 'REVIEW_QUEUE',
    updated_at = now()
from property_routes pr
where pr.property_id = p.property_id
  and pr.route_label ilike 'sold%';

-- Existing imported route rows inherit the property's resolved destination.
update property_routes pr
set destination_code = p.default_destination_code,
    updated_at = now()
from properties p
where pr.property_id = p.property_id
  and p.default_destination_code is not null;

-- Preserve destination subject-line instructions on route rows when the source row
-- did not already carry a more specific note.
update property_routes pr
set subject_instruction = coalesce(pr.subject_instruction, rd.subject_instruction),
    updated_at = now()
from routing_destinations rd
where pr.destination_code = rd.destination_code
  and rd.subject_instruction is not null;

-- Ensure every active property with a resolved destination has at least one route row.
insert into property_routes (
  property_id,
  destination_code,
  route_label,
  subject_instruction,
  source_sheet,
  source_row,
  seed_batch_code
)
select
  p.property_id,
  p.default_destination_code,
  'resolved_default',
  rd.subject_instruction,
  'route_resolution',
  null,
  'property_route_resolution_v1'
from properties p
left join routing_destinations rd
  on rd.destination_code = p.default_destination_code
where p.active = true
  and p.default_destination_code is not null
  and not exists (
    select 1
    from property_routes pr
    where pr.property_id = p.property_id
      and pr.destination_code = p.default_destination_code
  )
on conflict do nothing;
