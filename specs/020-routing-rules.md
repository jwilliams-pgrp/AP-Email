# 020 - Routing Rules Spec

## Purpose

Define deterministic routing behavior for AP inbox emails.

## Principle

The LLM extracts facts. The rules engine makes the routing decision. Mutable workflow policy comes from Postgres tables.

## Routing Priority

1. Hard exception rules
2. Duplicate detection
3. Routing table match
4. Confidence threshold
5. `REVIEW` fallback

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

## Hard Exception Rules

### Multi-Invoice PDF
If an attachment contains multiple invoices:
- outcome: `REVIEW`
- reason: requires manual split

### Invoice Plus Lien Waiver
If invoice and lien waiver must be merged:
- outcome: `REVIEW`
- reason: requires manual merge

### Link-Only Invoice
If invoice is only available by link:
- outcome: `REVIEW`
- reason: agent cannot access invoice attachment in local workflow

### Contract or Pay Application
If email is a contract or pay application:
- outcome: `REVIEW`
- reason: high-risk document type

### Invoice Over Amount Threshold
If invoice amount is greater than the configured threshold:
- default local outcome: `FILE`
- destination: configured lien release folder
- reason: invoice amount exceeds configured threshold; hold for lien release from Tiffany

### Duplicate
If duplicate is suspected:
- outcome: `REVIEW`
- reason: duplicate candidate found

### Statement
If document is statement or account summary:
- default local outcome: `FILE`
- reason: statement or account summary

The statement outcome must be configurable as `FILE` or `DISCARD`.

### ACH or Auto-Draft Notice
If document is ACH or auto-draft notice:
- default local outcome: `FILE`
- destination: configured ACH folder
- reason: ACH or auto-draft notice

### Ben E Keith Notice
If document is Ben E Keith notice:
- default local outcome: `FILE`
- destination: configured Ben E Keith folder
- reason: Ben E Keith notice

### Sold Property
If property is sold:
- outcome: `FLAG`
- reason: sold property

## Normal Routing

### Hillwood-Owned Property
If matched property is Hillwood-owned:
- outcome: `AUTO`
- destination: configured Hillwood Medius destination

### External PM Property
If matched property is investor-managed:
- outcome: `AUTO`
- destination: configured routing table destination

### ALC
If bill-to indicates ALC:
- outcome: `AUTO`
- destination: configured ALC destination

### Multifamily
If bill-to indicates Multifamily:
- outcome: `AUTO`
- destination: configured Multifamily destination

## Confidence Rule

If extraction confidence is below the configured threshold:
- outcome: `REVIEW`
- reason: confidence below configured threshold

## Fallback Rule

If no rule applies:
- outcome: `REVIEW`
- reason: no deterministic routing rule matched

## Acceptance Criteria

- Hard exception rules are evaluated before normal routing.
- Duplicate detection is evaluated before routing table matches.
- Routing destinations are read from Postgres.
- Amount thresholds are read from Postgres.
- Confidence thresholds are read from Postgres.
- A clean Hillwood-owned property invoice routes to configured Hillwood Medius destination.
- A clean external PM invoice routes to the configured PM destination.
- ALC and Multifamily invoices route to configured destinations.
- Multi-invoice PDFs, lien waiver merge cases, link-only invoices, contracts, and pay applications route to `REVIEW`.
- Invoices over the configured amount threshold route to the configured lien release folder by default.
- Statements route to the configured statement outcome.
- ACH, auto-draft, and Ben E Keith notices route to configured local folders.
- Sold properties route to `FLAG`.
- Unknown buildings route to `REVIEW`.
