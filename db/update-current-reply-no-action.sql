



select *
from workflow_rules

update workflow_rules
set priority = 119
where rule_code = 'hard_past_due_notice'

-- Adds thread-aware no-action routing for short current replies.
-- This targeted delta intentionally changes only workflow policy/config rows.

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
  'hard_current_reply_no_action',
  'Short current reply requires no AP action',
  114,
  true,
  'current_reply_no_action',
  'DISCARD',
  'NO_ACTION',
  'Short current reply contains acknowledgement or social reply only -> DISCARD',
  '2026-06-02',
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
  ('hard_current_reply_no_action', 'max_chars', '320'::jsonb),
  ('hard_current_reply_no_action', 'require_quoted_history', 'true'::jsonb)
on conflict (rule_code, condition_key) do update
set condition_value = excluded.condition_value;

delete from workflow_rule_conditions
where rule_code = 'hard_current_reply_no_action'
  and condition_key = 'allowed_sender_domains';

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
where wr.rule_code = 'hard_current_reply_no_action'
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
