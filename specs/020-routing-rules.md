# 020 - Routing Rules Spec

## Purpose

Define deterministic routing behavior for AP inbox emails.

## Principle

The LLM extracts facts. The rules engine makes the routing decision. Mutable workflow policy comes from Postgres tables.

## Routing Priority

1. Hard exception rules
2. Duplicate detection
3. Routing table match
4. Confidence threshold audit comparison
5. `ESCALATE` fallback

## Rule Configuration

Rules must be stored in Postgres with:
- stable rule code
- priority
- enabled status
- condition type
- condition values
- outcome
- destination, when applicable
- reason template
- effective start and end dates
- version or revision metadata

The code may define supported condition types, but business-specific condition values must come from data.

## Property Match Surface

- `asset` stores canonical asset identity and matching fields.
- `ownership` maps asset ownership classifications to routing destinations.
- `asset_custom` stores management-maintained asset lookup rows with a direct `destination_code`.
- `vw_asset_lookup` is the combined matching/read surface for canonical `asset` rows and direct-destination `asset_custom` rows.
- `asset_alias` and `asset_name` are the canonical property code/name equivalents for routing.
- Runtime matching queries property identity, tenant, and address signal columns from `vw_asset_lookup`.
- Canonical `asset` rows derive their destination through `ownership.destination`; `asset_custom` rows use `asset_custom.destination_code` directly.
- Extractors return normalized `property_lookup` arrays and optional ranked `property_lookup.address_candidates` for SQL matching; repository code must not create a second normalized lookup layer from invoice fields.
- `property_lookup` must include explicit normalized property/address candidates from service, property, site, delivery, billing, bill-to, and shipping signals. Service/property/site/shipping/delivery addresses must be ordered before billing/bill-to signals when facts conflict.
- Structured address candidates must be scored as complete address units so city/state/ZIP from one extracted address cannot strengthen a street from another extracted address.
- Sender, vendor, remit-to, and email signature addresses are not property lookup candidates unless the same address is explicitly labeled as the service, property, site, shipping, delivery, or bill-to address for the invoice.
- Exact property-code matches after normalization are strong deterministic signals, including formatting variants such as `HC-2`, `HC 2`, and `HC2`.
- Property-code matching must compact punctuation and whitespace so asset-code variants such as `GW 31`, `GW-31`, and `GW31` compare as exact matches.
- Exact property-code matches after compaction must receive the strongest candidate score.
- Address matches are also strong signals, but an unfamiliar address must not defeat an otherwise unambiguous exact property-code match.
- Database address scoring must parse canonical `asset.address` values into street, city, state, and ZIP components inside the lookup SQL when component columns are not available. ZIP scoring must use the last ZIP-like token from the canonical address, and state scoring must normalize common full state names to 2-letter abbreviations.
- Deterministic gate checks remain the final authority.
- LLM final property review evaluates the full candidate set after SQL retrieval. It must consider asset alias/code, asset name, asset type, matched text/address, extracted property name, possible aliases, evidence summary, address candidate evidence text, service/property/site address, and bill-to context before returning its advisory selected asset. Asset type is advisory context only and must not authorize routing.
- LLM final property review must preserve visible asset-code families. It must not convert visible Westport/`WP` evidence into Alliance Gateway/`GW` evidence, or visible Gateway/`GW` evidence into Westport/`WP` evidence. If the source evidence says `WP9` and a candidate is `GW9` / `Alliance Gateway 9`, the reviewer must return no candidate unless the source also visibly supports `GW9` or `Alliance Gateway 9`.
- When extracted property code and property name conflict, the LLM final property review should prefer the source-visible exact code family and return no candidate when the conflict prevents a confident candidate selection.
- For shared-address candidates, the review may select exactly one candidate only when extracted name, code, tenant, alias, or evidence text distinguishes that candidate; address-only ties must remain ambiguous and escalate.
- LLM extraction and advisory property review must preserve explicit visible asset or property names in audit-facing fields. A visible name must not be normalized into a different canonical asset family solely because a nearby address, code, or bill-to signal resembles another asset.
- When a visible property name and address disagree, LLM extraction and advisory review should prefer the visible property name for canonical asset normalization unless the source explicitly identifies the address as the service, site, delivery, shipping, or property address for the invoice.
- A visible `Hillwood Commons II` name should normalize to `Hillwood Commons II` / `HWC2` when that asset exists in the provided asset reference or candidate set. It must not be converted to `Heritage Commons II` / `HC2` unless the source visibly says `Heritage Commons II` or `HC2`.
- Project, Job, Site, Service Location, Location, Deliver To, Ship To, and Property evidence is stronger serviced-property evidence than Bill To when these source fields conflict.
- When extraction sees a labeled Project, Job, Site, Location, Service Location, Property, Building, Ship To, or Deliver To value that visibly matches or clearly near-matches exactly one asset name or alias, that value must be returned in structured `property_lookup.property_name` and/or `property_lookup.property_code`. This structured name/code should disambiguate shared-address assets.
- Clear near-name signals such as missing common prefixes (`Gateway 15` for `Alliance Gateway 15`), suffix variants (`Circle T Golf Course` for `Circle T Golf`), and number-format variants (`Heritage Commons 2` for `Heritage Commons II`) may identify a configured asset only when the candidate is unique. Vague family names or ambiguous partial names must not authorize a property route.
- Bill-to or customer-account address evidence may be used as an automatic-routing fallback when no property code, property name, tenant, service/site/delivery/shipping/property address, or other stronger serviced-property signal maps to an active asset.
- Bill-to address fallback is not allowlisted. Any active asset address candidate may qualify when deterministic matching returns exactly one clear active candidate above `property_match_min_score`.
- Bill-to address fallback must not override a stronger conflicting property, site, service, deliver-to, or ship-to signal. Unresolved project, job, or property text is not a conflict unless it identifies a different configured asset, address, property name, or property code.
- Property name, property code, project, job, site, or service evidence is stronger than bill-to evidence only when it maps to a different configured active asset. Visible but unresolved text such as `Hillwood - 2026 CTR MIB` must not block an exact bill-to address fallback to the only active asset candidate above threshold, such as `9800 Hillwood Parkway`.
- Shared-address assets are not automatically ambiguous when an extracted property name or alias clearly names one of the candidates, such as `Heritage Commons II` selecting `HC2` over `HC3` at the same address.
- Ambiguity, near-tie, low confidence score, or any gate failure must route to `ESCALATE`.

## Hard Exception Rules

### Multi-Invoice PDF
If an attachment contains multiple invoices:
- outcome: `ESCALATE`
- escalate label: `MULTI-INVOICE-PDF`
- reason: requires manual split
- This hard exception applies only when a single attached PDF contains multiple invoices.
- Multiple invoices across separate attachment files must not trigger this hard exception by itself.

### Invoice With Separate Related Backup
If an invoice has separate related backup documentation:
- outcome: `ESCALATE`
- escalate label: `MULTI-PDF-MERGE`
- destination: configured `ESCALATE_MULTI_PDF_MERGE` destination
- reason: invoice has separate related backup documentation
- This includes lien waivers, lien releases, work orders, tickets, time detail, shift reports, actual hours worked, hours worked, staffing hours, timesheets, time sheets, labor/detail backup, labor/material breakdowns, job completion records, or similar support represented as a distinct extracted AP-relevant document item separate from the invoice item.
- This rule matches the derived `separate_lien_waiver` document flag only when `document_type = invoice` and batch normalization finds a related supporting-document item. It must not be derived from attachment count or from an invoice item's `observed_facts.mentions_separate_backup_document = true` alone.
- Embedded invoice pages, invoice line-item detail, same-invoice work descriptions, inline images, logos, decorative images, photo filenames or image references inside the invoice PDF, excluded irrelevant attachments, and not-selected attachments must not qualify as separate related backup.
- The supporting document does not need to say "invoice", and explicit merge/combine language is not required.
- `lien_release_related` remains a standalone lien-release classification flag and must not route invoice packages by itself.

### Wrong File Type
If an attachment is image (`jpg`, `jpeg`, `png`) or Excel (`xls`, `xlsx`):
- outcome: `ESCALATE`
- escalate label: `WRONG-FILE-TYPE`
- reason: unsupported AP attachment type for deterministic routing
- Word attachments (`doc`, `docx`) are supported through Word text extraction and must not trigger this rule solely by extension.
- Inline or embedded email body assets, such as `cid:` logos and tracking images, are not AP business attachments and must not trigger this rule.
- Known non-payable filing notices may be exempted by workflow rule conditions when the attachment is supporting evidence rather than an invoice to route for payment.
- Ben E Keith, ACH, and auto-draft notices with unsupported attachment types must follow their configured filing rules when their document type or derived flags are otherwise clear.
- Ben E Keith notices and Ben E Keith flagged invoice/payment evidence file before file-type and PDF-readability exceptions, including `.txt` integration attachments, unsupported evidence files, unreadable required PDFs, and low-quality PDF text.

### Link-Only Invoice
If invoice is only available by link:
- outcome: `ESCALATE`
- escalate label: `LINK-ONLY`
- reason: agent cannot access invoice attachment in local workflow
- This hard exception does not require property identity, address, or business-unit signals; missing automatic-routing fields must not obscure the configured `ESCALATE_LINK_ONLY` destination.
- Generic payment portal, enrollment, or informational notices are not link-only invoices unless invoice retrieval is link-only and no usable invoice attachment is present.
- In local processing, deterministic code must set the link-only observed fact when email body text includes both a URL and payment-portal language (for example `log in`, `pay bill`, or `bill is due`) and no invoice attachment is present, even if extractor output misses that signal.

### Contractor Timesheet With No Invoice
If a validated extraction batch contains a contractor timesheet, time sheet, time-entry detail, hourly detail, shift report, actual hours worked, hours worked, or staffing-hours document item and no item classified as `invoice`:
- outcome: `ESCALATE`
- escalate label: `CONTRACTOR-TIMESHEET`
- destination: configured `ESCALATE_CONTRACTOR_TIMESHEET` destination
- reason: contractor timesheet or time-detail document has no invoice in the run
- This rule matches the derived `contractor_timesheet_no_invoice` document flag.
- Invoice packages with separate timesheet or time-detail backup continue to match `hard_separate_lien_waiver` and route to `ESCALATE_MULTI_PDF_MERGE`.
- The flag is derived by Python batch normalization and must not be returned by extractors.

### Contract or Pay Application
If email is a contract or pay application:
- outcome: `ESCALATE`
- escalate label: `CONTRACT-PAY-APP`
- reason: high-risk document type

### Vendor Question or Payment Inquiry
If vendor inquiry signals are present (including inquiry language in email body/subject even when `document_type` is `invoice`):
- outcome: `ESCALATE`
- escalate label: `VENDOR-QUESTION`
- reason: vendor inquiry requires manual research or response
- Rule match must key off the derived `vendor_inquiry` document flag, not only `document_type`.

### Wrong Destination Reply
If a recipient replies or forwards the message back saying they are the wrong person, should not have received the email, or the email should be escalated because it went to the wrong destination:
- outcome: `ESCALATE`
- escalate label: `WRONG-DESTINATION`
- destination: configured `ESCALATE_WRONG_DESTINATION` destination
- reason: wrong-destination recipient escalation requires manual ESCALATE
- Rule match must key off the derived `wrong_destination` document flag.

### Past Due or Overdue Invoice Notice
If the current invoice or document is itself a past-due, overdue, or collection notice:
- outcome: `ESCALATE`
- escalate label: `PAST-DUE`
- destination: configured `ESCALATE_PAST_DUE` destination
- reason: past due invoice notice requires manual escalation
- Rule match must key off the derived `past_due` document flag so both `past_due_notice` classification and invoices whose current amount due is past due route consistently.
- The derived `past_due` flag means the current email subject or body explicitly calls the payable invoice past due, overdue, in collection, or equivalent.
- It must not be derived from an inferred or labeled invoice due date, payable-upon-receipt terms, copied invoice dates, prior balances, attachment-only labels, or any date comparison.
- Account-level aging summaries with unrelated past-due balances must not derive `past_due` when the current invoice amount or balance due is in the `Current` aging bucket.
- Valid `statement` and `account_summary` classifications must not derive `past_due` from invoice-like fields, due-date comparisons, account-level aging balances, or an erroneous `observed_facts.current_invoice_is_past_due=true`.

### Check Request
If email is a check request:
- if deterministic property matching resolves to configured destination `MEDIUS_PROPERTIES`, outcome: `AUTO`
- destination: matched Medius Properties destination
- reason: check request matched configured Medius property destination
- otherwise, outcome: `ESCALATE`
- destination: configured `ESCALATE_CHECK_REQUEST` destination
- reason: check request requires human ESCALATE

### Automated Non-AP Notification
If email metadata matches an enabled no-action pattern from Postgres:
- outcome: `DISCARD`
- destination: `NO_ACTION`
- reason: matched configured non-AP automated email pattern
- This rule must evaluate before attachment file-type exceptions so allowlisted automated notifications with inline logos are discarded rather than escalated.

### Current Reply With No AP Action
If the latest reply in a thread is classified by validated LLM extraction as indicating no AP action:
- outcome: `DISCARD`
- destination: `NO_ACTION`
- reason: latest reply indicates no AP action
- The LLM may classify only what `email.latest_body_text` indicates; quoted history is background and must not create current invoice, link-only, vendor-question, wrong-destination, or property-routing facts by itself.
- Deterministic code must still require parser-derived thread context, quoted history when configured, allowed sender-domain policy when configured, and no current item attachments.
- Deterministic code must make the final `DISCARD` decision; the LLM must not choose the destination or outcome.

### Appointment Informational Notice
If validated LLM extraction classifies the current email as informational about an appointment or service visit:
- outcome: `DISCARD`
- destination: `NO_ACTION`
- reason: LLM classified current email as informational appointment notice
- Applies to non-payable appointment confirmations, reminders, follow-ups, upcoming appointments, scheduled service visits, technician visits, reschedule notices, and similar appointment/service-visit communications.
- Must not apply to invoices, statements, account summaries, payment links, past-due notices, vendor inquiries, wrong-destination replies, contracts, pay applications, check requests, unsupported attachments, ACH or auto-draft notices, Ben E Keith notices, lien releases, or conflicting/low-quality inputs.
- The LLM may classify only the source-observable appointment fact. Deterministic code must make the final `DISCARD` decision from the validated observed fact and workflow rule configuration.

### Invoice Over Amount Threshold
If invoice amount is greater than the configured threshold:
- derive the invoice's normal deterministic destination from configured routing rules
- if the normal destination is `MEDIUS_PROPERTIES` and the invoice has an explicit `PROJECT NO` or `PROJECT NUMBER` value extracted as `invoice.project_number`, do not match this rule and continue normal automatic routing
- a `JOB NO`, `JOB NUMBER`, or extracted `invoice.job_number` value must not satisfy the Medius Properties exemption
- if the normal destination is any other configured automatic destination, outcome: `ESCALATE`
- if the normal destination is `MEDIUS_PROPERTIES` but no explicit project number was extracted, outcome: `ESCALATE`
- destination: configured `ESCALATE_OVER_10000` destination with `OVER-10000` label
- reason: invoice amount exceeds configured threshold and normal destination is not Medius Properties with a project number exemption
- if no usable normal destination exists, continue to the existing explicit `ESCALATE` path such as unmatched building or fallback

### Zero-Dollar Invoice
If invoice amount is exactly `0`:
- derive the invoice's normal deterministic destination from configured routing rules
- if a usable normal automatic destination exists, outcome: `ESCALATE`
- destination: configured `ESCALATE_0_DOLLAR_INVOICE` destination with `0-DOLLAR-INVOICE` label
- reason: invoice amount is zero and normal destination would auto-route
- if no usable normal destination exists, continue to the existing explicit `ESCALATE` path such as unmatched building or fallback
- this rule evaluates before normal automatic property routing, including multifamily routing

### Multifamily Asset
If an invoice matches a configured asset with `asset_type = 'Multifamily'`:
- outcome: `AUTO`
- destination: configured `MEDIUS_MF` destination
- reason: matched multifamily asset routes to Medius Multifamily
- This rule evaluates before invoice amount threshold escalation so high-dollar multifamily invoices route to `MEDIUS_MF` when no earlier hard exception, duplicate, or past-due email rule matches.

### Duplicate
If duplicate is suspected:
- outcome: `ESCALATE`
- escalate label: `DUPLICATE-SUSPECTED`
- reason: duplicate candidate found
- duplicate status `suspected` means another persisted invoice exists for a different `idempotency_key` with the same normalized vendor name, normalized invoice number, and exact invoice date
- duplicate status `suspected` routes to `ESCALATE`; `confirmed` is no longer produced or required by routing policy

### Statement
If document is statement or account summary:
- default local outcome: `FILE`
- destination: configured `ESCALATE Statement` subfolder
- reason: statement or account summary
- Receipt-only documents must be normalized by extraction to `account_summary` so they follow this same deterministic `FILE` rule.

The statement outcome must be configurable as `FILE` or `DISCARD`.

### ACH or Auto-Draft Notice
If document is ACH or auto-draft notice:
- default local outcome: `FILE`
- destination: configured `ACH` subfolder
- reason: ACH or auto-draft notice

### Ben E Keith Notice
If document is Ben E Keith notice:
- default local outcome: `FILE`
- destination: configured Ben E Keith folder
- reason: Ben E Keith notice

### Informational Property Notice
If an email is an informational non-invoice property notice and the property match resolves to an active routing destination:
- outcome: `AUTO`
- destination: matched property's configured routing destination
- reason: informational property notice matched configured property destination
- Must not apply to invoices, statements, account summaries, payment links, past-due notices, vendor inquiries, contracts, pay applications, check requests, unsupported attachments, or conflicting/low-quality inputs.

### Unmatched Building
If an invoice or configured informational property notice has property/building signals but no routing table match:
- outcome: `ESCALATE`
- escalate label: `UNMATCHED-BUILDING`
- reason: unmatched building in routing table

## Normal Routing

### Hillwood-Owned Property
If matched property is Hillwood-owned:
- outcome: `AUTO`
- destination: configured Hillwood Medius destination
- Matching evidence may come from email body fields, extracted attachment text fields, or both.
- Address alias matching must normalize common street suffix variants such as `PARKWAY`, `PKWY`, and `PWKY`.

### External PM Property
If matched property is investor-managed:
- outcome: `AUTO`
- destination: configured routing table destination

## Confidence Rule

If extraction confidence is below the configured threshold:
- outcome: no direct outcome change
- reason: threshold comparison is recorded for audit and observability only

## Fallback Rule

If no rule applies:
- outcome: `ESCALATE`
- reason: no deterministic routing rule matched

## Acceptance Criteria

- Hard exception rules are evaluated before normal routing.
- Duplicate detection is evaluated before routing table matches.
- Routing destinations are read from Postgres.
- Asset routing and dashboard lookup reads use `vw_asset_lookup`.
- Custom asset rows in `asset_custom` may route directly to their configured active `destination_code`.
- Custom asset rows with missing, unknown, or inactive destinations must not auto-route and must remain explicit `ESCALATE` candidates.
- Amount thresholds are read from Postgres.
- Confidence thresholds are read from Postgres.
- Low confidence alone does not force `ESCALATE`.
- A clean Hillwood-owned property invoice routes to configured Hillwood Medius destination.
- A clean external PM invoice routes to the configured PM destination.
- Property matching accepts property code, property name, building name, tenant name, bill-to address, service/shipping address, and other normalized property-related signals from email or attached invoice text.
- Property matching guidance preserves explicit visible asset names, including `Hillwood Commons II`, and does not convert them to a different canonical asset such as `Heritage Commons II` because of conflicting bill-to or address evidence.
- Database property matching uses the normalized `property_lookup` object produced by extraction directly. It must not re-normalize invoice fields into a separate property lookup query.
- Database property matching sends all extracted `property_lookup` arrays and structured `address_candidates` to Postgres and scores property code, property or tenant/building name, and address candidates before selecting top candidates. Address candidate order is meaningful: earlier address candidates receive priority because extraction orders service/property/site/shipping/delivery before billing/bill-to.
- Extracted property address lookup candidates should include both street-only and complete address strings when both are explicitly visible, with service/property/site/shipping/delivery candidates ordered before billing or bill-to candidates.
- Database address scoring must rank each structured address candidate by deterministic component score plus candidate priority weight. It must not combine the best street from one candidate with the best city, state, or ZIP from another candidate to create a stronger address match.
- Property code normalization treats common formatting variants equivalently (for example `HC-2`, `HC 2`, and `HC2`).
- Multi-invoice PDFs, lien waiver merge cases, link-only invoices, contractor timesheets with no invoice, unsupported image/Excel business attachments, contracts, and pay applications route to `ESCALATE`.
- Link-only invoice escalation uses the configured `hard_link_only_invoice` rule and `ESCALATE_LINK_ONLY` destination even when no property or business-unit identity is present.
- Check requests route automatically only when they match configured `MEDIUS_PROPERTIES`; all other check requests route to `ESCALATE_CHECK_REQUEST`.
- Inline email body images are persisted for audit/dashboard rendering but excluded from extraction attachment inputs and wrong-file-type evaluation.
- Unsupported attachment types do not override configured filing rules for clear non-payable Ben E Keith, ACH, or auto-draft notices.
- Multi-invoice PDF, split multi-PDF, merge-required, link-only, wrong-file-type, contractor-timesheet-no-invoice, contract/pay-app, duplicate, vendor-question, wrong-destination, past-due, multifamily, and unmatched-building scenarios map to configured escalate labels.
- Current-invoice past due or overdue notices route to `ESCALATE` with destination `ESCALATE_PAST_DUE`.
- Invoices that only contain an account aging table with unrelated past-due balances do not route to `ESCALATE_PAST_DUE` when the current invoice amount or balance due is in the `Current` bucket.
- Wrong-destination recipient replies route to `ESCALATE` with destination `ESCALATE_WRONG_DESTINATION`.
- Emails with payment-portal links and no invoice attachment deterministically normalize to link-only invoice and route to `ESCALATE`.
- Configured automated non-AP notifications route to `DISCARD` with destination `NO_ACTION`.
- Current replies whose latest body is classified by validated LLM extraction as a no-action acknowledgement, courtesy/social reply, or confirmation that the recipient will handle/process the prior item route to `DISCARD` with destination `NO_ACTION` when deterministic thread, sender, and attachment gates pass.
- For current replies, an internal `@hillwood.com` sender is a positive extraction indicator for social/no-action classification, not a routing decision by itself.
- Short internal replies such as `Thank you. I just sent it.`, `Received, thank you.`, or `I resent it.` should set `latest_reply_indicates_no_ap_action=true` only when the latest body does not ask a question, report a wrong destination, cite a current attachment as AP evidence, or introduce new invoice, payment, statement, link, vendor-question, or property-routing facts.
- Quoted statement, invoice, or vendor-question history must not override a latest-body no-action acknowledgement when extracting the current item.
- Appointment confirmations, reminders, and follow-ups classified by validated LLM extraction as informational appointment notices route to `DISCARD` with destination `NO_ACTION` when configured blocked AP-risk flags are absent.
- Invoices over the configured amount threshold route automatically only when their normal deterministic destination is `MEDIUS_PROPERTIES` and an explicit project number was extracted from a `PROJECT NO` or `PROJECT NUMBER` signal.
- Invoices over the configured amount threshold whose normal deterministic destination is not `MEDIUS_PROPERTIES`, or whose normal deterministic destination is `MEDIUS_PROPERTIES` without an explicit project number, route to configured `ESCALATE_OVER_10000`, except matched multifamily assets route to `MEDIUS_MF` first.
- Zero-dollar invoices whose normal deterministic destination would auto-route instead route to configured `ESCALATE_0_DOLLAR_INVOICE`.
- Mixed extracted document-item outcomes or destinations aggregate to `ESCALATE_SPLIT_MULTI_PDF`.
- Statements route to the configured statement outcome.
- ACH, auto-draft, and Ben E Keith notices route to configured local folders.
- Informational non-invoice property notices with a usable asset/address match forward to the matched asset's configured destination.
- Informational non-invoice property notices with property or address evidence but no active asset match route to `ESCALATE_UNMATCHED_BUILDING`.
- Informational property notice routing does not bypass hard exceptions, duplicate detection, or invoice automatic-routing requirements.
- Unknown buildings route to `ESCALATE`.
- Invoices containing both account/bill-to and deliver-to/site addresses use the stronger deliver-to/site candidate first for deterministic matching.
- Invoices with no mapped property code/name/site/service/deliver-to/ship-to signal may route from a bill-to or customer-account address when exactly one active asset candidate qualifies above the configured score threshold and no stronger conflicting serviced-property evidence exists.
- Unresolved project, job, or property text does not block bill-to/customer-account address fallback when no configured active asset matches that text and the address selects exactly one active candidate above the configured score threshold.
- Bill-to-only address fallback is blocked when no candidate exists, the selected candidate is below `property_match_min_score`, multiple near-equivalent active candidates remain, the selected destination is inactive, or stronger property/site/service/deliver-to/ship-to evidence maps elsewhere.
- If no active asset matches the extracted candidate addresses, the result remains explicit unmatched-building `ESCALATE`; the LLM must not invent asset codes or destinations.
- Fuzzy retrieval returns top-N candidates (default `5`) from `asset`.
- Deterministic asset gate only requires the top candidate to meet the configured score threshold.
- Candidate margin and runner-up score are retained for audit, but ties or near-ties do not block routing when the top candidate meets the score threshold.
- A clean invoice containing `HC-2` must normalize to `HC2` and route to the configured HC2 ownership destination when the invoice address also supports the asset match, provided no higher-priority rule or missing required field blocks automation.
- A clean invoice whose source visibly says `HC2`, `HC-2`, or `Heritage Commons II` may normalize to `Heritage Commons II` / `HC2`; this exact-code behavior does not allow visible `Hillwood Commons II` to be converted to `Heritage Commons II`.
