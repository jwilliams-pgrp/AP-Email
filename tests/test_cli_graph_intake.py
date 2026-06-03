from __future__ import annotations

import unittest
from unittest.mock import patch

from ap_automation import cli


class CliGraphIntakeTests(unittest.TestCase):
    def test_source_intake_failure_after_claim_moves_claimed_message_to_escalate(self) -> None:
        graph_mailbox = _FakeGraphMailbox()

        with (
            patch("sys.argv", ["ap-automation", "--source-intake"]),
            patch("ap_automation.cli.PostgresRepository", return_value=_FakeRepository()),
            patch("ap_automation.cli.AzureOpenAIExtractor", return_value=_FakeExtractor()),
            patch("ap_automation.cli.GraphMailboxClient.from_env", return_value=graph_mailbox),
            patch("ap_automation.cli.LocalProcessor", return_value=_FailingProcessor()),
            patch("ap_automation.cli.load_dotenv"),
        ):
            with self.assertRaisesRegex(RuntimeError, "processor failed"):
                cli.main()

        self.assertEqual(graph_mailbox.escalated_message_ids, ["claimed-processing-msg-1"])


class _FakeEnvelope:
    message_id = "claimed-processing-msg-1"
    categories = ()
    internet_message_id = "<internet-id-1>"
    web_link = "https://outlook.office.com/mail/processing/id/claimed-processing-msg-1"
    parsed_msg = None


class _FakeGraphMailbox:
    def __init__(self) -> None:
        self.escalated_message_ids: list[str] = []

    def claim_oldest_from_intake(self):
        return _FakeEnvelope()

    def move_message_to_escalate(self, message_id: str):
        self.escalated_message_ids.append(message_id)
        return {"message_id": f"escalated-{message_id}"}


class _FailingProcessor:
    def process_graph_email(self, envelope, fixture):
        raise RuntimeError("processor failed")


class _FakeRepository:
    pass


class _FakeExtractor:
    pass


if __name__ == "__main__":
    unittest.main()
