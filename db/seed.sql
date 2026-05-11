-- Core local seed data.
-- Idempotent by design: safe to rerun during local development.

insert into seed_batches (seed_batch_code, description, source_file, metadata)
values
  (
    'core_local_v1',
    'Core local workflow configuration derived from project specs and reference deck.',
    'specs + AP_Inbox_Automation_Properties_Team_v4.pptx',
    '{"environment":"LOCAL","dry_run":true}'::jsonb
  )
on conflict (seed_batch_code) do update
set description = excluded.description,
    source_file = excluded.source_file,
    metadata = excluded.metadata;

insert into routing_destinations (
  destination_code,
  destination_type,
  display_name,
  email_address,
  folder_path,
  subject_instruction,
  seed_batch_code
)
values
  ('MEDIUS_PROP', 'email', 'Medius PROP Queue', 'medius.prop@hillwood.com', null, null, 'core_local_v1'),
  ('MEDIUS_ALC', 'email', 'Medius ALC Queue', 'Medius.ALC@hillwood.com', null, null, 'core_local_v1'),
  ('MEDIUS_MF', 'email', 'Medius Multifamily Queue', 'Medius.MF@hillwood.com', null, null, 'core_local_v1'),
  ('PM_TIFFANY_BECK_NUVEEN', 'email', 'Tiffany Beck - Nuveen', 'Tiffany.Beck@Hillwood.com', null, 'Note Nuveen in email subject line', 'core_local_v1'),
  ('PM_MICHELE_FELLERS', 'email', 'Michele Fellers', 'Michele.Fellers@Hillwood.com', null, null, 'core_local_v1'),
  ('PM_PATTIE_MCCLEAN_LEX_NP', 'email', 'Pattie McClean - Lex/NP', 'pattie.mcclean@hillwood.com', null, 'Note Lex or NP in email subject line when applicable', 'core_local_v1'),
  ('FOLDER_STATEMENTS', 'folder', 'Review Statement Folder', null, 'local/outbound/review-statements', null, 'core_local_v1'),
  ('FOLDER_ACH', 'folder', 'ACH Folder', null, 'local/outbound/ach', null, 'core_local_v1'),
  ('FOLDER_BEN_E_KEITH', 'folder', 'Ben E Keith Folder', null, 'local/outbound/ben-e-keith', null, 'core_local_v1'),
  ('FOLDER_LIEN_RELEASE', 'folder', 'Lien Release Folder', null, 'local/outbound/lien-release', null, 'core_local_v1'),
  ('REVIEW_QUEUE', 'review_queue', 'Human Review Queue', null, null, null, 'core_local_v1'),
  ('NO_ACTION', 'no_action', 'No External Action', null, null, null, 'core_local_v1')
on conflict (destination_code) do update
set destination_type = excluded.destination_type,
    display_name = excluded.display_name,
    email_address = excluded.email_address,
    folder_path = excluded.folder_path,
    subject_instruction = excluded.subject_instruction,
    seed_batch_code = excluded.seed_batch_code,
    updated_at = now();

insert into business_units (
  business_unit_code,
  name,
  category,
  default_destination_code,
  seed_batch_code
)
values
  ('PROP', 'Hillwood Properties', 'properties', 'MEDIUS_PROP', 'core_local_v1'),
  ('ALC', 'Alliance Landscape Company', 'alc', 'MEDIUS_ALC', 'core_local_v1'),
  ('MF', 'Multifamily', 'multifamily', 'MEDIUS_MF', 'core_local_v1')
on conflict (business_unit_code) do update
set name = excluded.name,
    category = excluded.category,
    default_destination_code = excluded.default_destination_code,
    seed_batch_code = excluded.seed_batch_code,
    updated_at = now();

insert into runtime_config (config_key, config_value, description, seed_batch_code)
values
  ('app_env', '"LOCAL"'::jsonb, 'Default local runtime environment.', 'core_local_v1'),
  ('dry_run', 'true'::jsonb, 'External actions are disabled by default.', 'core_local_v1'),
  ('confidence_threshold', '0.90'::jsonb, 'Minimum extraction confidence for automatic routing.', 'core_local_v1'),
  ('amount_review_threshold', '10000'::jsonb, 'Invoices above this amount are filed to lien release for hold by default.', 'core_local_v1'),
  ('statement_outcome', '"FILE"'::jsonb, 'Default local statement handling outcome.', 'core_local_v1'),
  ('default_review_destination', '"REVIEW_QUEUE"'::jsonb, 'Default destination for human review.', 'core_local_v1')
on conflict (config_key) do update
set config_value = excluded.config_value,
    description = excluded.description,
    seed_batch_code = excluded.seed_batch_code,
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
  version,
  seed_batch_code
)
values
  ('hard_multi_invoice_pdf', 'Multi-invoice PDF requires manual split', 100, true, 'document_flag', 'REVIEW', 'REVIEW_QUEUE', 'Attachment appears to contain multiple invoices -> REVIEW', '2026-05-07', 1, 'core_local_v1'),
  ('hard_invoice_plus_lien_waiver', 'Invoice plus lien waiver requires merge', 110, true, 'document_flag', 'REVIEW', 'REVIEW_QUEUE', 'Invoice and lien waiver require manual merge -> REVIEW', '2026-05-07', 1, 'core_local_v1'),
  ('hard_link_only_invoice', 'Link-only invoice requires review', 120, true, 'document_flag', 'REVIEW', 'REVIEW_QUEUE', 'Invoice is only available by link -> REVIEW', '2026-05-07', 1, 'core_local_v1'),
  ('hard_contract_or_pay_app', 'Contract or pay application requires review', 130, true, 'document_type', 'REVIEW', 'REVIEW_QUEUE', 'High-risk document type requires human review -> REVIEW', '2026-05-07', 1, 'core_local_v1'),
  ('hard_vendor_inquiry', 'Vendor question or payment inquiry requires review', 140, true, 'document_type', 'REVIEW', 'REVIEW_QUEUE', 'Vendor inquiry requires research or response -> REVIEW', '2026-05-07', 1, 'core_local_v1'),
  ('duplicate_candidate', 'Duplicate candidate requires review', 200, true, 'duplicate_check', 'REVIEW', 'REVIEW_QUEUE', 'Duplicate candidate found in audit history -> REVIEW', '2026-05-07', 1, 'core_local_v1'),
  ('sold_property', 'Sold property is flagged', 300, true, 'property_status', 'FLAG', 'REVIEW_QUEUE', 'Matched property is sold -> FLAG', '2026-05-07', 1, 'core_local_v1'),
  ('amount_over_threshold', 'Invoice amount over configured threshold', 400, true, 'amount_threshold', 'FILE', 'FOLDER_LIEN_RELEASE', 'Invoice amount exceeds configured threshold -> FILE to lien release folder; hold for lien release from Tiffany', '2026-05-07', 1, 'core_local_v1'),
  ('statement_file', 'Statement or account summary is filed', 500, true, 'document_type', 'FILE', 'FOLDER_STATEMENTS', 'Statement or account summary -> FILE', '2026-05-07', 1, 'core_local_v1'),
  ('ach_notice_file', 'ACH or auto-draft notice is filed', 520, true, 'document_type', 'FILE', 'FOLDER_ACH', 'ACH or auto-draft notice -> FILE', '2026-05-07', 1, 'core_local_v1'),
  ('ben_e_keith_notice_file', 'Ben E Keith notice is filed', 530, true, 'document_type', 'FILE', 'FOLDER_BEN_E_KEITH', 'Ben E Keith notice -> FILE', '2026-05-07', 1, 'core_local_v1'),
  ('bill_to_alc', 'ALC bill-to routes to Medius ALC', 600, true, 'bill_to_business_unit', 'AUTO', 'MEDIUS_ALC', 'Bill-to indicates ALC -> AUTO', '2026-05-07', 1, 'core_local_v1'),
  ('bill_to_mf', 'Multifamily bill-to routes to Medius MF', 610, true, 'bill_to_business_unit', 'AUTO', 'MEDIUS_MF', 'Bill-to indicates Multifamily -> AUTO', '2026-05-07', 1, 'core_local_v1'),
  ('property_routing_match', 'Property routing table match', 700, true, 'property_routing_match', 'AUTO', null, 'Property matched configured routing destination -> AUTO', '2026-05-07', 1, 'core_local_v1'),
  ('confidence_below_threshold', 'Low confidence requires review', 800, true, 'confidence_threshold', 'REVIEW', 'REVIEW_QUEUE', 'Confidence below configured threshold -> REVIEW', '2026-05-07', 1, 'core_local_v1'),
  ('fallback_review', 'No deterministic route matched', 900, true, 'fallback', 'REVIEW', 'REVIEW_QUEUE', 'No deterministic routing rule matched -> REVIEW', '2026-05-07', 1, 'core_local_v1')
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
    seed_batch_code = excluded.seed_batch_code,
    updated_at = now();

insert into workflow_rule_conditions (rule_code, condition_key, condition_value)
values
  ('hard_multi_invoice_pdf', 'flag', '"multi_invoice_pdf"'::jsonb),
  ('hard_invoice_plus_lien_waiver', 'flag', '"invoice_plus_lien_waiver"'::jsonb),
  ('hard_link_only_invoice', 'flag', '"link_only_invoice"'::jsonb),
  ('hard_contract_or_pay_app', 'document_types', '["contract", "pay_application"]'::jsonb),
  ('hard_vendor_inquiry', 'document_types', '["vendor_question", "payment_inquiry", "past_due_notice"]'::jsonb),
  ('duplicate_candidate', 'duplicate_statuses', '["candidate", "suspected"]'::jsonb),
  ('sold_property', 'is_sold', 'true'::jsonb),
  ('amount_over_threshold', 'runtime_config_key', '"amount_review_threshold"'::jsonb),
  ('statement_file', 'document_types', '["statement", "account_summary"]'::jsonb),
  ('ach_notice_file', 'document_types', '["ach_notice", "auto_draft_notice"]'::jsonb),
  ('ben_e_keith_notice_file', 'document_types', '["ben_e_keith_notice"]'::jsonb),
  ('bill_to_alc', 'business_unit_code', '"ALC"'::jsonb),
  ('bill_to_mf', 'business_unit_code', '"MF"'::jsonb),
  ('property_routing_match', 'requires_property_route', 'true'::jsonb),
  ('confidence_below_threshold', 'runtime_config_key', '"confidence_threshold"'::jsonb),
  ('fallback_review', 'always', 'true'::jsonb)
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
where wr.seed_batch_code = 'core_local_v1'
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
