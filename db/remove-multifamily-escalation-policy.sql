-- Remove obsolete multifamily escalation policy from existing databases.
-- Safe to rerun. Historical decisions/actions are preserved by disabling rows
-- before deleting only records with no foreign-key references.

insert into workflow_rules (
  rule_code,
  rule_name,
  priority,
  enabled,
  condition_type,
  outcome,
  destination_code,
  reason_template,
  effective_start,
  version
)
values (
  'asset_type_multifamily',
  'Multifamily asset routes to Medius MF',
  375,
  true,
  'property_asset_type',
  'AUTO',
  'MEDIUS_MF',
  'Matched asset type is Multifamily -> AUTO to Medius MF',
  '2026-06-11',
  3
)
on conflict (rule_code) do update
set rule_name = excluded.rule_name,
    priority = excluded.priority,
    enabled = excluded.enabled,
    condition_type = excluded.condition_type,
    outcome = excluded.outcome,
    destination_code = excluded.destination_code,
    reason_template = excluded.reason_template,
    effective_start = excluded.effective_start,
    version = excluded.version,
    updated_at = now();

delete from workflow_rule_conditions
where rule_code = 'asset_type_multifamily';

insert into workflow_rule_conditions (rule_code, condition_key, condition_value)
values
  ('asset_type_multifamily', 'asset_type', '"Multifamily"'::jsonb),
  ('asset_type_multifamily', 'document_types', '["invoice"]'::jsonb)
on conflict (rule_code, condition_key) do update
set condition_value = excluded.condition_value;

insert into workflow_rule_versions (
  rule_code,
  version,
  rule_name,
  priority,
  enabled,
  condition_type,
  condition_snapshot,
  outcome,
  destination_code,
  reason_template,
  effective_start,
  effective_end
)
select
  wr.rule_code,
  wr.version,
  wr.rule_name,
  wr.priority,
  wr.enabled,
  wr.condition_type,
  coalesce(
    jsonb_object_agg(wrc.condition_key, wrc.condition_value) filter (where wrc.condition_key is not null),
    '{}'::jsonb
  ) as condition_snapshot,
  wr.outcome,
  wr.destination_code,
  wr.reason_template,
  wr.effective_start,
  wr.effective_end
from workflow_rules wr
left join workflow_rule_conditions wrc on wrc.rule_code = wr.rule_code
where wr.rule_code = 'asset_type_multifamily'
group by wr.rule_code
on conflict (rule_code, version) do update
set rule_name = excluded.rule_name,
    priority = excluded.priority,
    enabled = excluded.enabled,
    condition_type = excluded.condition_type,
    condition_snapshot = excluded.condition_snapshot,
    outcome = excluded.outcome,
    destination_code = excluded.destination_code,
    reason_template = excluded.reason_template,
    effective_start = excluded.effective_start,
    effective_end = excluded.effective_end;

update routing_destinations
set active = false,
    updated_at = now()
where destination_code = 'ESCALATE_MULTIFAMILY';

delete from routing_destinations rd
where rd.destination_code = 'ESCALATE_MULTIFAMILY'
  and not exists (select 1 from actions a where a.destination_code = rd.destination_code)
  and not exists (select 1 from decisions d where d.destination_code = rd.destination_code)
  and not exists (select 1 from workflow_rules wr where wr.destination_code = rd.destination_code)
  and not exists (select 1 from workflow_rule_versions wrv where wrv.destination_code = rd.destination_code)
  and not exists (select 1 from ownership o where o.destination = rd.destination_code)
  and not exists (select 1 from asset_custom ac where ac.destination_code = rd.destination_code);
