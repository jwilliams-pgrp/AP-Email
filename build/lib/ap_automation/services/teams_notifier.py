from __future__ import annotations

import json
import os
import ssl
from dataclasses import dataclass
from html import escape
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3 import PoolManager


class TeamsNotificationError(RuntimeError):
    """Raised when a Teams notification cannot be delivered."""


@dataclass(frozen=True)
class TeamsReviewNotification:
    email_subject: str | None
    routing_path: str | None
    office_web_link: str | None


class TeamsNotifier:
    def __init__(
        self,
        webhook_url: str,
        team_name: str,
        channel_name: str,
        timeout_seconds: int = 30,
    ) -> None:
        self._webhook_url = webhook_url
        self._team_name = _clean_route_value(team_name)
        self._channel_name = _clean_route_value(channel_name)
        self._timeout_seconds = timeout_seconds

    @classmethod
    def from_env(cls) -> TeamsNotifier:
        return cls(
            webhook_url=_required_env_any("TEAMS_WEBHOOK_URL_PROPERTIES_AP", "TEAMS-WEBHOOK-URL-PROPERTIES-AP"),
            team_name=_required_env_any("TEAMS_TEAM_NAME_PROPERTIES_AP", "TEAMS-TEAM-NAME-PROPERTIES-AP"),
            channel_name=_required_env_any("TEAMS_CHANNEL_NAME_PROPERTIES_AP", "TEAMS-CHANNEL-NAME-PROPERTIES-AP"),
            timeout_seconds=int(os.getenv("TEAMS_WEBHOOK_TIMEOUT_SECONDS", "30")),
        )

    def send_review_notification(self, notification: TeamsReviewNotification) -> dict[str, Any]:
        message = _format_review_message(notification)
        payload = {
            "Team": self._team_name,
            "Channel": self._channel_name,
            "Message": message,
        }
        response = _post_json(
            self._webhook_url,
            payload,
            self._timeout_seconds,
        )
        if response.status_code >= 400:
            raise TeamsNotificationError(
                f"Teams notification failed ({response.status_code}): {response.text}"
            )
        return {"team": self._team_name, "channel": self._channel_name, "status_code": response.status_code}


def _format_review_message(notification: TeamsReviewNotification) -> str:
    email_subject = escape(notification.email_subject or "No subject")
    routing_path = escape(notification.routing_path or "Unassigned")
    office_web_link = _optional_link(notification.office_web_link)
    office_link_html = (
        f'<a href="{escape(office_web_link, quote=True)}">Open email in Outlook</a>'
        if office_web_link
        else "Outlook link not available"
    )
    return (
        "<strong>Email needs escalation</strong><br><br>"
        f"<strong>Email:</strong> {email_subject}<br>"
        f"<strong>Routing path:</strong> {routing_path}<br><br>"
        f"{office_link_html}<br><br>"
        "Please review the message and take the appropriate action."
    )


def _optional_link(value: str | None) -> str | None:
    if not value:
        return None
    stripped = value.strip()
    if not stripped or stripped.lower() == "not available":
        return None
    return stripped


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise TeamsNotificationError(f"Missing required environment variable: {name}")
    return value


def _required_env_any(*names: str) -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    joined = " or ".join(names)
    raise TeamsNotificationError(f"Missing required environment variable: {joined}")


def _clean_route_value(value: str) -> str:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] == '"':
        return stripped[1:-1]
    return stripped


def _post_json(webhook_url: str, payload: dict[str, Any], timeout_seconds: int) -> requests.Response:
    data = json.dumps(payload)
    headers = {"Content-Type": "application/json; charset=utf-8"}
    try:
        return requests.post(
            webhook_url,
            data=data,
            headers=headers,
            timeout=timeout_seconds,
        )
    except requests.exceptions.SSLError:
        with requests.Session() as session:
            session.mount("https://", _DefaultTrustStoreHttpAdapter())
            return session.post(
                webhook_url,
                data=data,
                headers=headers,
                timeout=timeout_seconds,
            )


class _DefaultTrustStoreHttpAdapter(HTTPAdapter):
    def init_poolmanager(
        self,
        connections: int,
        maxsize: int,
        block: bool = False,
        **pool_kwargs: Any,
    ) -> None:
        context = ssl.create_default_context()
        context.load_default_certs(ssl.Purpose.SERVER_AUTH)
        self.poolmanager = PoolManager(
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            ssl_context=context,
            **pool_kwargs,
        )
