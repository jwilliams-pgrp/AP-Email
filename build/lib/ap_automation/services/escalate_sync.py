from __future__ import annotations

from ap_automation.repositories.protocols import OperationalRepository
from ap_automation.services.graph_mailbox import GraphMailboxClient, GraphMessageEnvelope


class EscalateMailboxSync:
    def __init__(self, graph_mailbox: GraphMailboxClient, operational_repository: OperationalRepository) -> None:
        self._graph_mailbox = graph_mailbox
        self._operational_repository = operational_repository

    def sync(self) -> int:
        envelopes = self._graph_mailbox.list_escalate_messages()
        self._operational_repository.reload_escalate_folder_items(
            [
                {
                    "email_metadata": _minimal_email_metadata(envelope),
                    "source_message_id": envelope.message_id,
                    "reason": "Message is currently in the ESCALATE folder.",
                    "office_web_link": envelope.web_link,
                }
                for envelope in envelopes
            ]
        )
        return len(envelopes)


def _minimal_email_metadata(envelope: GraphMessageEnvelope) -> dict[str, object]:
    parsed = envelope.parsed_msg
    metadata = {
        "parser": "graph_api",
        "graph_message_id": envelope.message_id,
        "internet_message_id": envelope.internet_message_id,
        "categories": list(envelope.categories),
        "office_web_link": envelope.web_link,
        "escalate_folder_sync": True,
    }
    if parsed.sender_name:
        metadata["sender_name"] = parsed.sender_name
    return {
        "source_system": "graph_mailbox",
        "source_message_id": envelope.message_id,
        "idempotency_key": f"graph_mailbox:{envelope.message_id}",
        "subject": parsed.subject,
        "sender_email": parsed.sender_email,
        "received_at": parsed.received_at.isoformat() if parsed.received_at else None,
        "raw_storage_path": None,
        "office_web_link": envelope.web_link,
        "metadata": metadata,
    }
