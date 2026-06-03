from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable
from uuid import uuid4

from ap_automation.models.decision import (
    Decision,
    Destination,
    NoActionEmailPattern,
    PropertyMatch,
    PropertyMatchCandidate,
    PropertyMatchEvaluation,
    WorkflowRule,
)
from ap_automation.models.extraction import ExtractionPayload


class PostgresRepository:
    """Postgres-backed policy and operational repository.

    This class keeps SQL at the repository boundary. Decision code receives
    typed policy rows and does not know table details.
    """

    def __init__(self, dsn: str) -> None:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("Install the postgres extra to use PostgresRepository: pip install -e .[postgres]") from exc

        self._psycopg = psycopg
        self._dict_row = dict_row
        self._dsn = dsn

    def get_runtime_config(self) -> dict[str, Any]:
        with self._connect() as conn:
            rows = conn.execute("select config_key, config_value from runtime_config").fetchall()
        return {row["config_key"]: row["config_value"] for row in rows}

    def get_active_workflow_rules(self) -> list[WorkflowRule]:
        sql = """
            select
              wr.rule_code,
              wr.rule_name,
              wr.priority,
              wr.condition_type,
              wr.outcome::text as outcome,
              wr.destination_code,
              wr.reason_template,
              wr.version,
              coalesce(jsonb_object_agg(wrc.condition_key, wrc.condition_value)
                filter (where wrc.condition_key is not null), '{}'::jsonb) as conditions
            from workflow_rules wr
            left join workflow_rule_conditions wrc on wrc.rule_code = wr.rule_code
            where wr.enabled = true
              and wr.effective_start <= current_date
              and (wr.effective_end is null or wr.effective_end >= current_date)
            group by wr.rule_code
            order by wr.priority
        """
        with self._connect() as conn:
            rows = conn.execute(sql).fetchall()
        return [
            WorkflowRule(
                rule_code=row["rule_code"],
                rule_name=row["rule_name"],
                priority=row["priority"],
                condition_type=row["condition_type"],
                outcome=_normalize_decision_outcome(row["outcome"]),
                destination_code=row["destination_code"],
                reason_template=row["reason_template"],
                version=row["version"],
                conditions=row["conditions"],
            )
            for row in rows
        ]

    def get_destination(self, destination_code: str) -> Destination:
        sql = """
            select destination_code, display_name, email_address,
                   parent_folder, label, send_teams_message, send_email, active
            from routing_destinations
            where destination_code = %s
        """
        with self._connect() as conn:
            row = conn.execute(sql, (destination_code,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown routing destination: {destination_code}")
        return Destination(**row)

    def evaluate_property_match(self, extraction: ExtractionPayload) -> PropertyMatchEvaluation:
        raw_signals, query_values = _property_query_signals(extraction)
        runtime = self.get_runtime_config()
        top_n = int(runtime.get("property_match_top_n", 5))
        min_score = float(runtime.get("property_match_min_score", 0.45))
        candidates, lookup_audit = self._retrieve_property_candidates(query_values, top_n=top_n)
        gate = {
            "top_n": top_n,
            "min_score": min_score,
            "passed": False,
            "reason": "No candidates",
        }
        if not candidates:
            return PropertyMatchEvaluation(
                property_match=None,
                standardized_signals={"raw_input_signals": raw_signals, "query_values": query_values},
                candidates=(),
                llm_advisory={"used": False, "candidate_property_codes": [], "reason": "LLM advisory is not part of deterministic property matching"},
                gate=gate,
                lookup_audit=lookup_audit,
            )

        top = candidates[0]
        runner_score = candidates[1].similarity_score if len(candidates) > 1 else 0.0
        margin = top.similarity_score - runner_score
        pass_score = top.similarity_score >= min_score
        passed = pass_score
        gate = {
            **gate,
            "top_score": top.similarity_score,
            "runner_up_score": runner_score,
            "score_margin": margin,
            "pass_score": pass_score,
            "passed": passed,
            "reason": (
                "Property fuzzy gate passed"
                if passed
                else "Property fuzzy gate failed: score requirement not met"
            ),
        }

        property_match = None
        if passed:
            property_match = self._property_by_id(top.asset_id, top.matched_text)
            if property_match is None:
                passed = False
                gate = {
                    **gate,
                    "passed": False,
                    "reason": "Asset fuzzy gate failed: matched asset has no active ownership destination",
                }
        return PropertyMatchEvaluation(
            property_match=property_match,
            standardized_signals={"raw_input_signals": raw_signals, "query_values": query_values},
            candidates=tuple(candidates),
            llm_advisory={
                "used": False,
                "candidate_property_codes": [candidate.asset_alias for candidate in candidates],
                "confidence": top.similarity_score,
                "reason": "Top fuzzy candidate selected by deterministic SQL scoring and gate checks",
            },
            gate=gate,
            lookup_audit=lookup_audit,
        )

    def get_property_match_by_asset_id(self, asset_id: str, matched_alias: str | None = None) -> PropertyMatch | None:
        return self._property_by_id(asset_id, matched_alias)

    def get_asset_reference_rows(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select asset_name, asset_alias, asset_type, address
                from vw_asset_lookup
                order by asset_name nulls last, asset_alias nulls last, address nulls last
                """
            ).fetchall()
        return [
            {
                "asset_name": row["asset_name"],
                "asset_alias": row["asset_alias"],
                "asset_type": row["asset_type"],
                "address": row["address"],
            }
            for row in rows
        ]

    def _retrieve_property_candidates(self, query_values: dict[str, Any], *, top_n: int) -> tuple[list[PropertyMatchCandidate], dict[str, Any]]:
        if not any(_has_lookup_value(value) for value in query_values.values()):
            return [], {
                "sql": None,
                "sent_payload": {**query_values, "top_n": top_n},
                "returned_payload": [],
                "reason": "No property lookup query was run because no lookup values were available.",
            }
        sql = """
            with extracted as (
              select
                %s::text[] as property_codes,
                %s::text[] as property_names,
                %s::text[] as tenants,
                %s::text[] as addresses,
                %s::text[] as suites,
                %s::text[] as cities,
                %s::text[] as states,
                %s::text[] as zipcodes,
                %s::jsonb as address_candidates,
                %s::int as top_n
            ),
            normalized as (
              /*
                Normalize all input text once.

                Address matching is component-based. Street, suite, city, state, and ZIP
                are scored separately so city/state/ZIP cannot create a strong match by
                themselves when the street does not match.
              */
              select
                array(
                  select lower(regexp_replace(trim(x.value), '[^a-zA-Z0-9]+', '', 'g'))
                  from unnest(property_codes) as x(value)
                  where nullif(trim(x.value), '') is not null
                ) as property_codes,
                array(
                  select lower(regexp_replace(trim(x.value), '[^a-zA-Z0-9]+', ' ', 'g'))
                  from unnest(property_names) as x(value)
                  where nullif(trim(x.value), '') is not null
                ) as property_names,
                array(
                  select lower(regexp_replace(trim(x.value), '[^a-zA-Z0-9]+', ' ', 'g'))
                  from unnest(tenants) as x(value)
                  where nullif(trim(x.value), '') is not null
                ) as tenants,
                array(
                  select lower(regexp_replace(trim(x.value), '[^a-zA-Z0-9]+', ' ', 'g'))
                  from unnest(addresses) with ordinality as x(value, ord)
                  where nullif(trim(x.value), '') is not null
                  order by x.ord
                ) as addresses,
                array(
                  select lower(regexp_replace(trim(x.value), '[^a-zA-Z0-9]+', ' ', 'g'))
                  from unnest(suites) as x(value)
                  where nullif(trim(x.value), '') is not null
                ) as suites,
                array(
                  select lower(regexp_replace(trim(x.value), '[^a-zA-Z0-9]+', ' ', 'g'))
                  from unnest(cities) as x(value)
                  where nullif(trim(x.value), '') is not null
                ) as cities,
                array(
                  select lower(regexp_replace(trim(x.value), '[^a-zA-Z0-9]+', ' ', 'g'))
                  from unnest(states) as x(value)
                  where nullif(trim(x.value), '') is not null
                ) as states,
                array(
                  select left(regexp_replace(trim(x.value), '[^0-9]+', '', 'g'), 5)
                  from unnest(zipcodes) as x(value)
                  where nullif(trim(x.value), '') is not null
                ) as zipcodes,
                address_candidates,
                top_n
              from extracted
            ),
            normalized_address_candidates as (
              select
                coalesce((candidate->>'rank')::int, ord::int) as rank,
                lower(regexp_replace(trim(coalesce(candidate->>'label', '')), '[^a-zA-Z0-9_]+', '_', 'g')) as label,
                lower(regexp_replace(trim(coalesce(candidate->>'street', '')), '[^a-zA-Z0-9]+', ' ', 'g')) as street,
                lower(regexp_replace(trim(coalesce(candidate->>'city', '')), '[^a-zA-Z0-9]+', ' ', 'g')) as city,
                lower(regexp_replace(trim(coalesce(candidate->>'state', '')), '[^a-zA-Z0-9]+', ' ', 'g')) as state,
                left(regexp_replace(trim(coalesce(candidate->>'zipcode', '')), '[^0-9]+', '', 'g'), 5) as zipcode,
                lower(regexp_replace(trim(coalesce(candidate->>'normalized_address', '')), '[^a-zA-Z0-9]+', ' ', 'g')) as normalized_address,
                case
                  when coalesce((candidate->>'rank')::int, ord::int) = 1 then 1.0
                  when coalesce((candidate->>'rank')::int, ord::int) = 2 then 0.75
                  else 0.65
                end * case
                  when lower(coalesce(candidate->>'label', '')) in ('deliver_to', 'ship_to', 'service_location', 'site', 'property') then 1.0
                  when lower(coalesce(candidate->>'label', '')) = 'customer_account' then 0.85
                  when lower(coalesce(candidate->>'label', '')) = 'bill_to' then 0.70
                  else 0.60
                end as priority_weight
              from normalized n
              cross join lateral jsonb_array_elements(n.address_candidates) with ordinality as item(candidate, ord)
              where jsonb_typeof(n.address_candidates) = 'array'
            ),
            candidates as (
              /*
                Pull configured asset lookup rows, normalizing candidate fields
                here so the scoring CTE stays focused on scoring.

                full_address_line is retained for audit/debug output only. It is not the
                main scoring target because full-line fuzzy matching can overvalue partial
                city/state/ZIP matches.
              */
              select
                a.asset_lookup_id as asset_id,
                a.asset_source,
                a.asset_lookup_id,
                nullif(a.asset_alias, '') as asset_alias,
                a.asset_name,
                a.ownership,
                a.asset_type,
                a.market_name,
                a.market_area,
                null::text as business_unit_code,
                a.address,
                a.tenants,
                a.destination_code,
                a.destination_active,

                lower(regexp_replace(trim(coalesce(a.asset_alias, '')), '[^a-zA-Z0-9]+', '', 'g')) as norm_property_code,
                lower(regexp_replace(trim(coalesce(a.asset_name, '')), '[^a-zA-Z0-9]+', ' ', 'g')) as norm_property_name,
                lower(regexp_replace(trim(coalesce(a.tenants::text, '')), '[^a-zA-Z0-9]+', ' ', 'g')) as norm_tenant,
                ''::text as norm_suite,
                lower(regexp_replace(trim(coalesce(a.address, '')), '[^a-zA-Z0-9]+', ' ', 'g')) as full_address_line
              from vw_asset_lookup a
            ),
            parsed_candidates as (
              /*
                asset.address is the only canonical address source available today.
                Parse common "street, city, state ZIP" rows in SQL so exact street,
                city, state, and ZIP evidence can score as exact component matches
                without requiring asset table changes.
              */
              select
                c.*,
                lower(regexp_replace(trim(coalesce(nullif(split_part(c.address, ',', 1), ''), c.address, '')), '[^a-zA-Z0-9]+', ' ', 'g')) as norm_address,
                lower(regexp_replace(trim(case when c.address like '%%,%%' then split_part(c.address, ',', 2) else '' end), '[^a-zA-Z0-9]+', ' ', 'g')) as norm_city,
                case lower(regexp_replace(split_part(trim(
                  case
                    when c.address like '%%,%%,%%' then split_part(c.address, ',', 3)
                    else ''
                  end
                ), ' ', 1), '[^a-zA-Z]+', '', 'g'))
                  when 'alabama' then 'al'
                  when 'alaska' then 'ak'
                  when 'arizona' then 'az'
                  when 'arkansas' then 'ar'
                  when 'california' then 'ca'
                  when 'colorado' then 'co'
                  when 'connecticut' then 'ct'
                  when 'delaware' then 'de'
                  when 'florida' then 'fl'
                  when 'georgia' then 'ga'
                  when 'hawaii' then 'hi'
                  when 'idaho' then 'id'
                  when 'illinois' then 'il'
                  when 'indiana' then 'in'
                  when 'iowa' then 'ia'
                  when 'kansas' then 'ks'
                  when 'kentucky' then 'ky'
                  when 'louisiana' then 'la'
                  when 'maine' then 'me'
                  when 'maryland' then 'md'
                  when 'massachusetts' then 'ma'
                  when 'michigan' then 'mi'
                  when 'minnesota' then 'mn'
                  when 'mississippi' then 'ms'
                  when 'missouri' then 'mo'
                  when 'montana' then 'mt'
                  when 'nebraska' then 'ne'
                  when 'nevada' then 'nv'
                  when 'newhampshire' then 'nh'
                  when 'newjersey' then 'nj'
                  when 'newmexico' then 'nm'
                  when 'newyork' then 'ny'
                  when 'northcarolina' then 'nc'
                  when 'northdakota' then 'nd'
                  when 'ohio' then 'oh'
                  when 'oklahoma' then 'ok'
                  when 'oregon' then 'or'
                  when 'pennsylvania' then 'pa'
                  when 'rhodeisland' then 'ri'
                  when 'southcarolina' then 'sc'
                  when 'southdakota' then 'sd'
                  when 'tennessee' then 'tn'
                  when 'texas' then 'tx'
                  when 'utah' then 'ut'
                  when 'vermont' then 'vt'
                  when 'virginia' then 'va'
                  when 'washington' then 'wa'
                  when 'westvirginia' then 'wv'
                  when 'wisconsin' then 'wi'
                  when 'wyoming' then 'wy'
                  else lower(regexp_replace(split_part(trim(
                    case
                      when c.address like '%%,%%,%%' then split_part(c.address, ',', 3)
                      else ''
                    end
                  ), ' ', 1), '[^a-zA-Z]+', '', 'g'))
                end as norm_state,
                coalesce(substring(c.address from '([0-9]{5})(?:-[0-9]{4})?\\D*$'), '') as norm_zipcode
              from candidates c
            ),
            scored as (
              select
                c.*,
                n.top_n,
                cardinality(n.suites) as suite_count,

                coalesce((
                  select max(
                    case
                      when c.norm_property_code = q.value then 1.0
                      else similarity(c.norm_property_code, q.value)
                    end
                  )
                  from unnest(n.property_codes) as q(value)
                ), 0) as property_code_score,

                greatest(
                  coalesce((
                    select max(
                      case
                        when c.norm_property_name = q.value then 1.0
                        else similarity(c.norm_property_name, q.value)
                      end
                    )
                    from unnest(n.property_names) as q(value)
                  ), 0),
                  coalesce((
                    select max(
                      case
                        when c.norm_tenant = q.value then 1.0
                        else similarity(c.norm_tenant, q.value)
                      end
                    )
                    from unnest(n.tenants) as q(value)
                  ), 0)
                ) as name_score,

                coalesce((
                  select max(
                    case
                      when c.norm_address = q.value then 1.0 * q.priority_weight
                      else similarity(c.norm_address, q.value) * q.priority_weight
                    end
                  )
                  from unnest(n.addresses) with ordinality as raw(value, ord)
                  cross join lateral (
                    select
                      raw.value,
                      case
                        when raw.ord = 1 then 1.0
                        when raw.ord = 2 then 0.75
                        else 0.65
                      end as priority_weight
                  ) as q
                ), 0) as street_score,

                coalesce((
                  select max(
                    case
                      when c.norm_suite = q.value then 1.0
                      else similarity(c.norm_suite, q.value)
                    end
                  )
                  from unnest(n.suites) as q(value)
                ), 0) as suite_score,

                coalesce((
                  select max(
                    case
                      when c.norm_city = q.value then 1.0
                      else similarity(c.norm_city, q.value)
                    end
                  )
                  from unnest(n.cities) as q(value)
                ), 0) as city_score,

                coalesce((
                  select max(case when c.norm_state = q.value then 1.0 else 0.0 end)
                  from unnest(n.states) as q(value)
                ), 0) as state_score,

                coalesce((
                  select max(case when c.norm_zipcode = q.value then 1.0 else 0.0 end)
                  from unnest(n.zipcodes) as q(value)
                ), 0) as zipcode_score
              from parsed_candidates c
              cross join normalized n
            ),
            candidate_scored as (
              select
                s.*,
                coalesce((
                  select max(
                    (
                      (
                        (case
                          when s.norm_address = q.street or s.norm_address = q.normalized_address then 1.0
                          else greatest(similarity(s.norm_address, q.street), similarity(s.norm_address, q.normalized_address))
                        end) * 8.0
                        + (case when nullif(q.city, '') is null then 0.0 when s.norm_city = q.city then 1.0 else similarity(s.norm_city, q.city) end) * 1.0
                        + (case when nullif(q.state, '') is null then 0.0 when s.norm_state = q.state then 1.0 else 0.0 end) * 0.75
                        + (case when nullif(q.zipcode, '') is null then 0.0 when s.norm_zipcode = q.zipcode then 1.0 else 0.0 end) * 1.5
                      )
                      / 11.25
                    ) * q.priority_weight
                  )
                  from normalized_address_candidates q
                ), 0) as structured_address_score
              from scored s
            ),
            weighted as (
              /*
                Street is the dominant address signal. Suite input is retained for
                audit but ignored for asset routing because routing is building-level.
              */
              select
                *,
                (
                  (
                    case
                      when structured_address_score > 0 then structured_address_score * 11.25
                      else street_score * 8.0
                        + city_score * 1.0
                        + state_score * 0.75
                        + zipcode_score * 1.5
                    end
                  )
                  /
                  (
                    8.0
                    + 1.0
                    + 0.75
                    + 1.5
                  )
                ) as address_score
              from candidate_scored
            ),
            final_scored as (
              /*
                Use the strongest match path: address-driven, property-code-driven, or
                property/tenant-name-driven. Missing or unused signals should not drag
                down a strong match from another path.
              */
              select
                *,
                case
                  when property_code_score = 1.0
                    then 1.0
                  when street_score >= 0.90
                   and city_score = 1
                   and state_score = 1
                   and zipcode_score = 1
                    then 1.0
                  when address_score >= greatest(property_code_score, name_score)
                    then address_score
                  when property_code_score >= greatest(address_score, name_score)
                    then property_code_score * 0.75
                       + address_score * 0.15
                       + name_score * 0.10
                  else name_score * 0.70
                     + address_score * 0.20
                     + property_code_score * 0.10
                end as final_score,
                case
                  when property_code_score = 1.0 then 'property_code'
                  when address_score >= greatest(property_code_score, name_score) then 'address_components'
                  when property_code_score >= greatest(address_score, name_score) then 'property_code'
                  else 'property_or_tenant_name'
                end as matched_column
              from weighted
            ),
            filtered as (
              /*
                Prevent city/state/ZIP-only rows from ranking near the top. A candidate
                must have a reasonable street match, strong code match, or strong
                property/tenant name match.
              */
              select *
              from final_scored
              where street_score >= 0.45
                 or property_code_score >= 0.65
                 or name_score >= 0.65
            ),
            best_property_match as (
              select distinct on (asset_id)
                asset_id,
                asset_source,
                asset_lookup_id,
                asset_alias,
                asset_name,
                ownership,
                asset_type,
                market_name,
                market_area,
                destination_code,
                destination_active,
                tenants,
                address,
                full_address_line,
                coalesce(full_address_line, asset_name, asset_alias) as candidate_text,
                matched_column,
                property_code_score,
                name_score,
                street_score,
                suite_score,
                city_score,
                state_score,
                zipcode_score,
                address_score,
                structured_address_score,
                final_score as score
              from filtered
              order by asset_id, final_score desc, street_score desc, zipcode_score desc
            )
            select
              asset_id,
              asset_source,
              asset_lookup_id,
              asset_alias,
              asset_name,
              ownership,
              asset_type,
              market_name,
              market_area,
              destination_code,
              destination_active,
              tenants,
              address,
              full_address_line,
              candidate_text,
              matched_column,
              property_code_score,
              name_score,
              street_score,
              suite_score,
              city_score,
              state_score,
              zipcode_score,
              address_score,
              structured_address_score,
              score
            from best_property_match
            order by score desc, street_score desc, zipcode_score desc
            limit (select top_n from normalized)
        """
        sent_payload = {**query_values, "top_n": top_n}
        execute_params = (
            query_values["property_codes"],
            query_values["property_names"],
            query_values["tenants"],
            query_values["addresses"],
            query_values["suites"],
            query_values["cities"],
            query_values["states"],
            query_values["zipcodes"],
            json.dumps(query_values.get("address_candidates", [])),
            top_n,
        )
        with self._connect() as conn:
            rows = conn.execute(sql, execute_params).fetchall()
        returned_payload = [dict(row) for row in rows]
        return [
            PropertyMatchCandidate(
                asset_id=row["asset_id"],
                asset_source=row["asset_source"] if "asset_source" in row else "asset",
                asset_lookup_id=row["asset_lookup_id"] if "asset_lookup_id" in row else row["asset_id"],
                asset_alias=row["asset_alias"],
                asset_name=row["asset_name"],
                destination_code=row["destination_code"] if row["destination_active"] else None,
                matched_text=row["candidate_text"],
                matched_column=row["matched_column"],
                similarity_score=float(row["score"] or 0.0),
                ownership=row["ownership"],
                asset_type=row["asset_type"],
                market_name=row["market_name"],
                market_area=row["market_area"],
            )
            for row in rows
        ], {
            "sql": _compact_sql(sql),
            "sent_payload": sent_payload,
            "returned_payload": returned_payload,
        }

    def _property_by_id(self, property_id: str, matched_alias: str) -> PropertyMatch | None:
        sql = """
            select
              a.asset_lookup_id as asset_id,
              a.asset_source,
              a.asset_lookup_id,
              nullif(a.asset_alias, '') as asset_alias,
              a.asset_name,
              coalesce(a.ownership, 'unknown') as ownership_type,
              a.ownership,
              a.asset_type,
              null::text as business_unit_code,
              a.destination_code as destination_code_value,
              a.market_name,
              a.market_area
            from vw_asset_lookup a
            where a.asset_lookup_id = %s
              and a.destination_active = true
              and a.destination_code is not null
            limit 1
        """
        with self._connect() as conn:
            row = conn.execute(sql, (property_id,)).fetchone()
        if row is None:
            return None
        return PropertyMatch(**dict(row), matched_alias=matched_alias)

    def find_duplicate_status(self, extraction: ExtractionPayload, idempotency_key: str) -> str | None:
        vendor = _normalize(extraction.invoice.vendor_name)
        invoice_number = _normalize(extraction.invoice.invoice_number)
        invoice_date = extraction.invoice.invoice_date

        if not (vendor and invoice_number and invoice_date):
            return None

        with self._connect() as conn:
            suspected = conn.execute(
                """
                select 1
                from invoices i
                join emails em on em.email_id = i.email_id
                where em.idempotency_key <> %s
                  and regexp_replace(lower(coalesce(i.vendor_name, '')), '\\s+', ' ', 'g') = %s
                  and regexp_replace(lower(coalesce(i.invoice_number, '')), '\\s+', ' ', 'g') = %s
                  and i.invoice_date = %s
                limit 1
                """,
                (idempotency_key, vendor, invoice_number, invoice_date),
            ).fetchone()
            if suspected:
                return "suspected"

        return None

    def get_active_no_action_email_patterns(self) -> list[NoActionEmailPattern]:
        sql = """
            select
              pattern_id::text as pattern_id,
              pattern_name,
              sender_email_equals,
              sender_domain_equals,
              subject_regex,
              body_regex,
              reason_template,
              priority
            from no_action_email_patterns
            where enabled = true
              and effective_start <= current_date
              and (effective_end is null or effective_end >= current_date)
            order by priority, pattern_name
        """
        with self._connect() as conn:
            rows = conn.execute(sql).fetchall()
        return [
            NoActionEmailPattern(
                pattern_id=row["pattern_id"],
                pattern_name=row["pattern_name"],
                sender_email_equals=row["sender_email_equals"],
                sender_domain_equals=row["sender_domain_equals"],
                subject_regex=row["subject_regex"],
                body_regex=row["body_regex"],
                reason_template=row["reason_template"],
                priority=row["priority"],
            )
            for row in rows
        ]

    def save_invoice_fact(self, email_id: str, extraction: ExtractionPayload, document_item_id: str | None = None) -> None:
        vendor_normalized = _normalize(extraction.invoice.vendor_name)
        invoice_number_normalized = _normalize(extraction.invoice.invoice_number)
        amount = extraction.invoice.amount
        invoice_date = extraction.invoice.invoice_date
        currency = _normalize(extraction.invoice.currency)

        fingerprint_parts = [
            vendor_normalized or "",
            invoice_number_normalized or "",
            f"{amount:.2f}" if amount is not None else "",
            invoice_date.isoformat() if invoice_date is not None else "",
            currency or "",
        ]
        fingerprint = hashlib.sha256("|".join(fingerprint_parts).encode("utf-8")).hexdigest()

        with self._connect() as conn:
            conn.execute(
                """
                insert into invoices (
                  email_id, document_item_id, vendor_name, invoice_number, invoice_date, amount, currency,
                  duplicate_fingerprint, metadata
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    email_id,
                    document_item_id,
                    extraction.invoice.vendor_name,
                    extraction.invoice.invoice_number,
                    invoice_date,
                    amount,
                    extraction.invoice.currency,
                    fingerprint,
                    _json_dumps({"source": "extraction.v1"}),
                ),
            )

    def upsert_email(self, metadata: dict[str, Any]) -> str:
        sql = """
            insert into emails (
              source_system, source_message_id, idempotency_key, subject,
              sender_email, received_at, raw_storage_path, office_web_link, metadata
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (idempotency_key) do update
            set subject = excluded.subject,
                sender_email = excluded.sender_email,
                received_at = excluded.received_at,
                raw_storage_path = excluded.raw_storage_path,
                office_web_link = excluded.office_web_link,
                metadata = excluded.metadata
            returning email_id::text
        """
        params = (
            metadata["source_system"],
            metadata["source_message_id"],
            metadata["idempotency_key"],
            metadata.get("subject"),
            metadata.get("sender_email"),
            metadata.get("received_at"),
            metadata.get("raw_storage_path"),
            metadata.get("office_web_link"),
            _json_dumps(metadata.get("metadata", {})),
        )
        with self._connect() as conn:
            return conn.execute(sql, params).fetchone()["email_id"]

    def _upsert_email(self, conn, metadata: dict[str, Any]) -> str:
        sql = """
            insert into emails (
              source_system, source_message_id, idempotency_key, subject,
              sender_email, received_at, raw_storage_path, office_web_link, metadata
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (idempotency_key) do update
            set subject = excluded.subject,
                sender_email = excluded.sender_email,
                received_at = excluded.received_at,
                raw_storage_path = excluded.raw_storage_path,
                office_web_link = excluded.office_web_link,
                metadata = excluded.metadata
            returning email_id::text
        """
        params = (
            metadata["source_system"],
            metadata["source_message_id"],
            metadata["idempotency_key"],
            metadata.get("subject"),
            metadata.get("sender_email"),
            metadata.get("received_at"),
            metadata.get("raw_storage_path"),
            metadata.get("office_web_link"),
            _json_dumps(metadata.get("metadata", {})),
        )
        return conn.execute(sql, params).fetchone()["email_id"]

    def update_email_office_web_link(self, email_id: str, office_web_link: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                update emails
                set office_web_link = %s,
                    metadata = jsonb_set(
                        coalesce(metadata, '{}'::jsonb),
                        '{office_web_link}',
                        to_jsonb(%s::text),
                        true
                    )
                where email_id = %s
                """,
                (office_web_link, office_web_link, email_id),
            )

    def create_audit_run(self, email_id: str, metadata: dict[str, Any]) -> str:
        with self._connect() as conn:
            return conn.execute(
                "insert into audit_runs (email_id, status, metadata) values (%s, 'started', %s) returning run_id::text",
                (email_id, _json_dumps(metadata)),
            ).fetchone()["run_id"]

    def add_audit_step(
        self,
        run_id: str,
        step_type: str,
        input_summary: dict[str, Any],
        output_summary: dict[str, Any],
        reason: str | None = None,
        confidence: float | None = None,
        decision: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        sql = """
            insert into audit_steps (
              run_id, sequence_number, step_type, input_summary, output_summary,
              decision, reason, confidence, error
            )
            values (
              %s,
              coalesce((select max(sequence_number) + 1 from audit_steps where run_id = %s), 1),
              %s, %s, %s, %s, %s, %s, %s
            )
        """
        with self._connect() as conn:
            conn.execute(
                sql,
                (
                    run_id,
                    run_id,
                    step_type,
                    _json_dumps(input_summary),
                    _json_dumps(output_summary),
                    _json_dumps(decision) if decision is not None else None,
                    reason,
                    confidence,
                    error,
                ),
            )

    def save_extraction(
        self,
        email_id: str,
        extraction: ExtractionPayload | None,
        parsed_payload: dict[str, Any],
        validation_errors: list[str],
        raw_output: dict[str, Any] | None = None,
        document_item_id: str | None = None,
    ) -> dict[str, Any]:
        if extraction is None:
            extractor_raw = parsed_payload.get("extractor", {}) if isinstance(parsed_payload.get("extractor"), dict) else {}
            extractor_type = str(extractor_raw.get("type") or (raw_output or {}).get("extractor_type") or "unknown")
            confidence = None
            validation_status = "invalid"
            parsed_output = parsed_payload
            model_name = (raw_output or {}).get("model") if isinstance((raw_output or {}).get("model"), str) else None
            prompt_version = (
                extractor_raw.get("prompt_version")
                if isinstance(extractor_raw.get("prompt_version"), str)
                else (raw_output or {}).get("prompt_version") if isinstance((raw_output or {}).get("prompt_version"), str) else None
            )
        else:
            extractor_type = extraction.extractor.type
            confidence = extraction.confidence.overall
            validation_status = "valid"
            parsed_output = extraction.raw
            model_name = extraction.extractor.model
            prompt_version = extraction.extractor.prompt_version

        insert_sql = """
                insert into extractions (
                  email_id, document_item_id, extractor_type, model_name, prompt_version, raw_output,
                  parsed_output, confidence, validation_status, validation_errors
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """
        raw_extractor_output = raw_output if raw_output is not None else parsed_payload

        with self._connect() as conn:
            conn.execute(
                insert_sql,
                (
                    email_id,
                    document_item_id,
                    extractor_type,
                    model_name,
                    prompt_version,
                    _json_dumps(raw_extractor_output),
                    _json_dumps(parsed_output),
                    confidence,
                    validation_status,
                    _json_dumps(validation_errors),
                ),
            )
        return raw_extractor_output

    def save_document_item(
        self,
        email_id: str,
        item_kind: str,
        item_key: str,
        display_name: str | None,
        metadata: dict[str, Any],
        attachment_id: str | None = None,
    ) -> str:
        with self._connect() as conn:
            row = conn.execute(
                """
                insert into document_items (
                  email_id, item_kind, attachment_id, item_key, display_name, metadata
                )
                values (%s, %s, %s, %s, %s, %s)
                on conflict (email_id, item_key) do update
                set item_kind = excluded.item_kind,
                    attachment_id = excluded.attachment_id,
                    display_name = excluded.display_name,
                    metadata = excluded.metadata
                returning document_item_id::text
                """,
                (email_id, item_kind, attachment_id, item_key, display_name, _json_dumps(metadata)),
            ).fetchone()
        return row["document_item_id"]

    def save_llm_interaction(self, email_id: str, run_id: str, interaction: dict[str, Any]) -> None:
        sql = """
            insert into llm_interactions (
              email_id, run_id, step_id, extraction_id, interaction_type, provider,
              model_name, deployment_name, api_version, prompt_template_name,
              prompt_version, prompt_artifact_path, response_artifact_path,
              request_parameters, prompt_tokens, completion_tokens, total_tokens,
              cached_prompt_tokens, reasoning_tokens, raw_usage, latency_ms,
              status, error
            )
            values (
              %s, %s,
              (
                select step_id from audit_steps
                where run_id = %s and step_type = 'LLM_EXTRACTION'
                order by sequence_number desc
                limit 1
              ),
              (
                select extraction_id from extractions
                where email_id = %s
                order by created_at desc
                limit 1
              ),
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
        """
        raw_usage = interaction.get("raw_usage") if isinstance(interaction.get("raw_usage"), dict) else {}
        request_parameters = (
            interaction.get("request_parameters")
            if isinstance(interaction.get("request_parameters"), dict)
            else {}
        )
        with self._connect() as conn:
            conn.execute(
                sql,
                (
                    email_id,
                    run_id,
                    run_id,
                    email_id,
                    interaction["interaction_type"],
                    interaction["provider"],
                    interaction.get("model_name"),
                    interaction.get("deployment_name"),
                    interaction.get("api_version"),
                    interaction.get("prompt_template_name"),
                    interaction.get("prompt_version"),
                    interaction.get("prompt_artifact_path"),
                    interaction.get("response_artifact_path"),
                    _json_dumps(request_parameters),
                    interaction.get("prompt_tokens"),
                    interaction.get("completion_tokens"),
                    interaction.get("total_tokens"),
                    interaction.get("cached_prompt_tokens"),
                    interaction.get("reasoning_tokens"),
                    _json_dumps(raw_usage),
                    interaction.get("latency_ms"),
                    interaction["status"],
                    interaction.get("error"),
                ),
            )

    def save_attachments(self, email_id: str, attachments: list[dict[str, Any]]) -> None:
        sql = """
            insert into attachments (
              email_id, file_name, content_type, storage_path,
              file_size_bytes, sha256, metadata
            )
            values (%s, %s, %s, %s, %s, %s, %s)
            on conflict (email_id, storage_path, sha256) do update
            set file_name = excluded.file_name,
                content_type = excluded.content_type,
                file_size_bytes = excluded.file_size_bytes,
                metadata = excluded.metadata
        """
        with self._connect() as conn:
            for attachment in attachments:
                conn.execute(
                    sql,
                    (
                        email_id,
                        attachment["file_name"],
                        attachment.get("content_type"),
                        attachment["storage_path"],
                        attachment.get("file_size_bytes"),
                        attachment.get("sha256"),
                        _json_dumps(attachment.get("metadata", {})),
                    ),
                )

    def update_email_html_storage_path(self, email_id: str, html_storage_path: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "update emails set html_storage_path = %s where email_id = %s",
                (html_storage_path, email_id),
            )

    def save_decision(self, email_id: str, run_id: str, decision: Decision, document_item_id: str | None = None) -> str:
        outcome = _normalize_decision_outcome(decision.outcome)
        with self._connect() as conn:
            return conn.execute(
                """
                insert into decisions (
                  email_id, run_id, outcome, destination_code, destination_email,
                  document_item_id,
                  reason, confidence, matched_rule_code, matched_rule_version,
                  extracted_fields, routing_match
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                returning decision_id::text
                """,
                (
                    email_id,
                    run_id,
                    outcome,
                    decision.destination_code,
                    decision.destination_email,
                    document_item_id,
                    decision.reason,
                    decision.confidence,
                    decision.matched_rule_code,
                    decision.matched_rule_version,
                    _json_dumps(decision.extracted_fields),
                    _json_dumps(decision.routing_match),
                ),
            ).fetchone()["decision_id"]

    def save_action(self, email_id: str, decision_id: str, decision: Decision, manifest_path: str, document_item_id: str | None = None) -> None:
        outcome = _normalize_decision_outcome(decision.outcome)
        action_type = {
            "AUTO": "forward_email",
            "ESCALATE": "queue_escalate",
            "FLAG": "queue_escalate",
            "FILE": "file_email",
            "DISCARD": "no_action",
        }[outcome]
        with self._connect() as conn:
            conn.execute(
                """
                insert into actions (
                  email_id, document_item_id, decision_id, action_type, destination_code,
                  status, external_reference, reason, completed_at
                )
                values (%s, %s, %s, %s, %s, 'recorded', %s, %s, now())
                """,
                (
                    email_id,
                    document_item_id,
                    decision_id,
                    action_type,
                    decision.destination_code,
                    manifest_path,
                    decision.reason,
                ),
            )

    def enqueue_escalate(self, email_id: str, decision_id: str, reason: str, priority: str = "normal", document_item_id: str | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "insert into escalate_queue (email_id, document_item_id, decision_id, priority, reason) values (%s, %s, %s, %s, %s)",
                (email_id, document_item_id, decision_id, priority, reason),
            )

    def reload_escalate_folder_items(self, items: list[dict[str, Any]]) -> None:
        insert_sql = """
            insert into escalate_queue (
              email_id, source_message_id, status, priority, reason, office_web_link,
              last_seen_in_escalate_at, active
            )
            values (%s, %s, 'open', 'normal', %s, %s, now(), true)
        """
        with self._connect() as conn:
            conn.execute("delete from escalate_queue")
            for item in items:
                email_id = self._upsert_email(conn, item["email_metadata"])
                conn.execute(
                    insert_sql,
                    (
                        email_id,
                        item["source_message_id"],
                        item["reason"],
                        item.get("office_web_link"),
                    ),
                )

    def finalize_audit_run(self, run_id: str, final_outcome: str, trace_artifact_path: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                update audit_runs
                set status = 'completed',
                    completed_at = now(),
                    final_outcome = %s,
                    trace_artifact_path = %s
                where run_id = %s
                """,
                (final_outcome, trace_artifact_path, run_id),
            )

    def fail_audit_run(self, run_id: str, error: str, trace_artifact_path: str | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                update audit_runs
                set status = 'failed',
                    completed_at = now(),
                    trace_artifact_path = coalesce(%s, trace_artifact_path),
                    metadata = metadata || jsonb_build_object('error', %s::text)
                where run_id = %s
                """,
                (trace_artifact_path, error, run_id),
            )

    def _connect(self):
        if os.getenv("APP_ENV", "LOCAL").strip().upper() == "AZURE" and "password=" not in self._dsn.lower():
            try:
                from azure.identity import DefaultAzureCredential
            except Exception as exc:
                raise RuntimeError("azure-identity is required for Azure Postgres Microsoft Entra authentication.") from exc
            token = DefaultAzureCredential().get_token("https://ossrdbms-aad.database.windows.net/.default").token
            return self._psycopg.connect(self._dsn, password=token, row_factory=self._dict_row)
        return self._psycopg.connect(self._dsn, row_factory=self._dict_row)


def _compact_sql(sql: str) -> str:
    return " ".join(sql.split())


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, default=_json_default)


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    raise TypeError(f"Object of type {value.__class__.__name__} is not JSON serializable")


def new_local_id(prefix: str) -> str:
    return f"{prefix}_{uuid4()}"


def _normalize(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.strip().lower().split())
    return normalized or None


_ADDRESS_TOKEN_NORMALIZATION = {
    "st": "street",
    "street": "street",
    "rd": "road",
    "road": "road",
    "dr": "drive",
    "drive": "drive",
    "pkwy": "parkway",
    "pwky": "parkway",
    "parkway": "parkway",
    "fwy": "freeway",
    "freeway": "freeway",
    "blvd": "boulevard",
    "boulevard": "boulevard",
    "ln": "lane",
    "lane": "lane",
    "ct": "court",
    "court": "court",
    "ave": "avenue",
    "avenue": "avenue",
    "ft": "fort",
    "n": "north",
    "s": "south",
    "e": "east",
    "w": "west",
}

_SUITE_PREFIXES = {"ste", "suite", "unit"}


def _normalize_property_value(value: str | None) -> str:
    if not value:
        return ""
    cleaned = re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
    if not cleaned:
        return ""
    tokens = [_ADDRESS_TOKEN_NORMALIZATION.get(token, token) for token in cleaned.split()]
    normalized = " ".join(tokens)
    compact = "".join(tokens)
    if re.fullmatch(r"[a-z]+\d+[a-z0-9]*", compact):
        return compact
    return normalized


def _normalize_suite_value(value: str | None) -> str:
    if not value:
        return ""
    cleaned = re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
    if not cleaned:
        return ""
    normalized = " ".join(_ADDRESS_TOKEN_NORMALIZATION.get(token, token) for token in cleaned.split())
    tokens = [token for token in normalized.split() if token not in _SUITE_PREFIXES]
    return " ".join(tokens).strip()


def _normalize_state_value(value: str | None) -> str:
    normalized = _normalize_property_value(value)
    return normalized if re.fullmatch(r"[a-z]{2}", normalized) else ""


def _normalize_zipcode_value(value: str | None) -> str:
    if not value:
        return ""
    match = re.search(r"\d{5}", value)
    return match.group(0) if match else ""


def _has_lookup_value(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Iterable):
        return any(_has_lookup_value(item) for item in value)
    return value is not None


def _unique_values(values: Iterable[str | None]) -> list[str]:
    items: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = " ".join(value.strip().split()) if value else ""
        if item and item not in seen:
            items.append(item)
            seen.add(item)
    return items


def _lookup_values(values: dict[str, Any], key: str) -> list[str | None]:
    value = values.get(key)
    if isinstance(value, str) or value is None:
        return [value]
    if isinstance(value, Iterable):
        return [item for item in value if isinstance(item, str) or item is None]
    return []


def _property_query_signals(extraction: ExtractionPayload) -> tuple[dict[str, Any], dict[str, Any]]:
    address_candidates = [
        {
            "rank": candidate.rank,
            "label": candidate.label,
            "street": candidate.street,
            "city": candidate.city,
            "state": candidate.state,
            "zipcode": candidate.zipcode,
            "normalized_address": candidate.normalized_address,
            "source": candidate.source,
            "confidence": candidate.confidence,
            "evidence_text": candidate.evidence_text,
        }
        for candidate in extraction.property_lookup.address_candidates
    ]
    query_values = {
        "property_codes": _unique_values(extraction.property_lookup.property_code),
        "property_names": _unique_values(extraction.property_lookup.property_name),
        "tenants": _unique_values(extraction.property_lookup.tenant),
        "addresses": _unique_values(extraction.property_lookup.address),
        "suites": _unique_values(extraction.property_lookup.suite),
        "cities": _unique_values(extraction.property_lookup.city),
        "states": _unique_values(extraction.property_lookup.state),
        "zipcodes": _unique_values(extraction.property_lookup.zipcode),
        "address_candidates": address_candidates,
    }
    return (
        {
            "property_lookup": {
                "property_code": list(extraction.property_lookup.property_code),
                "property_name": list(extraction.property_lookup.property_name),
                "tenant": list(extraction.property_lookup.tenant),
                "address": list(extraction.property_lookup.address),
                "suite": list(extraction.property_lookup.suite),
                "city": list(extraction.property_lookup.city),
                "state": list(extraction.property_lookup.state),
                "zipcode": list(extraction.property_lookup.zipcode),
                "address_candidates": address_candidates,
            },
            "property_code": extraction.invoice.property_code,
            "property_name": extraction.invoice.property_name,
            "service_address": extraction.invoice.service_address,
            "bill_to": extraction.invoice.bill_to,
            "bill_to_components": {
                "name_line_1": extraction.invoice.bill_to_name_line_1,
                "name_line_2": extraction.invoice.bill_to_name_line_2,
                "street_address": extraction.invoice.bill_to_street_address,
                "suite": extraction.invoice.bill_to_suite,
                "city": extraction.invoice.bill_to_city,
                "state": extraction.invoice.bill_to_state,
                "zip_code": extraction.invoice.bill_to_zip_code,
            },
            "possible_property_aliases": list(extraction.business_signals.possible_property_aliases),
        },
        query_values,
    )


def _normalize_decision_outcome(outcome: str) -> str:
    return outcome
