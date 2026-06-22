-- Allow Word attachments to flow through normal document routing.
-- Run this file alone against an existing database that already has the
-- workflow_rules and workflow_rule_conditions tables.

begin;

update workflow_rules
set rule_name = 'Image or Excel attachment requires escalation',
    updated_at = now()
where rule_code = 'hard_wrong_file_type';

insert into workflow_rule_conditions (rule_code, condition_key, condition_value)
values (
  'hard_wrong_file_type',
  'disallowed_extensions',
  '[".jpg", ".jpeg", ".png", ".xls", ".xlsx"]'::jsonb
)
on conflict (rule_code, condition_key) do update
set condition_value = excluded.condition_value,
    updated_at = now();

commit;
