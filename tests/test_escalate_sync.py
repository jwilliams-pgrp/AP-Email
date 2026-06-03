from __future__ import annotations

import unittest

from ap_automation.services.escalate_sync import EscalateMailboxSync
from ap_automation.services.graph_mailbox import GraphMessageEnvelope
from ap_automation.services.msg_parser import ParsedMsg


class EscalateMailboxSyncTests(unittest.TestCase):
    def test_sync_imports_unknown_graph_message_and_removes_stale_items(self) -> None:
        graph = _FakeGraphMailbox(
            [
                GraphMessageEnvelope(
                    message_id="msg-1",
                    internet_message_id="internet-1",
                    web_link="https://outlook.office.com/mail/ESCALATE/id/msg-1",
                    parsed_msg=ParsedMsg(
                        subject="Needs escalation",
                        sender_email="sender@example.com",
                        sender_name="Sender",
                        received_at=None,
                        body_text="Please inspect",
                        transport_headers=None,
                        attachments=(),
                        metadata={},
                    ),
                    categories=("General",),
                )
            ]
        )
        repository = _FakeOperationalRepository()
        repository.queue_by_source_message_id["old-msg"] = {"active": True, "status": "open"}

        count = EscalateMailboxSync(graph, repository).sync()

        self.assertEqual(count, 1)
        self.assertEqual(repository.emails["graph_mailbox:msg-1"]["subject"], "Needs escalation")
        self.assertEqual(repository.queue_by_source_message_id["msg-1"]["office_web_link"], "https://outlook.office.com/mail/ESCALATE/id/msg-1")
        self.assertNotIn("old-msg", repository.queue_by_source_message_id)

    def test_sync_with_empty_graph_folder_leaves_queue_empty(self) -> None:
        graph = _FakeGraphMailbox([])
        repository = _FakeOperationalRepository()
        repository.queue_by_source_message_id["old-msg"] = {"active": True, "status": "open"}

        count = EscalateMailboxSync(graph, repository).sync()

        self.assertEqual(count, 0)
        self.assertEqual(repository.queue_by_source_message_id, {})


class _FakeGraphMailbox:
    def __init__(self, envelopes: list[GraphMessageEnvelope]) -> None:
        self._envelopes = envelopes

    def list_escalate_messages(self) -> list[GraphMessageEnvelope]:
        return self._envelopes


class _FakeOperationalRepository:
    def __init__(self) -> None:
        self.emails: dict[str, dict[str, object]] = {}
        self.queue_by_source_message_id: dict[str, dict[str, object]] = {}

    def reload_escalate_folder_items(self, items: list[dict[str, object]]) -> None:
        self.queue_by_source_message_id.clear()
        for item in items:
            metadata = item["email_metadata"]
            assert isinstance(metadata, dict)
            email_id = self.upsert_email(metadata)
            source_message_id = str(item["source_message_id"])
            self.queue_by_source_message_id[source_message_id] = {
                "email_id": email_id,
                "reason": item["reason"],
                "office_web_link": item.get("office_web_link"),
                "active": True,
                "status": "open",
            }

    def upsert_email(self, metadata: dict[str, object]) -> str:
        key = str(metadata["idempotency_key"])
        self.emails[key] = metadata
        return key


if __name__ == "__main__":
    unittest.main()
