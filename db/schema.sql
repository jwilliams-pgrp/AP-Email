-- AP Automation local schema.
-- Idempotent by design: safe to rerun during local development.

create extension if not exists pgcrypto;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'decision_outcome') then
    create type decision_outcome as enum ('AUTO', 'REVIEW', 'FILE', 'FLAG', 'DISCARD');
  end if;

  if not exists (select 1 from pg_type where typname = 'audit_step_type') then
    create type audit_step_type as enum (
      'INGESTION',
      'ATTACHMENT_PROCESSING',
      'LLM_EXTRACTION',
      'VALIDATION',
      'DUPLICATE_CHECK',
      'ROUTING_MATCH',
      'RULE_EVALUATION',
      'DECISION',
      'ACTION',
      'FINALIZE'
    );
  end if;
end $$;

create table if not exists seed_batches (
  seed_batch_code text primary key,
  description text not null,
  source_file text,
  source_hash text,
  imported_by text not null default current_user,
  imported_at timestamptz not null default now(),
  metadata jsonb not null default '{}'::jsonb
);

create table if not exists runtime_config (
  config_key text primary key,
  config_value jsonb not null,
  description text not null,
  seed_batch_code text references seed_batches(seed_batch_code),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists routing_destinations (
  destination_code text primary key,
  destination_type text not null,
  display_name text not null,
  email_address text,
  folder_path text,
  subject_instruction text,
  active boolean not null default true,
  seed_batch_code text references seed_batches(seed_batch_code),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint routing_destinations_target_check check (
    email_address is not null or folder_path is not null or destination_type in ('review_queue', 'no_action')
  )
);

create table if not exists business_units (
  business_unit_code text primary key,
  name text not null,
  category text not null,
  cost_center text,
  default_destination_code text references routing_destinations(destination_code),
  active boolean not null default true,
  seed_batch_code text references seed_batches(seed_batch_code),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists properties (
  property_id uuid primary key default gen_random_uuid(),
  property_code text not null unique,
  property_name text,
  cost_center text,
  ownership_type text not null default 'unknown',
  management_type text not null default 'unknown',
  business_unit_code text references business_units(business_unit_code),
  default_destination_code text references routing_destinations(destination_code),
  is_sold boolean not null default false,
  active boolean not null default true,
  seed_batch_code text references seed_batches(seed_batch_code),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists property_aliases (
  alias_id uuid primary key default gen_random_uuid(),
  property_id uuid not null references properties(property_id) on delete cascade,
  alias_type text not null,
  alias_value text not null,
  source_sheet text,
  source_row integer,
  seed_batch_code text references seed_batches(seed_batch_code),
  created_at timestamptz not null default now(),
  unique (alias_type, alias_value)
);

create index if not exists property_aliases_value_lower_idx
  on property_aliases (lower(alias_value));

create or replace function coalesce_text(value text)
returns text
language sql
immutable
as $$
  select coalesce(value, '')
$$;

create table if not exists property_routes (
  property_route_id uuid primary key default gen_random_uuid(),
  property_id uuid not null references properties(property_id) on delete cascade,
  destination_code text references routing_destinations(destination_code),
  route_label text,
  subject_instruction text,
  source_sheet text,
  source_row integer,
  seed_batch_code text references seed_batches(seed_batch_code),
  active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index if not exists property_routes_unique_idx
  on property_routes (property_id, coalesce_text(route_label), coalesce_text(destination_code));

create table if not exists reference_rows (
  reference_row_id uuid primary key default gen_random_uuid(),
  source_file text not null,
  source_sheet text not null,
  source_row integer not null,
  row_data jsonb not null,
  seed_batch_code text references seed_batches(seed_batch_code),
  imported_at timestamptz not null default now(),
  unique (source_file, source_sheet, source_row)
);

create table if not exists workflow_rules (
  rule_code text primary key,
  rule_name text not null,
  priority integer not null,
  enabled boolean not null default true,
  condition_type text not null,
  outcome decision_outcome not null,
  destination_code text references routing_destinations(destination_code),
  reason_template text not null,
  effective_start date not null default current_date,
  effective_end date,
  version integer not null default 1,
  seed_batch_code text references seed_batches(seed_batch_code),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint workflow_rules_effective_dates_check check (effective_end is null or effective_end >= effective_start)
);

create index if not exists workflow_rules_active_idx
  on workflow_rules (enabled, priority, effective_start, effective_end);

create table if not exists workflow_rule_conditions (
  condition_id uuid primary key default gen_random_uuid(),
  rule_code text not null references workflow_rules(rule_code) on delete cascade,
  condition_key text not null,
  condition_value jsonb not null,
  created_at timestamptz not null default now(),
  unique (rule_code, condition_key)
);

create table if not exists management_audit_events (
  management_audit_event_id uuid primary key default gen_random_uuid(),
  changed_table text not null,
  changed_key text not null,
  change_type text not null,
  old_value jsonb,
  new_value jsonb not null,
  changed_by text not null default current_user,
  changed_at timestamptz not null default now(),
  reason text,
  request_metadata jsonb not null default '{}'::jsonb
);

create index if not exists management_audit_events_lookup_idx
  on management_audit_events (changed_table, changed_key, changed_at desc);

create table if not exists workflow_rule_versions (
  rule_version_id uuid primary key default gen_random_uuid(),
  rule_code text not null references workflow_rules(rule_code),
  version integer not null,
  rule_name text not null,
  priority integer not null,
  enabled boolean not null,
  condition_type text not null,
  condition_snapshot jsonb not null default '{}'::jsonb,
  outcome decision_outcome not null,
  destination_code text references routing_destinations(destination_code),
  reason_template text not null,
  effective_start date not null,
  effective_end date,
  management_audit_event_id uuid references management_audit_events(management_audit_event_id),
  created_at timestamptz not null default now(),
  constraint workflow_rule_versions_effective_dates_check check (effective_end is null or effective_end >= effective_start),
  unique (rule_code, version)
);

create index if not exists workflow_rule_versions_lookup_idx
  on workflow_rule_versions (rule_code, version);

create table if not exists emails (
  email_id uuid primary key default gen_random_uuid(),
  source_system text not null default 'local_file',
  source_message_id text not null,
  idempotency_key text not null unique,
  subject text,
  sender_email text,
  received_at timestamptz,
  raw_storage_path text,
  html_storage_path text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

alter table emails
  add column if not exists html_storage_path text;

create table if not exists attachments (
  attachment_id uuid primary key default gen_random_uuid(),
  email_id uuid not null references emails(email_id) on delete cascade,
  file_name text not null,
  content_type text,
  storage_path text not null,
  file_size_bytes bigint,
  sha256 text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create unique index if not exists attachments_email_path_hash_idx
  on attachments (email_id, storage_path, sha256);

create table if not exists extractions (
  extraction_id uuid primary key default gen_random_uuid(),
  email_id uuid not null references emails(email_id) on delete cascade,
  extractor_type text not null,
  model_name text,
  prompt_version text,
  raw_output jsonb,
  parsed_output jsonb not null,
  confidence numeric(5,4),
  validation_status text not null,
  validation_errors jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists audit_runs (
  run_id uuid primary key default gen_random_uuid(),
  email_id uuid references emails(email_id),
  status text not null,
  started_at timestamptz not null default now(),
  completed_at timestamptz,
  final_outcome decision_outcome,
  trace_artifact_path text,
  metadata jsonb not null default '{}'::jsonb
);

create table if not exists audit_steps (
  step_id uuid primary key default gen_random_uuid(),
  run_id uuid not null references audit_runs(run_id) on delete cascade,
  sequence_number integer not null,
  step_type audit_step_type not null,
  input_summary jsonb not null default '{}'::jsonb,
  output_summary jsonb not null default '{}'::jsonb,
  decision jsonb,
  reason text,
  confidence numeric(5,4),
  error text,
  created_at timestamptz not null default now(),
  unique (run_id, sequence_number)
);

create table if not exists decisions (
  decision_id uuid primary key default gen_random_uuid(),
  email_id uuid not null references emails(email_id) on delete cascade,
  run_id uuid references audit_runs(run_id),
  outcome decision_outcome not null,
  destination_code text references routing_destinations(destination_code),
  destination_email text,
  reason text not null,
  confidence numeric(5,4),
  matched_rule_code text references workflow_rules(rule_code),
  matched_rule_version integer,
  extracted_fields jsonb not null default '{}'::jsonb,
  routing_match jsonb not null default '{}'::jsonb,
  dry_run boolean not null default true,
  created_at timestamptz not null default now()
);

create table if not exists actions (
  action_id uuid primary key default gen_random_uuid(),
  email_id uuid not null references emails(email_id) on delete cascade,
  decision_id uuid references decisions(decision_id),
  action_type text not null,
  destination_code text references routing_destinations(destination_code),
  dry_run boolean not null default true,
  status text not null,
  external_reference text,
  reason text,
  created_at timestamptz not null default now(),
  completed_at timestamptz
);

create table if not exists review_queue (
  review_id uuid primary key default gen_random_uuid(),
  email_id uuid not null references emails(email_id) on delete cascade,
  decision_id uuid references decisions(decision_id),
  status text not null default 'open',
  priority text not null default 'normal',
  reason text not null,
  assigned_to text,
  created_at timestamptz not null default now(),
  resolved_at timestamptz
);
