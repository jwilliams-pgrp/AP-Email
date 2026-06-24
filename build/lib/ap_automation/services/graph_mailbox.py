from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from json import JSONDecodeError
from typing import Any

import msal
import requests
from requests.exceptions import JSONDecodeError as RequestsJSONDecodeError

from ap_automation.services.msg_parser import ParsedAttachment, ParsedMsg
from ap_automation.services.thread_context import derive_thread_context

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = ["https://graph.microsoft.com/.default"]
INTAKE_FOLDER_ID = "inbox"
PROCESSING_FOLDER_NAME = "processing"


class GraphMailboxError(RuntimeError):
    """Raised when Graph mailbox operations fail."""


@dataclass(frozen=True)
class GraphMessageEnvelope:
    message_id: str
    internet_message_id: str | None
    web_link: str | None
    parsed_msg: ParsedMsg
    categories: tuple[str, ...]


class GraphMailboxClient:
    def __init__(
        self,
        client_id: str,
        tenant_id: str,
        client_secret: str | None,
        user_principal_name: str,
        intake_folder_id: str,
        timeout_seconds: int = 60,
    ) -> None:
        self._client_id = client_id
        self._tenant_id = tenant_id
        self._client_secret = client_secret
        self._user_principal_name = user_principal_name
        self._intake_folder_id = intake_folder_id
        self._timeout_seconds = timeout_seconds
        self._token: str | None = None
        self._folder_cache: dict[str, dict[str, Any]] = {}

    @classmethod
    def from_env(cls) -> GraphMailboxClient:
        auth_mode = os.getenv("GRAPH_AUTH_MODE", "client_secret").strip().lower()
        if auth_mode != "client_secret":
            raise GraphMailboxError(
                "Unsupported GRAPH_AUTH_MODE for Graph mailbox access: "
                f"{auth_mode}. Supported value: client_secret."
            )
        return cls(
            client_id=_required_env_any("AZURE_CLIENT_ID_MAIL", "AZURE-CLIENT-ID-MAIL"),
            tenant_id=_required_env_any("AZURE_TENANT_ID", "AZURE-TENANT-ID"),
            client_secret=_required_env_any("AZURE_CLIENT_SECRET_MAIL", "AZURE-CLIENT-SECRET-MAIL"),
            user_principal_name=_required_env("USER_PRINCIPAL_NAME_MAIL"),
            intake_folder_id=INTAKE_FOLDER_ID,
            timeout_seconds=int(os.getenv("GRAPH_TIMEOUT_SECONDS", "60")),
        )

    def claim_oldest_from_intake(self) -> GraphMessageEnvelope | None:
        data = self._graph_get(
            f"{GRAPH_BASE_URL}/users/{self._user_principal_name}/mailFolders/{self._intake_folder_id}/messages",
            params={
                "$top": "1",
                "$orderby": "receivedDateTime asc",
                "$select": "id,internetMessageId,webLink,subject,bodyPreview,receivedDateTime,from,categories",
            },
        )
        rows = data.get("value", [])
        if not rows:
            return None
        message = rows[0]
        moved = self.move_message_to_folder_name(str(message["id"]), PROCESSING_FOLDER_NAME)
        moved_message_id = str(moved["message_id"])
        claimed_message = self._graph_get(
            f"{GRAPH_BASE_URL}/users/{self._user_principal_name}/messages/{moved_message_id}",
            params={
                "$select": "id,internetMessageId,webLink,subject,body,bodyPreview,receivedDateTime,from,categories",
            },
        )
        return self._envelope_from_message(claimed_message, include_attachments=True)

    def fetch_latest_from_intake(self) -> GraphMessageEnvelope | None:
        return self.claim_oldest_from_intake()

    def move_message_to_folder_name(self, message_id: str, folder_display_name: str) -> dict[str, Any]:
        destination_id = self._resolve_required_unique_folder_id_by_display_name(folder_display_name)
        return self._move_message(message_id, destination_id)

    def move_message_to_escalate(self, message_id: str) -> dict[str, Any]:
        return self.move_message_to_folder_name(message_id, "ESCALATE")

    def _move_message(self, message_id: str, destination_id: str) -> dict[str, Any]:
        move_response = self._graph_post(
            f"{GRAPH_BASE_URL}/users/{self._user_principal_name}/messages/{message_id}/move",
            {"destinationId": destination_id},
        )
        updated_message_id = move_response.get("id", message_id)
        return {
            "moved": True,
            "destination_folder_id": destination_id,
            "message_id": updated_message_id,
            "office_web_link": move_response.get("webLink") if isinstance(move_response.get("webLink"), str) else None,
        }

    def _envelope_from_message(self, message: dict[str, Any], *, include_attachments: bool = False) -> GraphMessageEnvelope:
        message_id = str(message["id"])
        attachments = self._message_attachments(message_id) if include_attachments else []
        body_text = _graph_body_text(message)
        thread_context = derive_thread_context(body_text)
        parsed = ParsedMsg(
            subject=message.get("subject"),
            sender_email=((message.get("from") or {}).get("emailAddress") or {}).get("address"),
            sender_name=((message.get("from") or {}).get("emailAddress") or {}).get("name"),
            received_at=_parse_graph_datetime(message.get("receivedDateTime")),
            body_text=body_text,
            body_html=_graph_body_html(message),
            transport_headers=None,
            attachments=tuple(attachments),
            metadata={
                "parser": "graph_api",
                "graph_message_id": message_id,
                "office_web_link": message.get("webLink"),
                "thread_context": thread_context.to_metadata(),
            },
        )
        return GraphMessageEnvelope(
            message_id=message_id,
            internet_message_id=message.get("internetMessageId"),
            web_link=message.get("webLink"),
            parsed_msg=parsed,
            categories=tuple(message.get("categories") or ()),
        )

    def list_escalate_messages(self) -> list[GraphMessageEnvelope]:
        folder_id = self._resolve_destination_folder_id(
            parent_folder_hint="ESCALATE",
            destination_display_name="ESCALATE",
            destination_folder_path="ESCALATE",
        )
        rows = self._list_messages(folder_id)
        return [self._envelope_from_message(row) for row in rows]

    def _list_messages(self, folder_id: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        page_url = f"{GRAPH_BASE_URL}/users/{self._user_principal_name}/mailFolders/{folder_id}/messages"
        params: dict[str, str] | None = {
            "$top": "100",
            "$orderby": "receivedDateTime asc",
            "$select": "id,internetMessageId,webLink,subject,bodyPreview,receivedDateTime,from,categories",
        }
        while page_url:
            page = self._graph_get(page_url, params=params)
            rows.extend(row for row in page.get("value", []) if isinstance(row, dict))
            next_link = page.get("@odata.nextLink")
            page_url = next_link if isinstance(next_link, str) else ""
            params = None
        return rows

    def route_message(
        self,
        message_id: str,
        existing_categories: tuple[str, ...],
        parent_folder: str | None,
        label: str | None,
        destination_display_name: str | None = None,
        destination_folder_path: str | None = None,
    ) -> dict[str, Any]:
        updated_message_id = message_id
        resolved_destination_id: str | None = None
        updated_web_link: str | None = None
        final_categories = list(existing_categories)
        if label and label not in final_categories:
            final_categories.append(label)
            self._graph_patch(
                f"{GRAPH_BASE_URL}/users/{self._user_principal_name}/messages/{message_id}",
                {"categories": final_categories},
            )
        if parent_folder:
            resolved_destination_id = self._resolve_destination_folder_id(
                parent_folder_hint=parent_folder,
                destination_display_name=destination_display_name,
                destination_folder_path=destination_folder_path,
            )
            move_response = self._graph_post(
                f"{GRAPH_BASE_URL}/users/{self._user_principal_name}/messages/{message_id}/move",
                {"destinationId": resolved_destination_id},
            )
            updated_message_id = move_response.get("id", message_id)
            if isinstance(move_response.get("webLink"), str):
                updated_web_link = move_response["webLink"]
        if parent_folder and updated_web_link is None:
            routed_message = self._graph_get(
                f"{GRAPH_BASE_URL}/users/{self._user_principal_name}/messages/{updated_message_id}",
                params={"$select": "id,webLink"},
            )
            if isinstance(routed_message.get("webLink"), str):
                updated_web_link = routed_message["webLink"]
        return {
            "moved": bool(parent_folder),
            "destination_folder_id": resolved_destination_id,
            "applied_label": label if label else None,
            "categories": final_categories,
            "message_id": updated_message_id,
            "office_web_link": updated_web_link,
        }

    def forward_message(self, message_id: str, recipient_email: str, comment: str | None = None) -> dict[str, Any]:
        recipient = recipient_email.strip()
        recipients = _parse_recipient_emails(recipient_email)
        if not recipients:
            raise GraphMailboxError("Cannot forward Graph message without a recipient email address.")
        payload: dict[str, Any] = {
            "toRecipients": [
                {"emailAddress": {"address": address}}
                for address in recipients
            ]
        }
        if comment:
            payload["comment"] = comment
        self._graph_post(
            f"{GRAPH_BASE_URL}/users/{self._user_principal_name}/messages/{message_id}/forward",
            payload,
        )
        return {"forwarded": True, "recipient_email": recipient, "recipient_emails": recipients}

    def _resolve_required_unique_folder_id_by_display_name(self, display_name: str) -> str:
        cached = self._load_folder_cache()
        target = display_name.strip().lower()
        matches = [
            folder.get("id")
            for folder in cached.values()
            if str(folder.get("displayName", "")).strip().lower() == target and isinstance(folder.get("id"), str)
        ]
        if len(matches) == 1:
            return str(matches[0])
        if not matches:
            raise GraphMailboxError(f"Required Graph folder {display_name!r} was not found.")
        raise GraphMailboxError(f"Required Graph folder {display_name!r} is ambiguous; found {len(matches)} matches.")

    def _resolve_destination_folder_id(
        self,
        parent_folder_hint: str,
        destination_display_name: str | None,
        destination_folder_path: str | None,
    ) -> str:
        cached = self._load_folder_cache()
        path_segments = self._extract_path_segments(destination_folder_path)
        if path_segments:
            by_path = self._find_folder_id_by_path(cached, path_segments)
            if by_path:
                return by_path

        name_candidates = [parent_folder_hint, destination_display_name]
        for candidate in name_candidates:
            if not candidate:
                continue
            by_name = self._find_unique_folder_id_by_name(cached, candidate)
            if by_name:
                return by_name

        raise GraphMailboxError(
            "Unable to resolve Graph destination folder id from configured hints: "
            f"parent_folder={parent_folder_hint!r}, display_name={destination_display_name!r}, "
            f"folder_path={destination_folder_path!r}"
        )

    def _load_folder_cache(self) -> dict[str, dict[str, Any]]:
        if self._folder_cache:
            return self._folder_cache

        visited: dict[str, dict[str, Any]] = {}
        queue: list[str | None] = [None]
        while queue:
            parent_id = queue.pop(0)
            page_url = (
                f"{GRAPH_BASE_URL}/users/{self._user_principal_name}/mailFolders/{parent_id}/childFolders"
                if parent_id
                else f"{GRAPH_BASE_URL}/users/{self._user_principal_name}/mailFolders"
            )
            first_page = True
            while page_url:
                page = self._graph_get(
                    page_url,
                    params={"$top": "200", "$select": "id,displayName,parentFolderId,childFolderCount"} if first_page else None,
                )
                for row in page.get("value", []):
                    folder_id = row.get("id")
                    if not isinstance(folder_id, str) or folder_id in visited:
                        continue
                    visited[folder_id] = row
                    child_count = row.get("childFolderCount")
                    if isinstance(child_count, int) and child_count > 0:
                        queue.append(folder_id)
                next_link = page.get("@odata.nextLink")
                page_url = next_link if isinstance(next_link, str) else ""
                first_page = False
        self._folder_cache = visited
        return visited

    @staticmethod
    def _extract_path_segments(folder_path: str | None) -> list[str]:
        if not folder_path:
            return []
        normalized = folder_path.replace("\\", "/").strip("/")
        if not normalized:
            return []
        pieces = [segment.strip() for segment in normalized.split("/") if segment.strip()]
        if len(pieces) >= 2 and pieces[0].lower() == "local" and pieces[1].lower() == "outbound":
            pieces = pieces[2:]
        return pieces

    def _find_folder_id_by_path(self, folders: dict[str, dict[str, Any]], path_segments: list[str]) -> str | None:
        if not path_segments:
            return None
        last = path_segments[-1].lower()
        candidates = [
            folder for folder in folders.values() if str(folder.get("displayName", "")).strip().lower() == last
        ]
        for candidate in candidates:
            if self._matches_parent_chain(folders, candidate, path_segments):
                folder_id = candidate.get("id")
                if isinstance(folder_id, str):
                    return folder_id
        return None

    def _matches_parent_chain(
        self,
        folders: dict[str, dict[str, Any]],
        leaf: dict[str, Any],
        path_segments: list[str],
    ) -> bool:
        expected = [segment.lower() for segment in path_segments]
        current = leaf
        index = len(expected) - 1
        while index >= 0:
            current_name = str(current.get("displayName", "")).strip().lower()
            if current_name != expected[index]:
                return False
            parent_id = current.get("parentFolderId")
            if index == 0:
                return True
            if not isinstance(parent_id, str):
                return False
            parent = folders.get(parent_id)
            if parent is None:
                return False
            current = parent
            index -= 1
        return False

    @staticmethod
    def _find_unique_folder_id_by_name(folders: dict[str, dict[str, Any]], name: str) -> str | None:
        target = name.strip().lower()
        matches = [
            folder.get("id")
            for folder in folders.values()
            if str(folder.get("displayName", "")).strip().lower() == target and isinstance(folder.get("id"), str)
        ]
        if len(matches) == 1:
            return matches[0]
        return None

    def _message_attachments(self, message_id: str) -> list[ParsedAttachment]:
        data = self._graph_get(
            f"{GRAPH_BASE_URL}/users/{self._user_principal_name}/messages/{message_id}/attachments",
            params={"$top": "50"},
        )
        parsed: list[ParsedAttachment] = []
        for row in data.get("value", []):
            if row.get("@odata.type") != "#microsoft.graph.fileAttachment":
                continue
            content_bytes = row.get("contentBytes")
            if not isinstance(content_bytes, str):
                continue
            import base64

            parsed.append(
                ParsedAttachment(
                    file_name=row.get("name") or "attachment.bin",
                    content=base64.b64decode(content_bytes),
                    content_type=row.get("contentType"),
                    metadata={
                        "graph_attachment_id": row.get("id"),
                        "content_id": row.get("contentId"),
                        "is_inline": bool(row.get("isInline")),
                    },
                )
            )
        return parsed

    def _get_token(self) -> str:
        if self._token:
            return self._token
        if not self._client_secret:
            raise GraphMailboxError("Graph client secret is required when GRAPH_AUTH_MODE=client_secret.")
        app = msal.ConfidentialClientApplication(
            client_id=self._client_id,
            authority=f"https://login.microsoftonline.com/{self._tenant_id}",
            client_credential=self._client_secret,
        )
        result: dict[str, Any] = app.acquire_token_for_client(scopes=GRAPH_SCOPE)
        token = result.get("access_token")
        if not isinstance(token, str):
            raise GraphMailboxError(
                f"Unable to acquire Graph token: {result.get('error')} {result.get('error_description')}"
            )
        self._token = token
        return token

    def _graph_get(self, url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        response = requests.get(url, headers=self._headers(), params=params, timeout=self._timeout_seconds)
        return self._handle_json_response("GET", url, response)

    def _graph_post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = requests.post(url, headers=self._headers(), json=payload, timeout=self._timeout_seconds)
        return self._handle_json_response("POST", url, response)

    def _graph_patch(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = requests.patch(url, headers=self._headers(), json=payload, timeout=self._timeout_seconds)
        return self._handle_json_response("PATCH", url, response)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _handle_json_response(method: str, url: str, response: requests.Response) -> dict[str, Any]:
        if response.status_code >= 400:
            raise GraphMailboxError(f"Graph {method} failed ({response.status_code}) for {url}: {response.text}")
        if not response.text:
            return {}
        try:
            return response.json()
        except (JSONDecodeError, RequestsJSONDecodeError) as exc:
            raise GraphMailboxError(
                f"Graph {method} returned invalid JSON ({response.status_code}) for {url}: "
                f"{exc.msg} at character {exc.pos}"
            ) from exc


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise GraphMailboxError(f"Missing required environment variable: {name}")
    return value


def _required_env_any(*names: str) -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    raise GraphMailboxError(f"Missing required environment variable: {' or '.join(names)}")


def _parse_recipient_emails(recipient_email: str) -> list[str]:
    return [address for address in (part.strip() for part in re.split(r"[;,]", recipient_email)) if address]


def _parse_graph_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _graph_body_text(message: dict[str, Any]) -> str:
    body = message.get("body")
    if isinstance(body, dict):
        content = body.get("content")
        if isinstance(content, str) and content.strip():
            content_type = str(body.get("contentType") or "").strip().lower()
            if content_type == "html":
                return _html_to_text(content)
            return content
    preview = message.get("bodyPreview")
    return preview if isinstance(preview, str) else ""


def _graph_body_html(message: dict[str, Any]) -> str | None:
    body = message.get("body")
    if not isinstance(body, dict):
        return None
    content = body.get("content")
    content_type = str(body.get("contentType") or "").strip().lower()
    if content_type == "html" and isinstance(content, str) and content.strip():
        return content
    return None


class _HtmlTextExtractor(HTMLParser):
    _BLOCK_TAGS = {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "div",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "li",
        "p",
        "section",
        "table",
        "td",
        "th",
        "tr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._link_stack: list[str | None] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_name = tag.lower()
        if tag_name in self._BLOCK_TAGS:
            self._parts.append("\n")
        if tag_name == "a":
            href = _http_href(attrs)
            self._link_stack.append(href)

    def handle_endtag(self, tag: str) -> None:
        tag_name = tag.lower()
        if tag_name == "a":
            href = self._link_stack.pop() if self._link_stack else None
            if href:
                self._parts.append(f" {href}")
        if tag_name in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if data.strip():
            self._parts.append(data)

    def text(self) -> str:
        lines = [" ".join(line.split()) for line in "".join(self._parts).splitlines()]
        return "\n".join(line for line in lines if line)


def _html_to_text(content: str) -> str:
    parser = _HtmlTextExtractor()
    parser.feed(content)
    parser.close()
    return parser.text()


def _http_href(attrs: list[tuple[str, str | None]]) -> str | None:
    for name, value in attrs:
        if name.lower() != "href" or value is None:
            continue
        href = value.strip()
        if href.lower().startswith(("http://", "https://")):
            return href
    return None
