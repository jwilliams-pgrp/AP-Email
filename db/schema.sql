--
-- PostgreSQL database dump
--

\restrict E1GyjeUZqbHlvfeeHugPAR50EHeMSOnQktnm6Y9KAoROAVTYwP6XNewDfRqoUJP

-- Dumped from database version 18.3
-- Dumped by pg_dump version 18.3

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: public; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA IF NOT EXISTS public;


--
-- Name: SCHEMA public; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA public IS 'standard public schema';

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;
CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;


--
-- Name: coalesce_text(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.coalesce_text(value text) RETURNS text
    LANGUAGE sql IMMUTABLE
    AS $$
  select coalesce(value, '')
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: actions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.actions (
    action_id uuid DEFAULT gen_random_uuid() NOT NULL,
    email_id uuid NOT NULL,
    decision_id uuid,
    action_type text NOT NULL,
    destination_code text,
    status text NOT NULL,
    external_reference text,
    reason text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    document_item_id uuid
);


--
-- Name: asset; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.asset (
    id bigint NOT NULL,
    asset_name character varying(255) NOT NULL,
    ownership character varying(255),
    asset_type character varying(100),
    asset_alias character varying(255),
    market_name character varying(255),
    market_area character varying(255),
    tenants text,
    address text,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


--
-- Name: asset_custom; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.asset_custom (
    id bigint NOT NULL,
    asset_alias character varying(255),
    asset_name character varying(255) NOT NULL,
    address text,
    destination_code character varying(100),
    comment text,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


--
-- Name: asset_custom_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.asset_custom_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: asset_custom_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.asset_custom_id_seq OWNED BY public.asset_custom.id;


--
-- Name: asset_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.asset_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: asset_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.asset_id_seq OWNED BY public.asset.id;


--
-- Name: attachments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.attachments (
    attachment_id uuid DEFAULT gen_random_uuid() NOT NULL,
    email_id uuid NOT NULL,
    file_name text NOT NULL,
    content_type text,
    storage_path text NOT NULL,
    file_size_bytes bigint,
    sha256 text,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: audit_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_runs (
    run_id uuid DEFAULT gen_random_uuid() NOT NULL,
    email_id uuid,
    status text NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    final_outcome text,
    trace_artifact_path text,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL
);


--
-- Name: audit_steps; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_steps (
    step_id uuid DEFAULT gen_random_uuid() NOT NULL,
    run_id uuid NOT NULL,
    sequence_number integer NOT NULL,
    step_type text NOT NULL,
    input_summary jsonb DEFAULT '{}'::jsonb NOT NULL,
    output_summary jsonb DEFAULT '{}'::jsonb NOT NULL,
    decision jsonb,
    reason text,
    confidence numeric(5,4),
    error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: decisions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.decisions (
    decision_id uuid DEFAULT gen_random_uuid() NOT NULL,
    email_id uuid NOT NULL,
    run_id uuid,
    outcome text NOT NULL,
    destination_code text,
    destination_email text,
    reason text NOT NULL,
    confidence numeric(5,4),
    matched_rule_code text,
    matched_rule_version integer,
    extracted_fields jsonb DEFAULT '{}'::jsonb NOT NULL,
    routing_match jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    document_item_id uuid
);


--
-- Name: document_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.document_items (
    document_item_id uuid DEFAULT gen_random_uuid() NOT NULL,
    email_id uuid NOT NULL,
    item_kind text NOT NULL,
    attachment_id uuid,
    item_key text NOT NULL,
    display_name text,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT document_items_kind_check CHECK ((item_kind = ANY (ARRAY['attachment'::text, 'email'::text])))
);


--
-- Name: emails; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.emails (
    email_id uuid DEFAULT gen_random_uuid() NOT NULL,
    source_system text DEFAULT 'local_file'::text NOT NULL,
    source_message_id text NOT NULL,
    idempotency_key text NOT NULL,
    subject text,
    sender_email text,
    received_at timestamp with time zone,
    raw_storage_path text,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    html_storage_path text,
    office_web_link text
);


--
-- Name: escalate_queue; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.escalate_queue (
    escalate_id uuid DEFAULT gen_random_uuid() NOT NULL,
    email_id uuid NOT NULL,
    decision_id uuid,
    status text DEFAULT 'open'::text NOT NULL,
    priority text DEFAULT 'normal'::text NOT NULL,
    reason text NOT NULL,
    assigned_to text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    resolved_at timestamp with time zone,
    source_message_id text,
    office_web_link text,
    last_seen_in_escalate_at timestamp with time zone,
    active boolean DEFAULT true NOT NULL,
    document_item_id uuid
);


--
-- Name: extractions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.extractions (
    extraction_id uuid DEFAULT gen_random_uuid() NOT NULL,
    email_id uuid NOT NULL,
    extractor_type text NOT NULL,
    model_name text,
    prompt_version text,
    raw_output jsonb,
    parsed_output jsonb NOT NULL,
    confidence numeric(5,4),
    validation_status text NOT NULL,
    validation_errors jsonb DEFAULT '[]'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    document_item_id uuid
);


--
-- Name: invoices; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.invoices (
    invoice_id uuid DEFAULT gen_random_uuid() NOT NULL,
    email_id uuid NOT NULL,
    vendor_name text,
    invoice_number text,
    invoice_date date,
    amount numeric(12,2),
    currency text,
    duplicate_fingerprint text,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    document_item_id uuid
);


--
-- Name: llm_interactions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.llm_interactions (
    llm_interaction_id uuid DEFAULT gen_random_uuid() NOT NULL,
    email_id uuid NOT NULL,
    run_id uuid NOT NULL,
    step_id uuid,
    extraction_id uuid,
    interaction_type text NOT NULL,
    provider text NOT NULL,
    model_name text,
    deployment_name text,
    api_version text,
    prompt_template_name text,
    prompt_version text,
    prompt_artifact_path text,
    response_artifact_path text,
    request_parameters jsonb DEFAULT '{}'::jsonb NOT NULL,
    prompt_tokens integer,
    completion_tokens integer,
    total_tokens integer,
    cached_prompt_tokens integer,
    reasoning_tokens integer,
    raw_usage jsonb DEFAULT '{}'::jsonb NOT NULL,
    latency_ms integer,
    status text NOT NULL,
    error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT llm_interactions_cached_prompt_tokens_check CHECK (((cached_prompt_tokens IS NULL) OR (cached_prompt_tokens >= 0))),
    CONSTRAINT llm_interactions_completion_tokens_check CHECK (((completion_tokens IS NULL) OR (completion_tokens >= 0))),
    CONSTRAINT llm_interactions_latency_ms_check CHECK (((latency_ms IS NULL) OR (latency_ms >= 0))),
    CONSTRAINT llm_interactions_prompt_tokens_check CHECK (((prompt_tokens IS NULL) OR (prompt_tokens >= 0))),
    CONSTRAINT llm_interactions_reasoning_tokens_check CHECK (((reasoning_tokens IS NULL) OR (reasoning_tokens >= 0))),
    CONSTRAINT llm_interactions_total_tokens_check CHECK (((total_tokens IS NULL) OR (total_tokens >= 0)))
);


--
-- Name: management_audit_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.management_audit_events (
    management_audit_event_id uuid DEFAULT gen_random_uuid() NOT NULL,
    changed_table text NOT NULL,
    changed_key text NOT NULL,
    change_type text NOT NULL,
    old_value jsonb,
    new_value jsonb NOT NULL,
    changed_by text DEFAULT CURRENT_USER NOT NULL,
    changed_at timestamp with time zone DEFAULT now() NOT NULL,
    reason text,
    request_metadata jsonb DEFAULT '{}'::jsonb NOT NULL
);


--
-- Name: no_action_email_patterns; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.no_action_email_patterns (
    pattern_id uuid DEFAULT gen_random_uuid() NOT NULL,
    pattern_name text NOT NULL,
    sender_email_equals text,
    sender_domain_equals text,
    subject_regex text,
    body_regex text,
    reason_template text NOT NULL,
    priority integer DEFAULT 100 NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    effective_start date DEFAULT CURRENT_DATE NOT NULL,
    effective_end date,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT no_action_email_patterns_at_least_one_match_check CHECK (((sender_email_equals IS NOT NULL) OR (sender_domain_equals IS NOT NULL) OR (subject_regex IS NOT NULL) OR (body_regex IS NOT NULL))),
    CONSTRAINT no_action_email_patterns_effective_dates_check CHECK (((effective_end IS NULL) OR (effective_end >= effective_start)))
);


--
-- Name: ownership; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ownership (
    ownership character varying(255),
    destination character varying(255),
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: routing_destinations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.routing_destinations (
    destination_code text NOT NULL,
    display_name text NOT NULL,
    email_address text,
    active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    parent_folder text,
    label text,
    send_teams_message boolean DEFAULT false,
    send_email boolean DEFAULT false
);


--
-- Name: runtime_config; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.runtime_config (
    config_key text NOT NULL,
    config_value jsonb NOT NULL,
    description text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: vw_asset_lookup; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.vw_asset_lookup AS
 SELECT 'asset'::text AS asset_source,
    ('asset:'::text || (a.id)::text) AS asset_lookup_id,
    (a.id)::text AS source_id,
    a.asset_alias,
    a.asset_name,
    a.address,
    a.tenants,
    a.asset_type,
    a.ownership,
    a.market_name,
    a.market_area,
    NULL::text AS comment,
    o.destination AS destination_code,
    rd.active AS destination_active,
    a.created_at
   FROM ((public.asset a
     LEFT JOIN public.ownership o ON (((a.ownership)::text = (o.ownership)::text)))
     LEFT JOIN public.routing_destinations rd ON ((rd.destination_code = (o.destination)::text)))
UNION ALL
 SELECT 'asset_custom'::text AS asset_source,
    ('asset_custom:'::text || (ac.id)::text) AS asset_lookup_id,
    (ac.id)::text AS source_id,
    ac.asset_alias,
    ac.asset_name,
    ac.address,
    NULL::text AS tenants,
    NULL::character varying(100) AS asset_type,
    NULL::character varying(255) AS ownership,
    NULL::character varying(255) AS market_name,
    NULL::character varying(255) AS market_area,
    ac.comment,
    (ac.destination_code)::text AS destination_code,
    rd.active AS destination_active,
    ac.created_at
   FROM (public.asset_custom ac
     LEFT JOIN public.routing_destinations rd ON ((rd.destination_code = (ac.destination_code)::text)));


--
-- Name: workflow_rule_conditions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.workflow_rule_conditions (
    condition_id uuid DEFAULT gen_random_uuid() NOT NULL,
    rule_code text NOT NULL,
    condition_key text NOT NULL,
    condition_value jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: workflow_rule_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.workflow_rule_versions (
    rule_version_id uuid DEFAULT gen_random_uuid() NOT NULL,
    rule_code text NOT NULL,
    version integer NOT NULL,
    rule_name text NOT NULL,
    priority integer NOT NULL,
    enabled boolean NOT NULL,
    condition_type text NOT NULL,
    condition_snapshot jsonb DEFAULT '{}'::jsonb NOT NULL,
    outcome text NOT NULL,
    destination_code text,
    reason_template text NOT NULL,
    effective_start date NOT NULL,
    effective_end date,
    management_audit_event_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT workflow_rule_versions_effective_dates_check CHECK (((effective_end IS NULL) OR (effective_end >= effective_start)))
);


--
-- Name: workflow_rules; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.workflow_rules (
    rule_code text NOT NULL,
    rule_name text NOT NULL,
    priority integer NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    condition_type text NOT NULL,
    outcome text NOT NULL,
    destination_code text,
    reason_template text NOT NULL,
    effective_start date DEFAULT CURRENT_DATE NOT NULL,
    effective_end date,
    version integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT workflow_rules_effective_dates_check CHECK (((effective_end IS NULL) OR (effective_end >= effective_start)))
);


--
-- Name: asset id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.asset ALTER COLUMN id SET DEFAULT nextval('public.asset_id_seq'::regclass);


--
-- Name: asset_custom id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.asset_custom ALTER COLUMN id SET DEFAULT nextval('public.asset_custom_id_seq'::regclass);


--
-- Name: actions actions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.actions
    ADD CONSTRAINT actions_pkey PRIMARY KEY (action_id);


--
-- Name: asset_custom asset_custom_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.asset_custom
    ADD CONSTRAINT asset_custom_pkey PRIMARY KEY (id);


--
-- Name: asset asset_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.asset
    ADD CONSTRAINT asset_pkey PRIMARY KEY (id);


--
-- Name: attachments attachments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attachments
    ADD CONSTRAINT attachments_pkey PRIMARY KEY (attachment_id);


--
-- Name: audit_runs audit_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_runs
    ADD CONSTRAINT audit_runs_pkey PRIMARY KEY (run_id);


--
-- Name: audit_steps audit_steps_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_steps
    ADD CONSTRAINT audit_steps_pkey PRIMARY KEY (step_id);


--
-- Name: audit_steps audit_steps_run_id_sequence_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_steps
    ADD CONSTRAINT audit_steps_run_id_sequence_number_key UNIQUE (run_id, sequence_number);


--
-- Name: decisions decisions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.decisions
    ADD CONSTRAINT decisions_pkey PRIMARY KEY (decision_id);


--
-- Name: document_items document_items_email_id_item_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_items
    ADD CONSTRAINT document_items_email_id_item_key_key UNIQUE (email_id, item_key);


--
-- Name: document_items document_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_items
    ADD CONSTRAINT document_items_pkey PRIMARY KEY (document_item_id);


--
-- Name: emails emails_idempotency_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.emails
    ADD CONSTRAINT emails_idempotency_key_key UNIQUE (idempotency_key);


--
-- Name: emails emails_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.emails
    ADD CONSTRAINT emails_pkey PRIMARY KEY (email_id);


--
-- Name: escalate_queue escalate_queue_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.escalate_queue
    ADD CONSTRAINT escalate_queue_pkey PRIMARY KEY (escalate_id);


--
-- Name: extractions extractions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.extractions
    ADD CONSTRAINT extractions_pkey PRIMARY KEY (extraction_id);


--
-- Name: invoices invoices_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoices
    ADD CONSTRAINT invoices_pkey PRIMARY KEY (invoice_id);


--
-- Name: llm_interactions llm_interactions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_interactions
    ADD CONSTRAINT llm_interactions_pkey PRIMARY KEY (llm_interaction_id);


--
-- Name: management_audit_events management_audit_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.management_audit_events
    ADD CONSTRAINT management_audit_events_pkey PRIMARY KEY (management_audit_event_id);


--
-- Name: no_action_email_patterns no_action_email_patterns_pattern_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.no_action_email_patterns
    ADD CONSTRAINT no_action_email_patterns_pattern_name_key UNIQUE (pattern_name);


--
-- Name: no_action_email_patterns no_action_email_patterns_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.no_action_email_patterns
    ADD CONSTRAINT no_action_email_patterns_pkey PRIMARY KEY (pattern_id);


--
-- Name: routing_destinations routing_destinations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.routing_destinations
    ADD CONSTRAINT routing_destinations_pkey PRIMARY KEY (destination_code);


--
-- Name: runtime_config runtime_config_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.runtime_config
    ADD CONSTRAINT runtime_config_pkey PRIMARY KEY (config_key);


--
-- Name: ownership uq_ownership; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ownership
    ADD CONSTRAINT uq_ownership UNIQUE (ownership);


--
-- Name: workflow_rule_conditions workflow_rule_conditions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workflow_rule_conditions
    ADD CONSTRAINT workflow_rule_conditions_pkey PRIMARY KEY (condition_id);


--
-- Name: workflow_rule_conditions workflow_rule_conditions_rule_code_condition_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workflow_rule_conditions
    ADD CONSTRAINT workflow_rule_conditions_rule_code_condition_key_key UNIQUE (rule_code, condition_key);


--
-- Name: workflow_rule_versions workflow_rule_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workflow_rule_versions
    ADD CONSTRAINT workflow_rule_versions_pkey PRIMARY KEY (rule_version_id);


--
-- Name: workflow_rule_versions workflow_rule_versions_rule_code_version_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workflow_rule_versions
    ADD CONSTRAINT workflow_rule_versions_rule_code_version_key UNIQUE (rule_code, version);


--
-- Name: workflow_rules workflow_rules_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workflow_rules
    ADD CONSTRAINT workflow_rules_pkey PRIMARY KEY (rule_code);


--
-- Name: actions_document_item_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX actions_document_item_idx ON public.actions USING btree (document_item_id) WHERE (document_item_id IS NOT NULL);


--
-- Name: asset_address_trgm_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX asset_address_trgm_idx ON public.asset USING gin (lower(COALESCE(address, ''::text)) public.gin_trgm_ops);


--
-- Name: asset_alias_trgm_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX asset_alias_trgm_idx ON public.asset USING gin (lower(regexp_replace((COALESCE(asset_alias, ''::character varying))::text, '[^a-zA-Z0-9]+'::text, ''::text, 'g'::text)) public.gin_trgm_ops);


--
-- Name: asset_custom_address_trgm_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX asset_custom_address_trgm_idx ON public.asset_custom USING gin (lower(COALESCE(address, ''::text)) public.gin_trgm_ops);


--
-- Name: asset_custom_alias_trgm_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX asset_custom_alias_trgm_idx ON public.asset_custom USING gin (lower(regexp_replace((COALESCE(asset_alias, ''::character varying))::text, '[^a-zA-Z0-9]+'::text, ''::text, 'g'::text)) public.gin_trgm_ops);


--
-- Name: asset_custom_destination_code_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX asset_custom_destination_code_idx ON public.asset_custom USING btree (destination_code);


--
-- Name: asset_custom_name_trgm_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX asset_custom_name_trgm_idx ON public.asset_custom USING gin (lower((COALESCE(asset_name, ''::character varying))::text) public.gin_trgm_ops);


--
-- Name: asset_name_trgm_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX asset_name_trgm_idx ON public.asset USING gin (lower((COALESCE(asset_name, ''::character varying))::text) public.gin_trgm_ops);


--
-- Name: asset_ownership_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX asset_ownership_idx ON public.asset USING btree (ownership);


--
-- Name: asset_tenants_trgm_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX asset_tenants_trgm_idx ON public.asset USING gin (lower(COALESCE(tenants, ''::text)) public.gin_trgm_ops);


--
-- Name: attachments_email_path_hash_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX attachments_email_path_hash_idx ON public.attachments USING btree (email_id, storage_path, sha256);


--
-- Name: decisions_document_item_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX decisions_document_item_idx ON public.decisions USING btree (document_item_id) WHERE (document_item_id IS NOT NULL);


--
-- Name: document_items_attachment_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX document_items_attachment_idx ON public.document_items USING btree (attachment_id) WHERE (attachment_id IS NOT NULL);


--
-- Name: document_items_email_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX document_items_email_idx ON public.document_items USING btree (email_id, created_at);


--
-- Name: escalate_queue_document_item_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX escalate_queue_document_item_idx ON public.escalate_queue USING btree (document_item_id) WHERE (document_item_id IS NOT NULL);


--
-- Name: escalate_queue_source_message_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX escalate_queue_source_message_id_idx ON public.escalate_queue USING btree (source_message_id) WHERE (source_message_id IS NOT NULL);


--
-- Name: extractions_document_item_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX extractions_document_item_idx ON public.extractions USING btree (document_item_id) WHERE (document_item_id IS NOT NULL);


--
-- Name: invoices_document_item_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX invoices_document_item_idx ON public.invoices USING btree (document_item_id) WHERE (document_item_id IS NOT NULL);


--
-- Name: invoices_fingerprint_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX invoices_fingerprint_idx ON public.invoices USING btree (duplicate_fingerprint);


--
-- Name: invoices_vendor_amount_date_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX invoices_vendor_amount_date_idx ON public.invoices USING btree (regexp_replace(lower(COALESCE(vendor_name, ''::text)), '\s+'::text, ' '::text, 'g'::text), amount, invoice_date);


--
-- Name: invoices_vendor_invoice_date_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX invoices_vendor_invoice_date_idx ON public.invoices USING btree (regexp_replace(lower(COALESCE(vendor_name, ''::text)), '\s+'::text, ' '::text, 'g'::text), regexp_replace(lower(COALESCE(invoice_number, ''::text)), '\s+'::text, ' '::text, 'g'::text), invoice_date);


--
-- Name: invoices_vendor_invoice_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX invoices_vendor_invoice_idx ON public.invoices USING btree (regexp_replace(lower(COALESCE(vendor_name, ''::text)), '\s+'::text, ' '::text, 'g'::text), regexp_replace(lower(COALESCE(invoice_number, ''::text)), '\s+'::text, ' '::text, 'g'::text));


--
-- Name: llm_interactions_extraction_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX llm_interactions_extraction_idx ON public.llm_interactions USING btree (extraction_id) WHERE (extraction_id IS NOT NULL);


--
-- Name: llm_interactions_run_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX llm_interactions_run_idx ON public.llm_interactions USING btree (run_id, created_at);


--
-- Name: llm_interactions_step_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX llm_interactions_step_idx ON public.llm_interactions USING btree (step_id) WHERE (step_id IS NOT NULL);


--
-- Name: management_audit_events_lookup_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX management_audit_events_lookup_idx ON public.management_audit_events USING btree (changed_table, changed_key, changed_at DESC);


--
-- Name: no_action_email_patterns_active_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX no_action_email_patterns_active_idx ON public.no_action_email_patterns USING btree (enabled, priority, effective_start, effective_end);


--
-- Name: ownership_destination_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ownership_destination_idx ON public.ownership USING btree (destination);


--
-- Name: workflow_rule_versions_lookup_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX workflow_rule_versions_lookup_idx ON public.workflow_rule_versions USING btree (rule_code, version);


--
-- Name: workflow_rules_active_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX workflow_rules_active_idx ON public.workflow_rules USING btree (enabled, priority, effective_start, effective_end);


--
-- Name: actions actions_decision_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.actions
    ADD CONSTRAINT actions_decision_id_fkey FOREIGN KEY (decision_id) REFERENCES public.decisions(decision_id);


--
-- Name: actions actions_destination_code_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.actions
    ADD CONSTRAINT actions_destination_code_fkey FOREIGN KEY (destination_code) REFERENCES public.routing_destinations(destination_code);


--
-- Name: actions actions_document_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.actions
    ADD CONSTRAINT actions_document_item_id_fkey FOREIGN KEY (document_item_id) REFERENCES public.document_items(document_item_id) ON DELETE SET NULL;


--
-- Name: actions actions_email_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.actions
    ADD CONSTRAINT actions_email_id_fkey FOREIGN KEY (email_id) REFERENCES public.emails(email_id) ON DELETE CASCADE;


--
-- Name: attachments attachments_email_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attachments
    ADD CONSTRAINT attachments_email_id_fkey FOREIGN KEY (email_id) REFERENCES public.emails(email_id) ON DELETE CASCADE;


--
-- Name: audit_runs audit_runs_email_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_runs
    ADD CONSTRAINT audit_runs_email_id_fkey FOREIGN KEY (email_id) REFERENCES public.emails(email_id);


--
-- Name: audit_steps audit_steps_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_steps
    ADD CONSTRAINT audit_steps_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.audit_runs(run_id) ON DELETE CASCADE;


--
-- Name: decisions decisions_destination_code_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.decisions
    ADD CONSTRAINT decisions_destination_code_fkey FOREIGN KEY (destination_code) REFERENCES public.routing_destinations(destination_code);


--
-- Name: decisions decisions_document_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.decisions
    ADD CONSTRAINT decisions_document_item_id_fkey FOREIGN KEY (document_item_id) REFERENCES public.document_items(document_item_id) ON DELETE SET NULL;


--
-- Name: decisions decisions_email_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.decisions
    ADD CONSTRAINT decisions_email_id_fkey FOREIGN KEY (email_id) REFERENCES public.emails(email_id) ON DELETE CASCADE;


--
-- Name: decisions decisions_matched_rule_code_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.decisions
    ADD CONSTRAINT decisions_matched_rule_code_fkey FOREIGN KEY (matched_rule_code) REFERENCES public.workflow_rules(rule_code);


--
-- Name: decisions decisions_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.decisions
    ADD CONSTRAINT decisions_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.audit_runs(run_id);


--
-- Name: document_items document_items_attachment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_items
    ADD CONSTRAINT document_items_attachment_id_fkey FOREIGN KEY (attachment_id) REFERENCES public.attachments(attachment_id) ON DELETE SET NULL;


--
-- Name: document_items document_items_email_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_items
    ADD CONSTRAINT document_items_email_id_fkey FOREIGN KEY (email_id) REFERENCES public.emails(email_id) ON DELETE CASCADE;


--
-- Name: escalate_queue escalate_queue_decision_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.escalate_queue
    ADD CONSTRAINT escalate_queue_decision_id_fkey FOREIGN KEY (decision_id) REFERENCES public.decisions(decision_id);


--
-- Name: escalate_queue escalate_queue_document_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.escalate_queue
    ADD CONSTRAINT escalate_queue_document_item_id_fkey FOREIGN KEY (document_item_id) REFERENCES public.document_items(document_item_id) ON DELETE SET NULL;


--
-- Name: escalate_queue escalate_queue_email_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.escalate_queue
    ADD CONSTRAINT escalate_queue_email_id_fkey FOREIGN KEY (email_id) REFERENCES public.emails(email_id) ON DELETE CASCADE;


--
-- Name: extractions extractions_document_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.extractions
    ADD CONSTRAINT extractions_document_item_id_fkey FOREIGN KEY (document_item_id) REFERENCES public.document_items(document_item_id) ON DELETE SET NULL;


--
-- Name: extractions extractions_email_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.extractions
    ADD CONSTRAINT extractions_email_id_fkey FOREIGN KEY (email_id) REFERENCES public.emails(email_id) ON DELETE CASCADE;


--
-- Name: asset fk_asset_ownership; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.asset
    ADD CONSTRAINT fk_asset_ownership FOREIGN KEY (ownership) REFERENCES public.ownership(ownership);


--
-- Name: ownership fk_ownership_destination; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ownership
    ADD CONSTRAINT fk_ownership_destination FOREIGN KEY (destination) REFERENCES public.routing_destinations(destination_code);


--
-- Name: invoices invoices_document_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoices
    ADD CONSTRAINT invoices_document_item_id_fkey FOREIGN KEY (document_item_id) REFERENCES public.document_items(document_item_id) ON DELETE SET NULL;


--
-- Name: invoices invoices_email_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoices
    ADD CONSTRAINT invoices_email_id_fkey FOREIGN KEY (email_id) REFERENCES public.emails(email_id) ON DELETE CASCADE;


--
-- Name: llm_interactions llm_interactions_email_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_interactions
    ADD CONSTRAINT llm_interactions_email_id_fkey FOREIGN KEY (email_id) REFERENCES public.emails(email_id) ON DELETE CASCADE;


--
-- Name: llm_interactions llm_interactions_extraction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_interactions
    ADD CONSTRAINT llm_interactions_extraction_id_fkey FOREIGN KEY (extraction_id) REFERENCES public.extractions(extraction_id) ON DELETE SET NULL;


--
-- Name: llm_interactions llm_interactions_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_interactions
    ADD CONSTRAINT llm_interactions_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.audit_runs(run_id) ON DELETE CASCADE;


--
-- Name: llm_interactions llm_interactions_step_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_interactions
    ADD CONSTRAINT llm_interactions_step_id_fkey FOREIGN KEY (step_id) REFERENCES public.audit_steps(step_id) ON DELETE SET NULL;


--
-- Name: workflow_rule_conditions workflow_rule_conditions_rule_code_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workflow_rule_conditions
    ADD CONSTRAINT workflow_rule_conditions_rule_code_fkey FOREIGN KEY (rule_code) REFERENCES public.workflow_rules(rule_code) ON DELETE CASCADE;


--
-- Name: workflow_rule_versions workflow_rule_versions_destination_code_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workflow_rule_versions
    ADD CONSTRAINT workflow_rule_versions_destination_code_fkey FOREIGN KEY (destination_code) REFERENCES public.routing_destinations(destination_code);


--
-- Name: workflow_rule_versions workflow_rule_versions_management_audit_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workflow_rule_versions
    ADD CONSTRAINT workflow_rule_versions_management_audit_event_id_fkey FOREIGN KEY (management_audit_event_id) REFERENCES public.management_audit_events(management_audit_event_id);


--
-- Name: workflow_rule_versions workflow_rule_versions_rule_code_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workflow_rule_versions
    ADD CONSTRAINT workflow_rule_versions_rule_code_fkey FOREIGN KEY (rule_code) REFERENCES public.workflow_rules(rule_code);


--
-- Name: workflow_rules workflow_rules_destination_code_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workflow_rules
    ADD CONSTRAINT workflow_rules_destination_code_fkey FOREIGN KEY (destination_code) REFERENCES public.routing_destinations(destination_code);


--
-- PostgreSQL database dump complete
--

\unrestrict E1GyjeUZqbHlvfeeHugPAR50EHeMSOnQktnm6Y9KAoROAVTYwP6XNewDfRqoUJP
