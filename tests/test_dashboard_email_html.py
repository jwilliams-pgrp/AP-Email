from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api import main
from app.api import dashboard_service as service
from ap_automation.services.local_artifacts import LocalArtifactStore
from ap_automation.services.msg_parser import ParsedAttachment


def test_rewrite_cid_urls_rewrites_inline_references() -> None:
    html = '<img src="cid:image001.png@01DCABD7.39567D00"><a href=\'cid:doc.pdf\'>open</a>'
    rewritten = main._rewrite_cid_urls(html, "abc-123")
    assert '/api/emails/abc-123/inline/image001.png%4001DCABD7.39567D00' in rewritten
    assert "/api/emails/abc-123/inline/doc.pdf" in rewritten


def test_email_preview_strips_outlook_noise_and_preserves_forwarded_invoice_text(tmp_path: Path) -> None:
    html = """
    <html><head>
      <!--[if gte mso 9]><xml><o:OfficeDocumentSettings></o:OfficeDocumentSettings></xml><![endif]-->
      <style>
        @font-face { font-family: Aptos; src: url("https://example.com/font.woff"); }
        @page WordSection1 { size: 8.5in 11.0in; }
        .MsoNormal { margin: 0in; color: red; }
      </style>
      <meta name="Generator" content="Microsoft Word 15">
    </head><body>
      <p class="MsoNormal" style="color:red" onclick="steal()">Please see forwarded invoice below.</p>
      <div>From: Vendor Billing &lt;billing@example.com&gt;</div>
      <div>Invoice # INV-1042 Amount Due $1,245.67</div>
      <script>alert("x")</script>
    </body></html>
    """
    path = LocalArtifactStore(tmp_path).write_email_html_preview(
        "email-1",
        "Invoice",
        "AP",
        "ap@example.com",
        "2026-06-01T10:00:00Z",
        "plain fallback",
        html,
        (),
    )

    rendered = (tmp_path / path).read_text(encoding="utf-8")

    assert "Please see forwarded invoice below." in rendered
    assert "From: Vendor Billing" in rendered
    assert "Invoice # INV-1042 Amount Due $1,245.67" in rendered
    assert "@font-face" not in rendered
    assert "@page" not in rendered
    assert ".MsoNormal" not in rendered
    assert "OfficeDocumentSettings" not in rendered
    assert "<script" not in rendered
    assert "onclick" not in rendered
    assert "style=" not in rendered


def test_email_preview_preserves_safe_links_and_cid_images_but_blocks_remote_images(tmp_path: Path) -> None:
    html = """
    <p>Pay online at <a href="https://example.com/pay" onclick="bad()">portal</a>.</p>
    <p><a href="javascript:alert(1)">bad link text remains</a></p>
    <img src="cid:image001.png@01DCABD7.39567D00" onerror="bad()">
    <img src="https://example.com/tracker.png">
    """
    attachment = ParsedAttachment(
        file_name="image001.png",
        content=b"png",
        content_type="image/png",
        metadata={"content_id": "image001.png@01DCABD7.39567D00", "is_inline": True},
    )

    path = LocalArtifactStore(tmp_path).write_email_html_preview(
        "email-1",
        "Invoice",
        None,
        "ap@example.com",
        None,
        None,
        html,
        (attachment,),
    )

    rendered = (tmp_path / path).read_text(encoding="utf-8")

    assert 'href="https://example.com/pay"' in rendered
    assert "bad link text remains" in rendered
    assert "javascript:" not in rendered
    assert 'src="cid:image001.png@01DCABD7.39567D00"' in rendered
    assert "https://example.com/tracker.png" not in rendered
    assert "onerror" not in rendered
    assert "image001.png" in rendered


def test_email_preview_plain_text_only_still_renders_cleanly(tmp_path: Path) -> None:
    path = LocalArtifactStore(tmp_path).write_email_html_preview(
        "email-1",
        "Invoice",
        None,
        "ap@example.com",
        None,
        "Line 1\nLine <unsafe>",
        None,
        (),
    )

    rendered = (tmp_path / path).read_text(encoding="utf-8")

    assert "Line 1" in rendered
    assert "Line &lt;unsafe&gt;" in rendered
    assert "<unsafe>" not in rendered


def test_resolve_inline_attachment_path_matches_content_id(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    inline_file = tmp_path / "inline.png"
    inline_file.write_bytes(b"inline")

    rows = [
        {
            "file_name": "image001.png",
            "content_type": "image/png",
            "storage_path": "local/attachments/x/001-image001.png",
            "metadata": {"content_id": "image001.png@01DCABD7.39567D00"},
        }
    ]

    class FakeConnection:
        def execute(self, _query: str, _params: tuple[str]):
            class Result:
                def fetchall(self):
                    return rows

            return Result()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(main, "connect", lambda: FakeConnection())
    monkeypatch.setattr(main, "require_local_artifact", lambda _path: inline_file)

    path, content_type = main._resolve_inline_attachment_path("email-1", "image001.png@01DCABD7.39567D00")
    assert path == inline_file
    assert content_type == "image/png"


def test_resolve_inline_attachment_path_raises_when_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeConnection:
        def execute(self, _query: str, _params: tuple[str]):
            class Result:
                def fetchall(self):
                    return []

            return Result()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(main, "connect", lambda: FakeConnection())
    with pytest.raises(HTTPException) as exc_info:
        main._resolve_inline_attachment_path("email-1", "missing-content-id")
    assert exc_info.value.status_code == 404


def test_asset_payload_requires_asset_name() -> None:
    with pytest.raises(HTTPException) as exc_info:
        main._asset_payload({"asset_alias": "GW19"})
    assert exc_info.value.status_code == 400


def test_asset_routes_do_not_expose_legacy_property_paths() -> None:
    paths = {route.path for route in main.app.routes}
    assert "/api/workflow/assets" in paths
    assert "/api/monitor/assets" in paths
    assert "/api/workflow/properties" not in paths
    assert "/api/monitor/properties" not in paths


def test_workflow_asset_custom_reads_asset_custom_table(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        {
            "asset_custom_id": "7",
            "asset_name": "Custom Asset",
            "asset_alias": "Custom Alias",
            "address": "123 Main St",
            "comment": "Manual override",
            "destination_code": "ESCALATE_SPECIAL_ADDRESS",
            "created_at": None,
        }
    ]
    connection = _FakeDashboardConnection({"from asset_custom": rows})
    monkeypatch.setattr(service, "connect", lambda: connection)

    result = service.workflow_asset_custom()

    assert result == rows
    assert "from asset_custom" in connection.sql
    assert "vw_asset_lookup" not in connection.sql


def test_monitor_summary_counts_all_escalate_queue_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _FakeDashboardConnection(
            {
                "from decisions d": [],
                "from escalate_queue": [{"count": 0}],
                "avg(extract": [{"seconds": None}],
                "count(*) filter": [{"high": 0, "medium": 0, "low": 0}],
                "select status, count(*)::int as count": [],
            }
        )
    monkeypatch.setattr(service, "connect", lambda: connection)

    summary = service.monitor_summary()

    assert summary["open_escalate_count"] == 0
    assert connection.matching_sql("from escalate_queue")[0].strip() == "select count(*)::int as count from escalate_queue"


def test_monitor_escalate_emails_reads_reloaded_mirror_without_status_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        {
            "escalate_id": "esc-1",
            "email_id": "email-1",
            "status": "open",
            "priority": "normal",
            "reason": "Message is currently in the ESCALATE folder.",
            "assigned_to": None,
            "created_at": None,
            "subject": "Needs escalation",
            "sender_email": "sender@example.com",
            "office_web_link": "https://outlook.office.com/mail/ESCALATE/id/msg-1",
            "sender_name": "Sender",
            "outcome": None,
            "confidence": None,
        }
    ]
    connection = _FakeDashboardConnection({"from escalate_queue rq": rows})
    monkeypatch.setattr(service, "connect", lambda: connection)

    result = service.monitor_escalate_emails()

    assert result == rows
    assert "where rq.status" not in connection.sql.lower()


class _FakeDashboardConnection:
    def __init__(self, rows_by_pattern: dict[str, list[dict]]) -> None:
        self.rows_by_pattern = rows_by_pattern
        self.sql = ""
        self.params = ()
        self.statements: list[str] = []
        self._rows: list[dict] = []

    def execute(self, sql: str, params: object = ()) -> "_FakeDashboardConnection":
        self.sql = sql
        self.params = params
        self.statements.append(sql)
        self._rows = []
        for pattern, rows in self.rows_by_pattern.items():
            if pattern in sql:
                self._rows = rows
                break
        return self

    def matching_sql(self, pattern: str) -> list[str]:
        return [sql for sql in self.statements if pattern in sql]

    def fetchall(self) -> list[dict]:
        return self._rows

    def fetchone(self) -> dict:
        return self._rows[0] if self._rows else {}

    def __enter__(self) -> "_FakeDashboardConnection":
        return self

    def __exit__(self, exc_type, exc, tb):
        return False
