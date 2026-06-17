# 030 - Decision Engine Spec

## Purpose

Convert validated email and invoice facts into a safe routing decision.

## Inputs

- email metadata
- attachment metadata
- invoice facts history from Postgres
- extracted invoice fields
- document classification
- asset candidate set from `vw_asset_lookup` fuzzy retrieval
- deterministic asset gate checks
- duplicate check result
- confidence score
- runtime config
- active workflow rules from Postgres

## Outputs

A decision object containing:
- outcome
- destination_email, if applicable
- reason
- confidence
- matched_rule
- matched_rule_version
- extracted_fields
- routing_match

## Requirements

- Every processed email must produce one final decision.
- Local multi-document emails may also produce item-level decisions, but exactly one aggregated email decision controls the external action.
- Every decision must be audit logged.
- Decisions must be deterministic for the same input data and rule version.
- Missing required fields must result in `ESCALATE`.
- The engine must fail loudly if required workflow configuration is missing.
- The engine must not silently substitute hard-coded business defaults.
- Deterministic gate authority cannot be bypassed by fuzzy retrieval quality alone.
- For current thread replies, LLM extraction may classify whether `email.latest_body_text` indicates no AP action, but the decision engine must deterministically enforce quoted-history, sender-policy, and current-attachment gates before producing `DISCARD` / `NO_ACTION`.

## Asset Matching Contract

- Extractors return normalized `property_lookup` arrays before SQL retrieval.
- Repository code uses the extracted `property_lookup` object directly and must not create a second normalized lookup layer from invoice fields.
- Repository code sends all extracted property code, property name, tenant, address, suite, city, state, ZIP, and structured address candidates to Postgres in one explicit candidate query.
- When structured address candidates are present, repository SQL scores each candidate's street, city, state, and ZIP as a unit with deterministic candidate-priority weighting. It must not mix address components across candidates.
- SQL retrieval fuzzy-ranks `vw_asset_lookup` rows directly from business columns.
- Candidate identity uses `asset_lookup_id`, with namespace ids such as `asset:123` and `asset_custom:123`, and audit payloads include `asset_source`.
- Canonical `asset` candidates authorize destinations through `ownership.destination`; `asset_custom` candidates authorize destinations through the row's direct `destination_code`.
- Deterministic authority remains in the decision engine gate checks.
- LLM final property review must evaluate the extracted property code, property name, tenant/alias, possible aliases, evidence summary, address candidate evidence text, service/property address, bill-to evidence, and all provided candidate asset aliases, names, addresses, destinations, and scores.
- When candidate assets share the same address, an explicit property name, tenant, asset-code signal, or clear near-name signal may disambiguate the correct candidate. The reviewer should select exactly one candidate when that evidence clearly identifies one asset; otherwise it must return no candidate so deterministic policy can `ESCALATE`.
- Clear near-name review evidence includes missing common prefixes, suffix variants, number-format variants, and configured alias variants only when exactly one provided candidate fits the visible phrase. Vague family names or ambiguous partial names must not select a shared-address candidate.
- When shared-address candidates are supported only by the same address and no extracted name, code, tenant, alias, or evidence text distinguishes one candidate, the reviewer must return no candidate.
- The reviewer may select a bill-to-only or customer-account address candidate when SQL returned one clear active candidate above `property_match_min_score`, the candidate evidence is from bill-to or customer-account address text, no stronger candidate source maps to another active asset, and the review evidence explicitly cites the bill-to/customer-account address match.
- Unresolved project, job, or property text is not a stronger conflict unless it identifies a different configured asset, address, property name, or property code.
- Property name, property code, project, job, site, and service evidence is stronger than bill-to evidence only when it maps to a different configured active asset. If visible project/property text is unresolved and bill-to/customer-account address evidence selects the only active candidate above threshold, the deterministic gate may accept the address fallback.
- If no candidate exists, score is below minimum, or gate fails for any reason, route to `ESCALATE`.
- Candidate margin, runner-up score, and tie context are audit-only diagnostics and must not block asset routing.
- Duplicate `asset_alias` values are supported and must not auto-approve by alias alone.
- LLM-provided address candidate confidence is audit/debug data only and must not authorize routing.

## Audit Contract

Routing audit payload must include:
- raw extracted input signals
- extracted `property_lookup` query values
- candidate list
- per-candidate scores
- gate checks
- selected candidate, when present
- explicit `ESCALATE` reason for gate failure

## Table-Driven Boundary

Decision code owns:
- evaluation order
- supported condition types
- schema validation
- deterministic matching
- safety invariants
- final outcome and destination authorization

Postgres data owns:
- routing rows
- destinations
- thresholds
- enabled rules
- reason templates
- statement handling
- effective dates

## Acceptance Criteria

- Clean internal invoice routes to configured Medius destination.
- Clean external invoice routes to configured PM destination.
- Duplicate invoice routes to `ESCALATE`.
- Duplicate status `suspected` means another persisted invoice exists for a different `idempotency_key` with the same normalized vendor name, normalized invoice number, and exact invoice date; `confirmed` is not produced by duplicate policy.
- Duplicate status `suspected` routes to `ESCALATE`.
- Invoice packages with separate related backup documentation route to configured `ESCALATE_LIEN_WAIVER` through `hard_separate_lien_waiver`.
- `mentions_lien_waiver_or_release = true` alone does not route to the superseded `ESCALATE_MULTI_PDF_MERGE` path.
- Past due or overdue invoice notices route to `ESCALATE` using the configured past-due escalation destination.
- A payable invoice derives the internal `past_due` flag only when validated extraction identifies explicit current-email subject/body language calling the invoice past due, overdue, in collection, or equivalent. Invoice due dates, attachment-only labels, and date comparisons must not derive `past_due`.
- Link-only invoice or bill notices, including utility or service bill portal notices with no invoice attachment, route to `ESCALATE`.
- Contractor timesheet or time-detail document items in a batch with no `invoice` item route to `ESCALATE_CONTRACTOR_TIMESHEET`.
- Invoice packages with separate timesheet or time-detail backup continue to route through `hard_separate_lien_waiver`.
- Validated LLM-classified credit memo items route to `ESCALATE_CREDIT_MEMO`.
- Unsupported or unreadable attachment escalation is scoped to the current item evidence. A disallowed attachment extension or unreadable file must force `ESCALATE` when that file is named in the invoice item's `evidence.source_attachments`; unrelated non-inline attachments on the same email remain persisted and audited but do not block a valid invoice item whose evidence is scoped to a supported readable attachment.
- If an invoice item names both a supported invoice file and an unsupported backup file in `evidence.source_attachments`, the item must route to `ESCALATE_WRONG_FILE_TYPE` rather than guessing which file is invoice evidence.
- Ben E Keith flagged items are filed by `ben_e_keith_notice_file` before attachment extension, unreadable required PDF, or low-quality PDF hard exceptions, including `.txt` integration attachments.
- Messages classified as `payment_inquiry` or `vendor_question`, or with `observed_facts.indicates_vendor_question_or_payment_inquiry = true`, must not be converted to `link_only_invoice` by deterministic link-only overrides.
- Unknown building routes to `ESCALATE`.
- Low confidence is recorded and compared to configured threshold for audit, but does not by itself force `ESCALATE`.
- Zero-dollar invoice routes to configured `ESCALATE_0_DOLLAR_INVOICE` when a usable normal deterministic destination exists; otherwise the existing unmatched-building or fallback escalation path applies.
- High-dollar invoice routes automatically when its normal deterministic destination is `MEDIUS_PROPERTIES` and an explicit `PROJECT NO` or `PROJECT NUMBER` was extracted as `invoice.project_number`.
- High-dollar invoice routes to configured `ESCALATE_OVER_10000` when its normal deterministic destination is not `MEDIUS_PROPERTIES`, or when its normal deterministic destination is `MEDIUS_PROPERTIES` but only a job number or no project number was extracted, except matched multifamily assets route to `MEDIUS_MF` first.
- Check request routes automatically when deterministic property matching resolves to configured `MEDIUS_PROPERTIES`.
- Check request routes to configured `ESCALATE_CHECK_REQUEST` when deterministic property matching is missing, ambiguous, or resolves to a non-Medius destination.
- Statement routes to configured statement outcome.
- Missing required workflow config raises an explicit error in tests.
- The same input and same workflow rule version produce the same decision.
- Deterministic asset routing requires one selected candidate with the score check passing.
- Deterministic asset routing records `asset_source` and `asset_lookup_id` for candidates and the selected match.
- A selected `asset_custom` candidate routes only when its direct destination exists and is active.
- A selected `asset_custom` candidate with a null, unknown, or inactive destination does not auto-route and falls through to explicit `ESCALATE` behavior.
- If any deterministic gate check fails, asset routing must not auto-route and must fall through to `ESCALATE` behavior.
- Address-candidate extraction with a stronger deliver-to/site address and a weaker bill-to/account address routes only when deterministic Postgres matching selects an active asset from the candidate set.
- Bill-to/customer-account address fallback may route only when no mapped property code/name/site/service/deliver-to/ship-to signal exists, one active candidate qualifies above `property_match_min_score`, and audit details record `bill_to_address_fallback_selected` in the property gate or LLM advisory reason.
- Bill-to/customer-account address fallback may route when visible project or property text such as `Hillwood - 2026 CTR MIB` is unresolved, no candidate maps to that text, and exact `9800 Hillwood Parkway` bill-to evidence selects one active candidate above threshold.
- Bill-to/customer-account address fallback routes to `ESCALATE_UNMATCHED_BUILDING` when no candidate exists, the selected candidate is below threshold, multiple near-equivalent active candidates remain, the selected destination is inactive, or stronger property/site/service/deliver-to/ship-to evidence maps elsewhere.
- Missing or unknown asset addresses produce explicit unmatched-building `ESCALATE`, including configured informational property notices with property or address evidence but no active asset match.
- A current reply classified by validated LLM extraction as no-action routes to `DISCARD` / `NO_ACTION` only when parser-derived thread context and deterministic safety gates pass.
- An appointment confirmation, reminder, or follow-up classified by validated LLM extraction as an informational appointment notice routes to `DISCARD` / `NO_ACTION` only when the configured workflow rule matches the observed fact and blocked AP-risk flags are absent.
- Multiple actionable document items that all share the same outcome and destination aggregate to one final email action.
- If any item decision is `ESCALATE` or `FLAG`, the aggregated final decision uses the matching item decision with the lowest numeric workflow-rule priority.
- If multiple `AUTO`, `FILE`, or `DISCARD` item decisions disagree on outcome or destination, the aggregated final decision uses the table-driven mixed-item-destination escalation rule with destination `ESCALATE_SPLIT_MULTI_PDF`.
- Matched invoice assets with `asset_type = 'Multifamily'` route automatically to `MEDIUS_MF`.
