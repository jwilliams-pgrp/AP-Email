from __future__ import annotations

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv

from ap_automation.config import LOCAL_DATABASE_URL
from ap_automation.repositories.postgres import PostgresRepository
from ap_automation.services.azure_openai_extractor import AzureOpenAIExtractor
from ap_automation.services.graph_mailbox import GraphMailboxClient
from ap_automation.services.escalate_sync import EscalateMailboxSync
from ap_automation.services.local_processor import LocalProcessor
from ap_automation.services.teams_notifier import TeamsNotifier


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Run the AP Automation processor.")
    parser.add_argument(
        "--source-intake",
        action="store_true",
        help="Read one email from Graph intake folder configured in env and process it.",
    )
    parser.add_argument(
        "--source-email",
        help="Path to one saved source email. Intended only for fixtures/tests.",
    )
    parser.add_argument(
        "--extraction-fixture",
        help="Optional path to an extraction.v1 fixture JSON file.",
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
        "--azure-openai-endpoint",
        default=os.environ.get("AZURE_OPENAI_ENDPOINT"),
        help="Azure OpenAI endpoint. Defaults to AZURE_OPENAI_ENDPOINT.",
    )
    parser.add_argument(
        "--azure-openai-api-key",
        default=os.environ.get("AZURE_OPENAI_API_KEY"),
        help="Azure OpenAI API key. Defaults to AZURE_OPENAI_API_KEY.",
    )
    parser.add_argument(
        "--azure-openai-api-version",
        default=os.environ.get("AZURE_OPENAI_API_VERSION"),
        help="Azure OpenAI API version. Defaults to AZURE_OPENAI_API_VERSION.",
    )
    parser.add_argument(
        "--azure-openai-deployment",
        default=os.environ.get("AZURE_OPENAI_DEPLOYMENT"),
        help="Azure OpenAI deployment name. Defaults to AZURE_OPENAI_DEPLOYMENT.",
    )
    parser.add_argument(
        "--azure-openai-timeout-seconds",
        type=int,
        default=int(os.environ.get("AZURE_OPENAI_TIMEOUT_SECONDS", "120")),
        help="Timeout for Azure OpenAI extraction.",
    )
    args = parser.parse_args()
    if not args.source_intake and not args.source_email:
        parser.error("choose one source: --source-intake or --source-email")
    if args.source_intake and args.source_email:
        parser.error("--source-intake and --source-email are mutually exclusive")

    database_url = args.database_url
    if not database_url and args.app_env == "LOCAL":
        database_url = LOCAL_DATABASE_URL
    if not database_url:
        parser.error("--database-url or DATABASE_URL is required outside LOCAL mode")

    project_root = Path.cwd()
    repository = PostgresRepository(database_url)
    llm_extractor = AzureOpenAIExtractor(
        project_root=project_root,
        endpoint=args.azure_openai_endpoint,
        api_key=args.azure_openai_api_key,
        api_version=args.azure_openai_api_version,
        deployment=args.azure_openai_deployment,
        timeout_seconds=args.azure_openai_timeout_seconds,
    )
    graph_mailbox = GraphMailboxClient.from_env() if args.source_intake else None
    teams_notifier = TeamsNotifier.from_env() if os.environ.get("TEAMS-WEBHOOK-URL-PROPERTIES-AP") else None
    processor = LocalProcessor(project_root, repository, repository, llm_extractor, graph_mailbox, teams_notifier)

    fixture = Path(args.extraction_fixture) if args.extraction_fixture else None
    if args.source_intake:
        envelope = graph_mailbox.claim_oldest_from_intake() if graph_mailbox else None
        if envelope is None:
            print("no email found in Graph intake folder")
            return 0
        try:
            run_id = processor.process_graph_email(envelope, fixture)
        except Exception as exc:
            recovery_error: Exception | None = None
            try:
                if graph_mailbox is not None:
                    graph_mailbox.move_message_to_escalate(envelope.message_id)
            except Exception as move_exc:
                recovery_error = move_exc
            if recovery_error is not None:
                print(
                    "Graph intake processing failed after claim, and moving the claimed message to ESCALATE also failed: "
                    f"{recovery_error}"
                )
            raise
        print(f"completed audit run from Graph intake: {run_id}")
        synced = EscalateMailboxSync(graph_mailbox, repository).sync()
        print(f"synced ESCALATE folder messages: {synced}")
        return 0

    run_id = _process_one(processor, Path(args.source_email), fixture)
    print(f"completed audit run: {run_id}")
    return 0


def _process_one(processor: LocalProcessor, source_email: Path, extraction_fixture: Path | None) -> str:
    if extraction_fixture:
        return processor.process_fixture(source_email, extraction_fixture)
    return processor.process_email(source_email)


if __name__ == "__main__":
    raise SystemExit(main())
