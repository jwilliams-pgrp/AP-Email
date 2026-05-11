from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

from ap_automation.repositories.postgres import PostgresRepository
from ap_automation.services.codex_extractor import CodexCliExtractor
from ap_automation.services.local_processor import LocalProcessor


LOCAL_DATABASE_URL = "postgresql://postgres@localhost:5432/apautomation"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the AP Automation local dry-run processor.")
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--source-email", help="Path to one saved local source email.")
    source_group.add_argument("--source-folder", help="Path to a folder of saved local source emails.")
    parser.add_argument(
        "--source-pattern",
        default="*.msg",
        help="Glob used with --source-folder. Defaults to *.msg.",
    )
    parser.add_argument(
        "--extraction-fixture",
        help="Optional path to an extraction.v1 fixture JSON file. If omitted for .msg files, local extraction is used.",
    )
    parser.add_argument(
        "--app-env",
        default=os.environ.get("APP_ENV", "LOCAL"),
        choices=("LOCAL", "PRODUCTION"),
        help="Runtime environment. Defaults to APP_ENV or LOCAL.",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="Postgres DSN. Defaults to DATABASE_URL, or local Postgres in LOCAL mode.",
    )
    parser.add_argument(
        "--codex-command",
        default=_default_codex_command(),
        help="Codex CLI executable or command prefix for local LLM extraction. Defaults to CODEX_COMMAND or codex.",
    )
    parser.add_argument(
        "--codex-skip-git-repo-check",
        action="store_true",
        default=os.environ.get("CODEX_SKIP_GIT_REPO_CHECK", "").lower() in {"1", "true", "yes"},
        help="Pass --skip-git-repo-check to codex exec. Useful for local workspaces that are not Git repos.",
    )
    parser.add_argument(
        "--codex-model",
        default=os.environ.get("CODEX_MODEL"),
        help="Optional model passed to codex exec --model for local LLM extraction.",
    )
    parser.add_argument(
        "--codex-timeout-seconds",
        type=int,
        default=int(os.environ.get("CODEX_TIMEOUT_SECONDS", "300")),
        help="Timeout for codex exec local extraction.",
    )
    args = parser.parse_args()

    database_url = args.database_url
    if not database_url and args.app_env == "LOCAL":
        database_url = LOCAL_DATABASE_URL
    if not database_url:
        parser.error("--database-url or DATABASE_URL is required outside LOCAL mode")

    project_root = Path.cwd()
    repository = PostgresRepository(database_url)
    codex_extractor = CodexCliExtractor(
        project_root=project_root,
        command=args.codex_command,
        model=args.codex_model,
        timeout_seconds=args.codex_timeout_seconds,
        skip_git_repo_check=args.codex_skip_git_repo_check,
    )
    processor = LocalProcessor(project_root, repository, repository, codex_extractor)
    if args.source_email:
        run_id = _process_one(processor, Path(args.source_email), Path(args.extraction_fixture) if args.extraction_fixture else None)
        print(f"completed local dry-run audit run: {run_id}")
        return 0

    if args.extraction_fixture:
        parser.error("--extraction-fixture can only be used with --source-email")

    source_folder = Path(args.source_folder)
    source_emails = sorted(path for path in source_folder.glob(args.source_pattern) if path.is_file())
    if not source_emails:
        parser.error(f"--source-folder matched no files: {source_folder / args.source_pattern}")

    failures = 0
    for source_email in source_emails:
        print(f"processing local source email: {source_email}")
        try:
            run_id = processor.process_email(source_email)
            print(f"completed local dry-run audit run: {run_id}")
        except Exception as exc:
            failures += 1
            print(f"failed local dry-run for {source_email}: {exc}")

    print(f"processed {len(source_emails)} local source emails; failures={failures}")
    return 1 if failures else 0


def _process_one(processor: LocalProcessor, source_email: Path, extraction_fixture: Path | None) -> str:
    if extraction_fixture:
        return processor.process_fixture(source_email, extraction_fixture)
    return processor.process_email(source_email)


def _default_codex_command() -> str:
    configured = os.environ.get("CODEX_COMMAND")
    if configured:
        return configured
    if os.name == "nt":
        codex_cmd = shutil.which("codex.cmd")
        if codex_cmd:
            return codex_cmd
    return "codex"


if __name__ == "__main__":
    raise SystemExit(main())
