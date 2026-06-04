from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _seed_rule_priorities() -> dict[str, int]:
    seed_sql = (ROOT / "db" / "seed.sql").read_text(encoding="utf-8")
    return {
        match.group("code"): int(match.group("priority"))
        for match in re.finditer(
            r"\('(?P<code>[^']+)',\s*'[^']*',\s*(?P<priority>\d+),\s*(?:true|false),",
            seed_sql,
        )
    }


class SeedPolicyTests(unittest.TestCase):
    def test_ben_e_keith_files_before_attachment_and_pdf_exceptions(self) -> None:
        priorities = _seed_rule_priorities()

        self.assertLess(priorities["ben_e_keith_notice_file"], priorities["hard_wrong_file_type"])
        self.assertLess(priorities["ben_e_keith_notice_file"], priorities["hard_pdf_required_unreadable"])
        self.assertLess(priorities["ben_e_keith_notice_file"], priorities["hard_pdf_text_low_quality"])

    def test_ben_e_keith_priority_is_in_replayable_baseline(self) -> None:
        seed_sql = (ROOT / "db" / "seed.sql").read_text(encoding="utf-8")

        self.assertIn("'ben_e_keith_notice_file'", seed_sql)
        self.assertRegex(seed_sql, r"\('ben_e_keith_notice_file',\s*'[^']+',\s*113,")

    def test_one_time_db_scripts_are_not_part_of_current_baseline(self) -> None:
        allowed_targeted_scripts = {
            "add-appointment-informational-no-action.sql",
            "add-asset-custom-lookup.sql",
            "update-current-reply-no-action.sql",
        }
        one_time_scripts = [
            path.name
            for path in (ROOT / "db").glob("*.sql")
            if re.match(r"^(add|update)-.*\.sql$", path.name) and path.name not in allowed_targeted_scripts
        ]

        self.assertEqual([], one_time_scripts)

    def test_azure_postgres_deploy_applies_required_sql_files(self) -> None:
        deploy_script = (ROOT / "deploy-azure-postgres-nonprod.ps1").read_text(encoding="utf-8")

        self.assertIn("db\\schema.sql", deploy_script)
        self.assertIn("db\\seed.sql", deploy_script)
        self.assertIn("db\\azure-permissions.sql", deploy_script)

    def test_asset_custom_lookup_baseline_is_replayable(self) -> None:
        schema_sql = (ROOT / "db" / "schema.sql").read_text(encoding="utf-8")
        seed_sql = (ROOT / "db" / "seed.sql").read_text(encoding="utf-8")
        targeted_sql = (ROOT / "db" / "add-asset-custom-lookup.sql").read_text(encoding="utf-8")

        self.assertIn("CREATE TABLE public.asset_custom", schema_sql)
        self.assertIn("CREATE OR REPLACE VIEW public.vw_asset_lookup", schema_sql)
        self.assertIn("id bigint NOT NULL", schema_sql)
        self.assertIn("destination_code character varying(100)", schema_sql)
        self.assertIn("'ESCALATE_SPECIAL_ADDRESS'", seed_sql)
        self.assertIn("drop table if exists asset_custom", targeted_sql.lower())
        self.assertIn("create table if not exists asset_custom", targeted_sql.lower())
        self.assertIn("create or replace view vw_asset_lookup", targeted_sql.lower())
