# 050 - Auditing and Traceability Spec

## Purpose

Provide full traceability for every email processed by the AP system.

The system must:
- record every step of execution
- capture all decisions and justifications
- log all LLM inputs and outputs when LLMs are used
- produce a visual execution trace artifact
- support debugging, replay, and audit ESCALATE

## Complete Execution Trace

Every processed email must produce a full execution trace including:
- ingestion metadata
- extraction inputs and outputs
- validation steps
- duplicate check result
- routing match result
- workflow rule evaluations
- final decision
- actions taken and external routing results

No step may be omitted.

## Step-Based Audit Model

Execution must be recorded as an ordered sequence of steps.

Each step must include:
- step_id
- run_id
- sequence_number
- step_type
- timestamp
- input summary
- output summary
- decision, if applicable
- reason
- confidence, if applicable
- error, if applicable

## Required Step Types

Minimum required step types:
- `INGESTION`
- `ATTACHMENT_PROCESSING`
- `DOCUMENT_EXTRACTION_SELECTION`
- `DOCUMENT_INTELLIGENCE`
- `LLM_EXTRACTION`
- `VALIDATION`
- `DUPLICATE_CHECK`
- `ROUTING_MATCH`
- `RULE_EVALUATION`
- `DECISION`
- `ACTION`
- `FINALIZE`

If local development uses fixture extraction instead of Azure OpenAI Foundry, the extraction step must still be audited and identified as fixture extraction. Normal local `.msg` extraction must audit Azure OpenAI extractor metadata.

`DOCUMENT_EXTRACTION_SELECTION` must run after `ATTACHMENT_PROCESSING` and before `DOCUMENT_INTELLIGENCE` for parsed emails. It records the deterministic extractor selected for each attachment: `pymupdf`, `document_intelligence`, or `none`, including the reason code and PyMuPDF quality signals.

`DOCUMENT_INTELLIGENCE` must run after `DOCUMENT_EXTRACTION_SELECTION` and before `LLM_EXTRACTION` for parsed emails when one or more attachments are selected for Azure Document Intelligence. It records Azure Document Intelligence attachment enrichment, including attachment counts, model call counts, pages analyzed, per-model pages, latency, statuses, and raw artifact paths. Document Intelligence usage must not be stored in `llm_interactions` because it is billed by page/model usage rather than tokens.

## LLM Audit Requirements

For every LLM call, record:
- prompt template name
- prompt version
- rendered prompt or prompt artifact path
- model name
- parameters
- token usage metrics when available from the provider
- raw LLM response
- parsed JSON output
- validation result
- confidence signals
- for property lookup during routing, the SQL sent to Postgres, the bound lookup payload, and the returned candidate rows

Every LLM call must have a `llm_interactions` row linked to the relevant `audit_runs` row and, when the interaction is represented by an ordered audit step, the relevant `audit_steps` row. If the LLM call produced or attempted to produce an `extraction.v1` payload, the interaction must also link to the relevant `extractions` row.

LLM interaction usage metrics must include, when available:
- prompt tokens
- completion tokens
- total tokens
- cached prompt tokens
- reasoning tokens
- raw provider usage payload
- latency in milliseconds

When LLM-assisted interpretation is used for fallback property matching, the audit record must also identify:
- `schema_version = llm_interpretation.v1`
- candidate property codes
- cited evidence
- ambiguity flags
- any non-authoritative recommended outcome
- whether deterministic re-validation accepted or rejected the candidate

LLM outputs must never be used without schema validation and audit logging.

## Decision Logging

Every decision must include:
- outcome
- destination_email, if applicable
- destination folder, if applicable
- matched_rule
- matched_rule_version
- reason
- confidence
- contributing signals

For batch extraction, item-level decisions must be persisted with their document item identity, and the final email-level decision must include all item decisions in `routing_match.aggregation.item_decisions`.

Reasons must be human-readable, reference the rule or condition, and avoid vague language.

Good:
`Invoice amount 15000 exceeds configured threshold 10000; normal destination TIFFANY_BECK is not exempt -> ESCALATE with OVER-10000 label`

Bad:
`Seems high value`

## Data Storage Model

Each email processing attempt creates one audit run:

```json
{
  "run_id": "uuid",
  "email_id": "uuid",
  "status": "completed",
  "started_at": "...",
  "completed_at": "...",
  "final_outcome": "AUTO"
}
```

Each run has ordered audit steps. The final decision must be reproducible from the recorded input summaries, extracted data, and workflow rule version.

Failed processing attempts must not remain in `started` status. When a run fails after an audit run has been created, the processor must:
- write the failure to the relevant audit step
- persist available LLM input and output when extraction was attempted
- write a `FINALIZE` audit step with `status=failed`
- update the audit run to `status=failed` with `completed_at` populated

Processing failures must retry once before becoming final:
- On the first processing exception, log the attempt failure, wait 30 seconds, and retry the same email once.
- On the second processing exception, mark the known audit run as failed when Postgres is reachable.
- If Postgres is unavailable and cannot record the failure, preserve the runtime exception so the host reports the invocation failure.

## Visual Trace Artifact

The system should produce a local visual trace artifact for debugging, such as Mermaid text or rendered diagram output.

The artifact must reference:
- run id
- major pipeline steps
- final outcome
- matched rule or fallback
- business-facing step labels that describe what was achieved
- transition reasons that explain why processing moved from one step to the next
- green styling for completed steps and red styling for failed steps

## Acceptance Criteria

- Every processed email creates one audit run.
- Every audit run includes all required step types.
- Every parsed-email audit run records `DOCUMENT_EXTRACTION_SELECTION` between attachment processing and LLM extraction.
- Every parsed-email audit run records `DOCUMENT_INTELLIGENCE` after extractor selection and before LLM extraction.
- Every final decision is linked to an audit run.
- Batch extraction audit records include document item keys, item-level decisions, and the aggregation mode used to select the final decision.
- Every rule evaluation records matched or skipped status.
- Action execution status and external routing results are audit logged.
- LLM or fixture extraction input and output are audit logged.
- LLM-assisted interpretation input, output, validation result, and deterministic re-validation result are audit logged when used.
- A local visual trace artifact is created for each completed run.
- Failed local runs are finalized as failed and preserve available failure context.
- Audit records include enough rule version information to support replay.
