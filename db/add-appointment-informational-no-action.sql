-- Adds LLM-classified informational appointment notices to NO_ACTION routing.

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
    'appointment_informational_notice',
    'Appointment informational notice requires no action',
    116,
    true,
    'observed_fact',
    'DISCARD',
    'NO_ACTION',
    'LLM classified current email as informational appointment notice -> DISCARD',
    '2026-06-03',
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
  ('appointment_informational_notice', 'fact_key', '"indicates_informational_appointment_notice"'::jsonb),
  ('appointment_informational_notice', 'expected', 'true'::jsonb),
  ('appointment_informational_notice', 'document_types', '["unknown"]'::jsonb),
  ('appointment_informational_notice', 'blocked_flags', '["link_only_invoice", "missing_invoice_attachment", "vendor_inquiry", "wrong_destination", "past_due", "statement_or_account_summary", "ach_or_auto_draft", "ben_e_keith", "contract_or_pay_application", "lien_release_related", "conflicting_signals", "low_text_quality"]'::jsonb),
  ('appointment_informational_notice', 'forbid_source_attachments', 'true'::jsonb)
on conflict (rule_code, condition_key) do update
set condition_value = excluded.condition_value;
