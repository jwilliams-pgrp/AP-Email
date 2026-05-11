# 050 - Auditing and Traceability Spec

## Purpose

Provide full traceability for every email processed by the AP system.

The system must:
- record every step of execution
- capture all decisions and justifications
- log all LLM inputs and outputs when LLMs are used
- produce a visual execution trace artifact
- support debugging, replay, and audit review

## Complete Execution Trace

Every processed email must produce a full execution trace including:
- ingestion metadata
- extraction inputs and outputs
- validation steps
- duplicate check result
- routing match result
- workflow rule evaluations
- final decision
- actions taken or skipped due to dry run

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
- `LLM_EXTRACTION`
- `VALIDATION`
- `DUPLICATE_CHECK`
- `ROUTING_MATCH`
- `RULE_EVALUATION`
- `DECISION`
- `ACTION`
- `FINALIZE`

If local development uses fixture extraction instead of `codex exec`, the extraction step must still be audited and identified as fixture extraction. Normal local `.msg` extraction must audit the Codex CLI extractor metadata.

## LLM Audit Requirements

For every LLM call, record:
- prompt template name
- prompt version
- rendered prompt or prompt artifact path
- model name
- parameters
- raw LLM response
- parsed JSON output
- validation result
- confidence signals

LLM outputs must never be used without schema validation and audit logging.

## Decision Logging

Every decision must include:
- outcome
- destination_email, if applicable
- matched_rule
- matched_rule_version
- reason
- confidence
- contributing signals
- dry_run

Reasons must be human-readable, reference the rule or condition, and avoid vague language.

Good:
`Invoice amount 15000 exceeds configured threshold 10000 -> FILE to lien release folder`

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
- Every final decision is linked to an audit run.
- Every rule evaluation records matched or skipped status.
- Dry-run skipped actions are audit logged.
- LLM or fixture extraction input and output are audit logged.
- A local visual trace artifact is created for each completed run.
- Failed local runs are finalized as failed and preserve available failure context.
- Audit records include enough rule version information to support replay.
