from __future__ import annotations

import json
from unittest.mock import Mock, patch

import requests

from ap_automation.services.teams_notifier import TeamsNotificationError, TeamsNotifier, TeamsReviewNotification


def test_teams_notifier_from_env_uses_new_variable_names() -> None:
    env = {
        "TEAMS-WEBHOOK-URL-PROPERTIES-AP": "https://example.invalid/webhook",
        "TEAMS-TEAM-NAME-PROPERTIES-AP": "Properties AP",
        "TEAMS-CHANNEL-NAME-PROPERTIES-AP": "AP Review",
        "TEAMS_WEBHOOK_TIMEOUT_SECONDS": "11",
    }

    with patch.dict("os.environ", env, clear=True):
        notifier = TeamsNotifier.from_env()

    assert notifier._webhook_url == "https://example.invalid/webhook"
    assert notifier._team_name == "Properties AP"
    assert notifier._channel_name == "AP Review"
    assert notifier._timeout_seconds == 11


def test_teams_notifier_from_env_uses_azure_safe_variable_names() -> None:
    env = {
        "TEAMS_WEBHOOK_URL_PROPERTIES_AP": "https://example.invalid/webhook",
        "TEAMS_TEAM_NAME_PROPERTIES_AP": "Properties AP",
        "TEAMS_CHANNEL_NAME_PROPERTIES_AP": "AP Review",
    }

    with patch.dict("os.environ", env, clear=True):
        notifier = TeamsNotifier.from_env()

    assert notifier._webhook_url == "https://example.invalid/webhook"
    assert notifier._team_name == "Properties AP"
    assert notifier._channel_name == "AP Review"


def test_teams_notifier_from_env_reports_missing_new_variable_name() -> None:
    with patch.dict("os.environ", {}, clear=True):
        try:
            TeamsNotifier.from_env()
        except TeamsNotificationError as exc:
            assert "TEAMS_WEBHOOK_URL_PROPERTIES_AP" in str(exc)
            assert "TEAMS-WEBHOOK-URL-PROPERTIES-AP" in str(exc)
        else:
            raise AssertionError("expected TeamsNotificationError")


def test_teams_notifier_posts_rapid7_webhook_payload_as_json_body() -> None:
    response = Mock()
    response.status_code = 200

    notifier = TeamsNotifier(
        webhook_url="https://example.invalid/webhooks/test",
        team_name="Properties AP",
        channel_name="Properties AP",
    )

    with patch("ap_automation.services.teams_notifier.requests.post", return_value=response) as post:
        result = notifier.send_review_notification(
            TeamsReviewNotification(
                email_subject="Test subject",
                routing_path="Unassigned",
                office_web_link="Not available",
            )
        )

    post.assert_called_once()
    _, kwargs = post.call_args
    assert kwargs["headers"] == {"Content-Type": "application/json; charset=utf-8"}
    assert kwargs["timeout"] == 30
    assert json.loads(kwargs["data"]) == {
        "Team": "Properties AP",
        "Channel": "Properties AP",
        "Message": (
            "<strong>Email needs escalation</strong><br><br>"
            "<strong>Email:</strong> Test subject<br>"
            "<strong>Routing path:</strong> Unassigned<br><br>"
            "Outlook link not available<br><br>"
            "Please review the message and take the appropriate action."
        ),
    }
    assert result == {"team": "Properties AP", "channel": "Properties AP", "status_code": 200}


def test_teams_notifier_removes_accidental_route_value_quotes() -> None:
    response = Mock()
    response.status_code = 200

    notifier = TeamsNotifier(
        webhook_url="https://example.invalid/webhooks/test",
        team_name='"Properties AP"',
        channel_name='"Properties AP"',
    )

    with patch("ap_automation.services.teams_notifier.requests.post", return_value=response) as post:
        result = notifier.send_review_notification(
            TeamsReviewNotification(
                email_subject="REMINDER - Your CoServ bill is due",
                routing_path="ESCALATE / ESCALATE / LINK-ONLY",
                office_web_link="https://outlook.office365.com/owa/?ItemID=test",
            )
        )

    _, kwargs = post.call_args
    payload = json.loads(kwargs["data"])
    assert payload["Team"] == "Properties AP"
    assert payload["Channel"] == "Properties AP"
    assert result["team"] == "Properties AP"
    assert result["channel"] == "Properties AP"


def test_teams_notifier_escapes_formatted_review_message_values() -> None:
    response = Mock()
    response.status_code = 200

    notifier = TeamsNotifier(
        webhook_url="https://example.invalid/webhooks/test",
        team_name="Properties AP",
        channel_name="Properties AP",
    )

    with patch("ap_automation.services.teams_notifier.requests.post", return_value=response) as post:
        notifier.send_review_notification(
            TeamsReviewNotification(
                email_subject='Bad <subject> & "quote"',
                routing_path="ESCALATE / LINK-ONLY",
                office_web_link='https://outlook.office365.com/owa/?ItemID=a&viewmodel="ReadMessageItem"',
            )
        )

    _, kwargs = post.call_args
    assert json.loads(kwargs["data"])["Message"] == (
        "<strong>Email needs escalation</strong><br><br>"
        "<strong>Email:</strong> Bad &lt;subject&gt; &amp; &quot;quote&quot;<br>"
        "<strong>Routing path:</strong> ESCALATE / LINK-ONLY<br><br>"
        '<a href="https://outlook.office365.com/owa/?ItemID=a&amp;viewmodel=&quot;ReadMessageItem&quot;">Open email in Outlook</a><br><br>'
        "Please review the message and take the appropriate action."
    )


def test_teams_notifier_retries_ssl_failures_with_default_trust_store() -> None:
    response = Mock()
    response.status_code = 200
    session = Mock()
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=None)
    session.post.return_value = response

    notifier = TeamsNotifier(
        webhook_url="https://example.invalid/webhooks/test",
        team_name="Properties AP",
        channel_name="Properties AP",
    )

    with (
        patch(
            "ap_automation.services.teams_notifier.requests.post",
            side_effect=requests.exceptions.SSLError("certificate verify failed"),
        ) as post,
        patch("ap_automation.services.teams_notifier.requests.Session", return_value=session),
    ):
        result = notifier.send_review_notification(
            TeamsReviewNotification(
                email_subject="Test subject",
                routing_path="Unassigned",
                office_web_link="Not available",
            )
        )

    post.assert_called_once()
    session.mount.assert_called_once()
    session.post.assert_called_once()
    _, kwargs = session.post.call_args
    assert kwargs["headers"] == {"Content-Type": "application/json; charset=utf-8"}
    assert kwargs["timeout"] == 30
    assert result == {"team": "Properties AP", "channel": "Properties AP", "status_code": 200}
