from __future__ import annotations

import unittest
from unittest.mock import patch

import requests

from ap_automation.services.graph_mailbox import GraphMailboxClient
from ap_automation.services.graph_mailbox import GraphMailboxError


class GraphMailboxResolverTests(unittest.TestCase):
    def test_from_env_uses_new_mail_variable_names_and_inbox_intake(self) -> None:
        env = {
            "AZURE-CLIENT-ID-MAIL": "client-id",
            "AZURE-TENANT-ID": "tenant-id",
            "AZURE-CLIENT-SECRET-MAIL": "client-secret",
            "USER_PRINCIPAL_NAME_MAIL": "ap@example.com",
            "GRAPH_TIMEOUT_SECONDS": "12",
        }

        with patch.dict("os.environ", env, clear=True):
            client = GraphMailboxClient.from_env()

        self.assertEqual(client._client_id, "client-id")
        self.assertEqual(client._tenant_id, "tenant-id")
        self.assertEqual(client._client_secret, "client-secret")
        self.assertEqual(client._user_principal_name, "ap@example.com")
        self.assertEqual(client._intake_folder_id, "inbox")
        self.assertEqual(client._timeout_seconds, 12)

    def test_from_env_does_not_require_graph_intake_folder_id(self) -> None:
        env = {
            "AZURE-CLIENT-ID-MAIL": "client-id",
            "AZURE-TENANT-ID": "tenant-id",
            "AZURE-CLIENT-SECRET-MAIL": "client-secret",
            "USER_PRINCIPAL_NAME_MAIL": "ap@example.com",
        }

        with patch.dict("os.environ", env, clear=True):
            client = GraphMailboxClient.from_env()

        self.assertEqual(client._intake_folder_id, "inbox")

    def test_from_env_reports_missing_new_mail_variable_name(self) -> None:
        env = {
            "AZURE-TENANT-ID": "tenant-id",
        }

        with patch.dict("os.environ", env, clear=True):
            with self.assertRaises(GraphMailboxError) as context:
                GraphMailboxClient.from_env()

        self.assertIn("AZURE-CLIENT-ID-MAIL", str(context.exception))

    def test_client_secret_mode_requires_mailbox_upn(self) -> None:
        env = {
            "GRAPH_AUTH_MODE": "client_secret",
            "AZURE-CLIENT-ID-MAIL": "client-id",
            "AZURE-TENANT-ID": "tenant-id",
            "AZURE-CLIENT-SECRET-MAIL": "client-secret",
        }

        with patch.dict("os.environ", env, clear=True):
            with self.assertRaises(GraphMailboxError) as context:
                GraphMailboxClient.from_env()

        self.assertIn("USER_PRINCIPAL_NAME_MAIL", str(context.exception))

    def test_azure_mode_uses_client_secret_graph_settings(self) -> None:
        env = {
            "APP_ENV": "AZURE",
            "AZURE_CLIENT_ID_MAIL": "client-id",
            "AZURE_TENANT_ID": "tenant-id",
            "AZURE_CLIENT_SECRET_MAIL": "client-secret",
            "USER_PRINCIPAL_NAME_MAIL": "ap@example.com",
        }

        with patch.dict("os.environ", env, clear=True):
            client = GraphMailboxClient.from_env()

        self.assertEqual(client._client_id, "client-id")
        self.assertEqual(client._tenant_id, "tenant-id")
        self.assertEqual(client._client_secret, "client-secret")
        self.assertEqual(client._user_principal_name, "ap@example.com")

    def test_identity_mode_fails_explicitly(self) -> None:
        env = {
            "APP_ENV": "AZURE",
            "GRAPH_AUTH_MODE": "identity",
            "USER_PRINCIPAL_NAME_MAIL": "ap@example.com",
        }

        with patch.dict("os.environ", env, clear=True):
            with self.assertRaises(GraphMailboxError) as context:
                GraphMailboxClient.from_env()

        self.assertIn("Unsupported GRAPH_AUTH_MODE", str(context.exception))
        self.assertIn("client_secret", str(context.exception))

    def test_resolve_uses_folder_path_chain(self) -> None:
        client = _FakeGraphMailboxClient()
        client.folder_cache = {
            "id-escalate": {
                "id": "id-escalate",
                "displayName": "ESCALATE",
                "parentFolderId": "id-inbox",
                "childFolderCount": 1,
            },
            "id-dup": {
                "id": "id-dup",
                "displayName": "DUPLICATE-SUSPECTED",
                "parentFolderId": "id-escalate",
                "childFolderCount": 0,
            },
        }

        resolved = client._resolve_destination_folder_id(
            parent_folder_hint="ESCALATE",
            destination_display_name="DUPLICATE-SUSPECTED",
            destination_folder_path="local/outbound/ESCALATE/DUPLICATE-SUSPECTED",
        )

        self.assertEqual(resolved, "id-dup")

    def test_resolve_uses_unique_name_when_path_missing(self) -> None:
        client = _FakeGraphMailboxClient()
        client.folder_cache = {
            "id-ach": {
                "id": "id-ach",
                "displayName": "ACH",
                "parentFolderId": "id-inbox",
                "childFolderCount": 0,
            }
        }

        resolved = client._resolve_destination_folder_id(
            parent_folder_hint="FOLDER_ACH",
            destination_display_name="ACH",
            destination_folder_path=None,
        )

        self.assertEqual(resolved, "id-ach")

    def test_resolve_raises_when_name_is_ambiguous(self) -> None:
        client = _FakeGraphMailboxClient()
        client.folder_cache = {
            "id-a": {
                "id": "id-a",
                "displayName": "Invoices",
                "parentFolderId": "id-1",
                "childFolderCount": 0,
            },
            "id-b": {
                "id": "id-b",
                "displayName": "Invoices",
                "parentFolderId": "id-2",
                "childFolderCount": 0,
            },
        }

        with self.assertRaises(GraphMailboxError):
            client._resolve_destination_folder_id(
                parent_folder_hint="FOLDER_INVOICES",
                destination_display_name="Invoices",
                destination_folder_path=None,
            )

    def test_graph_attachment_metadata_preserves_inline_flags(self) -> None:
        client = _FakeGraphMailboxClient()
        client.attachments_payload = {
            "value": [
                {
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "id": "att-1",
                    "name": "logo.png",
                    "contentType": "image/png",
                    "contentId": "logo",
                    "isInline": True,
                    "contentBytes": "cG5n",
                }
            ]
        }

        attachments = client._message_attachments("msg-1")

        self.assertEqual(attachments[0].file_name, "logo.png")
        self.assertEqual(attachments[0].metadata["content_id"], "logo")
        self.assertTrue(attachments[0].metadata["is_inline"])

    def test_claim_oldest_moves_to_processing_then_uses_full_html_body_not_preview(self) -> None:
        client = _FakeGraphMailboxClient()
        client.folder_cache = {
            "id-processing": {
                "id": "id-processing",
                "displayName": "processing",
                "parentFolderId": "id-inbox",
                "childFolderCount": 0,
            }
        }
        client.messages_payload = {
            "value": [
                {
                    "id": "msg-1",
                    "internetMessageId": "internet-1",
                    "webLink": "https://outlook.office.com/mail/inbox/id/msg-1",
                    "subject": "Upcoming Service Appointment",
                    "bodyPreview": "Upcoming Service Appointment preview without address",
                    "body": {
                        "contentType": "html",
                        "content": "<html><body><p>Service on Wednesday at <b>16501 Victory Circle</b>.</p></body></html>",
                    },
                    "receivedDateTime": "2026-05-10T12:02:13Z",
                    "from": {"emailAddress": {"address": "info@forterrapestcontrol.com", "name": "Forterra"}},
                    "categories": ["Inbox"],
                }
            ]
        }
        client.move_payload = {
            "id": "claimed-msg-1",
            "webLink": "https://outlook.office.com/mail/processing/id/claimed-msg-1",
        }
        client.claimed_message_payload = {
            **client.messages_payload["value"][0],
            "id": "claimed-msg-1",
            "webLink": "https://outlook.office.com/mail/processing/id/claimed-msg-1",
        }

        envelope = client.claim_oldest_from_intake()

        self.assertIsNotNone(envelope)
        assert envelope is not None
        self.assertEqual(client.moves[0]["message_id"], "msg-1")
        self.assertEqual(client.moves[0]["payload"], {"destinationId": "id-processing"})
        self.assertEqual(client.attachments_message_id, "claimed-msg-1")
        self.assertEqual(envelope.message_id, "claimed-msg-1")
        self.assertIn("16501 Victory Circle", envelope.parsed_msg.body_text or "")
        self.assertNotIn("preview without address", envelope.parsed_msg.body_text or "")
        self.assertIn("<b>16501 Victory Circle</b>", envelope.parsed_msg.body_html or "")
        self.assertEqual(client.intake_query_params.get("$top"), "1")
        self.assertEqual(client.intake_query_params.get("$orderby"), "receivedDateTime asc")
        self.assertNotIn("body,", client.intake_query_params.get("$select", ""))
        self.assertIn("body", client.claimed_message_query_params.get("$select", ""))
        self.assertIn("bodyPreview", client.claimed_message_query_params.get("$select", ""))
        self.assertIn("webLink", client.claimed_message_query_params.get("$select", ""))
        self.assertEqual(envelope.web_link, "https://outlook.office.com/mail/processing/id/claimed-msg-1")
        self.assertEqual(envelope.parsed_msg.metadata["office_web_link"], "https://outlook.office.com/mail/processing/id/claimed-msg-1")

    def test_claim_oldest_preserves_html_anchor_href_in_body_text(self) -> None:
        client = _FakeGraphMailboxClient()
        client.folder_cache = {
            "id-processing": {
                "id": "id-processing",
                "displayName": "processing",
                "parentFolderId": "id-inbox",
                "childFolderCount": 0,
            }
        }
        client.messages_payload = {
            "value": [
                {
                    "id": "msg-1",
                    "internetMessageId": "internet-1",
                    "subject": "Your Electric Service Bill is Available",
                    "bodyPreview": "preview",
                    "body": {
                        "contentType": "html",
                        "content": (
                            "<html><body><p>Your bill is available.</p>"
                            '<p>Amount: $193.98</p><p>Due Date: Jun 8, 2026</p>'
                            '<p><a href="https://example.com/pay">Click here</a> to view or pay.</p>'
                            '<p><a href="mailto:support@example.com">Email us</a></p></body></html>'
                        ),
                    },
                    "receivedDateTime": "2026-05-10T12:02:13Z",
                    "from": {"emailAddress": {"address": "billing@example.com", "name": "Billing"}},
                    "categories": [],
                }
            ]
        }
        client.move_payload = {"id": "claimed-msg-1"}
        client.claimed_message_payload = {**client.messages_payload["value"][0], "id": "claimed-msg-1"}

        envelope = client.claim_oldest_from_intake()

        self.assertIsNotNone(envelope)
        assert envelope is not None
        self.assertIn("Click here https://example.com/pay", envelope.parsed_msg.body_text or "")
        self.assertNotIn("mailto:support@example.com", envelope.parsed_msg.body_text or "")

    def test_claim_oldest_falls_back_to_body_preview_when_full_body_missing(self) -> None:
        client = _FakeGraphMailboxClient()
        client.folder_cache = {
            "id-processing": {
                "id": "id-processing",
                "displayName": "processing",
                "parentFolderId": "id-inbox",
                "childFolderCount": 0,
            }
        }
        client.messages_payload = {
            "value": [
                {
                    "id": "msg-1",
                    "internetMessageId": "internet-1",
                    "subject": "Preview only",
                    "bodyPreview": "Preview fallback text",
                    "receivedDateTime": "2026-05-10T12:02:13Z",
                    "from": {"emailAddress": {"address": "sender@example.com", "name": "Sender"}},
                    "categories": [],
                }
            ]
        }
        client.move_payload = {"id": "claimed-msg-1"}
        client.claimed_message_payload = {**client.messages_payload["value"][0], "id": "claimed-msg-1"}

        envelope = client.claim_oldest_from_intake()

        self.assertIsNotNone(envelope)
        assert envelope is not None
        self.assertEqual(envelope.parsed_msg.body_text, "Preview fallback text")
        self.assertIsNone(envelope.parsed_msg.body_html)

    def test_claim_oldest_raises_when_processing_folder_missing(self) -> None:
        client = _FakeGraphMailboxClient()
        client.folder_cache = {}
        client.messages_payload = {
            "value": [
                {
                    "id": "msg-1",
                    "subject": "Needs claim",
                    "bodyPreview": "preview",
                    "receivedDateTime": "2026-05-10T12:02:13Z",
                    "from": {"emailAddress": {"address": "sender@example.com", "name": "Sender"}},
                    "categories": [],
                }
            ]
        }

        with self.assertRaises(GraphMailboxError):
            client.claim_oldest_from_intake()

    def test_claim_oldest_raises_when_processing_folder_ambiguous(self) -> None:
        client = _FakeGraphMailboxClient()
        client.folder_cache = {
            "id-processing-1": {"id": "id-processing-1", "displayName": "processing"},
            "id-processing-2": {"id": "id-processing-2", "displayName": "processing"},
        }
        client.messages_payload = {
            "value": [
                {
                    "id": "msg-1",
                    "subject": "Needs claim",
                    "bodyPreview": "preview",
                    "receivedDateTime": "2026-05-10T12:02:13Z",
                    "from": {"emailAddress": {"address": "sender@example.com", "name": "Sender"}},
                    "categories": [],
                }
            ]
        }

        with self.assertRaises(GraphMailboxError):
            client.claim_oldest_from_intake()

    def test_route_message_returns_post_move_web_link(self) -> None:
        client = _FakeGraphMailboxClient()
        client.folder_cache = {
            "id-destination": {
                "id": "id-destination",
                "displayName": "LINK-ONLY",
                "parentFolderId": "id-escalate",
                "childFolderCount": 0,
            }
        }
        client.move_payload = {
            "id": "moved-msg-1",
            "webLink": "https://outlook.office.com/mail/ESCALATE/id/moved-msg-1",
        }

        result = client.route_message(
            message_id="msg-1",
            existing_categories=(),
            parent_folder="LINK-ONLY",
            label=None,
            destination_display_name="LINK-ONLY",
            destination_folder_path=None,
        )

        self.assertEqual(result["message_id"], "moved-msg-1")
        self.assertEqual(result["office_web_link"], "https://outlook.office.com/mail/ESCALATE/id/moved-msg-1")

    def test_forward_message_posts_recipient_payload(self) -> None:
        client = _FakeGraphMailboxClient()

        result = client.forward_message("msg-1", " ap@example.com ")

        self.assertEqual(result, {"forwarded": True, "recipient_email": "ap@example.com"})
        self.assertEqual(client.forward_payloads[0]["payload"], {"toRecipients": [{"emailAddress": {"address": "ap@example.com"}}]})
        self.assertTrue(client.forward_payloads[0]["url"].endswith("/users/user@example.com/messages/msg-1/forward"))

    def test_list_escalate_messages_preserves_office_web_link(self) -> None:
        client = _FakeGraphMailboxClient()
        client.folder_cache = {
            "id-escalate": {
                "id": "id-escalate",
                "displayName": "ESCALATE",
                "parentFolderId": "id-inbox",
                "childFolderCount": 0,
            }
        }
        client.messages_payload = {
            "value": [
                {
                    "id": "msg-1",
                    "internetMessageId": "internet-1",
                    "webLink": "https://outlook.office.com/mail/ESCALATE/id/msg-1",
                    "subject": "Needs escalation",
                    "bodyPreview": "Please inspect",
                    "receivedDateTime": "2026-05-10T12:02:13Z",
                    "from": {"emailAddress": {"address": "sender@example.com", "name": "Sender"}},
                    "categories": ["General"],
                }
            ]
        }

        envelopes = client.list_escalate_messages()

        self.assertEqual(len(envelopes), 1)
        self.assertEqual(envelopes[0].web_link, "https://outlook.office.com/mail/ESCALATE/id/msg-1")
        self.assertEqual(envelopes[0].parsed_msg.metadata["office_web_link"], "https://outlook.office.com/mail/ESCALATE/id/msg-1")
        self.assertIn("bodyPreview", client.message_query_params.get("$select", ""))
        self.assertNotIn("body,", client.message_query_params.get("$select", ""))

    def test_invalid_json_response_raises_graph_mailbox_error(self) -> None:
        response = requests.Response()
        response.status_code = 200
        response.url = "https://graph.microsoft.com/v1.0/users/user@example.com/messages"
        response._content = b'{"value": ['

        with self.assertRaises(GraphMailboxError) as context:
            GraphMailboxClient._handle_json_response("GET", response.url, response)

        self.assertIn("returned invalid JSON", str(context.exception))
        self.assertIn("GET", str(context.exception))


class _FakeGraphMailboxClient(GraphMailboxClient):
    def __init__(self) -> None:
        super().__init__(
            client_id="cid",
            tenant_id="tid",
            client_secret="secret",
            user_principal_name="user@example.com",
            intake_folder_id="intake",
        )
        self.folder_cache: dict[str, dict] = {}
        self.attachments_payload: dict[str, list[dict]] = {"value": []}
        self.messages_payload: dict[str, list[dict]] = {"value": []}
        self.claimed_message_payload: dict[str, object] = {}
        self.move_payload: dict[str, str] = {}
        self.message_query_params: dict[str, str] = {}
        self.intake_query_params: dict[str, str] = {}
        self.claimed_message_query_params: dict[str, str] = {}
        self.attachments_message_id: str | None = None
        self.moves: list[dict[str, object]] = []
        self.forward_payloads: list[dict[str, object]] = []

    def _load_folder_cache(self):
        return self.folder_cache

    def _graph_get(self, url, params=None):
        if url.endswith("/attachments"):
            self.attachments_message_id = url.rsplit("/messages/", 1)[1].split("/attachments", 1)[0]
            return self.attachments_payload
        if "/mailFolders/" in url and "/messages" in url:
            self.message_query_params = dict(params or {})
            self.intake_query_params = dict(params or {})
            return self.messages_payload
        if "/messages/" in url:
            self.message_query_params = dict(params or {})
            self.claimed_message_query_params = dict(params or {})
            return self.claimed_message_payload or self.move_payload
        return self.messages_payload

    def _graph_post(self, url, payload):
        if "/messages/" in url and url.endswith("/move"):
            message_id = url.rsplit("/messages/", 1)[1].split("/move", 1)[0]
            self.moves.append({"message_id": message_id, "payload": payload})
        if "/messages/" in url and url.endswith("/forward"):
            self.forward_payloads.append({"url": url, "payload": payload})
        return self.move_payload


if __name__ == "__main__":
    unittest.main()
