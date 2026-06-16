-- Remove obsolete ALC/Landscaping routing policy from existing databases.
-- Safe to rerun. Historical decisions/actions are preserved by disabling rows
-- before deleting only records with no foreign-key references.

delete from workflow_rule_conditions
where rule_code in ('alc_escalation', 'bill_to_alc');

update workflow_rules
set enabled = false,
    effective_end = coalesce(effective_end, current_date),
    updated_at = now()
where rule_code in ('alc_escalation', 'bill_to_alc');

delete from workflow_rules wr
where wr.rule_code in ('alc_escalation', 'bill_to_alc')
  and not exists (select 1 from decisions d where d.matched_rule_code = wr.rule_code)
  and not exists (select 1 from workflow_rule_versions wrv where wrv.rule_code = wr.rule_code);

update routing_destinations
set active = false,
    updated_at = now()
where destination_code in ('ESCALATE_ALC', 'MEDIUS_ALC');

delete from routing_destinations rd
where rd.destination_code in ('ESCALATE_ALC', 'MEDIUS_ALC')
  and not exists (select 1 from actions a where a.destination_code = rd.destination_code)
  and not exists (select 1 from decisions d where d.destination_code = rd.destination_code)
  and not exists (select 1 from workflow_rules wr where wr.destination_code = rd.destination_code)
  and not exists (select 1 from workflow_rule_versions wrv where wrv.destination_code = rd.destination_code)
  and not exists (select 1 from ownership o where o.destination = rd.destination_code)
  and not exists (select 1 from asset_custom ac where ac.destination_code = rd.destination_code);
