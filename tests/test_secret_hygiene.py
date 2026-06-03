from __future__ import annotations

from pathlib import Path


CHECKED_FILES = [
    Path("db/README.md"),
    Path("launch-dashboard-api.ps1"),
    Path("infra/main.parameters.example.json"),
    Path("infra/main.parameters.nonprod.json"),
    Path("infra/main.parameters.prod.json"),
    Path(".env.example"),
]


def test_runtime_templates_do_not_contain_known_local_password() -> None:
    for path in CHECKED_FILES:
        text = path.read_text(encoding="utf-8")
        assert "llamas" not in text.lower()
        assert "password=llamas" not in text.lower()
