-- Adds dedicated escalation routing for standalone contractor timesheets with no invoice item.

insert into routing_destinations (
  destination_code,
  display_name,
  email_address,
  parent_folder,
  label,
  send_teams_message,
  send_email
)
values
  (
    'ESCALATE_CONTRACTOR_TIMESHEET',
    'CONTRACTOR-TIMESHEET',
    null,
    'ESCALATE',
    'Contractor Timesheet',
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
values
  (
    'hard_contractor_timesheet_no_invoice',
    'Contractor timesheet without invoice requires escalation',
    125,
    true,
    'document_flag',
    'ESCALATE',
    'ESCALATE_CONTRACTOR_TIMESHEET',
    'Contractor timesheet or time-detail document has no invoice in the run -> ESCALATE with CONTRACTOR-TIMESHEET label',
    '2026-06-04',
    1
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

insert into workflow_rule_conditions (rule_code, condition_key, condition_value)
values
  ('hard_contractor_timesheet_no_invoice', 'flag', '"contractor_timesheet_no_invoice"'::jsonb)
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
where wr.rule_code = 'hard_contractor_timesheet_no_invoice'
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
