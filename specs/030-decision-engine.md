# 030 - Decision Engine Spec

## Purpose

Convert validated email and invoice facts into a safe routing decision.

## Inputs

- email metadata
- attachment metadata
- extracted invoice fields
- document classification
- routing table match
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
- dry_run

## Requirements

- Every processed email must produce one final decision.
- Every decision must be audit logged.
- Decisions must be deterministic for the same input data and rule version.
- LLM output must be schema validated before use.
- Missing required fields must result in `REVIEW`.
- The engine must fail loudly if required workflow configuration is missing.
- The engine must not silently substitute hard-coded business defaults.

## Table-Driven Boundary

Decision code owns:
- evaluation order
- supported condition types
- schema validation
- deterministic matching
- safety invariants

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
- ALC invoice routes to configured ALC destination.
- Multifamily invoice routes to configured Multifamily destination.
- Duplicate invoice routes to `REVIEW`.
- Unknown building routes to `REVIEW`.
- Low confidence routes to `REVIEW`.
- High-dollar invoice routes to configured lien release folder using configured amount threshold.
- Statement routes to configured statement outcome.
- Missing required workflow config raises an explicit error in tests.
- The same input and same workflow rule version produce the same decision.
