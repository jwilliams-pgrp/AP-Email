from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ThreadContext:
    latest_body_text: str | None
    quoted_history_text: str | None
    has_quoted_history: bool

    def to_metadata(self) -> dict[str, object]:
        return {
            "latest_body_text": self.latest_body_text,
            "quoted_history_text": self.quoted_history_text,
            "has_quoted_history": self.has_quoted_history,
            "thread_context_version": "thread_context.v1",
        }


_BOUNDARY_PATTERNS = (
    re.compile(r"^\s*-{2,}\s*Original Message\s*-{2,}\s*$", re.IGNORECASE),
    re.compile(r"^\s*_{5,}\s*$"),
    re.compile(r"^\s*-{5,}\s*$"),
    re.compile(r"^\s*From:\s+.+", re.IGNORECASE),
    re.compile(r"^\s*Sent:\s+.+", re.IGNORECASE),
    re.compile(r"^\s*To:\s+.+", re.IGNORECASE),
    re.compile(r"^\s*Subject:\s+.+", re.IGNORECASE),
    re.compile(r"^\s*On .+ wrote:\s*$", re.IGNORECASE),
)


def derive_thread_context(body_text: str | None) -> ThreadContext:
    normalized = _normalize_body_text(body_text)
    if not normalized:
        return ThreadContext(latest_body_text=None, quoted_history_text=None, has_quoted_history=False)

    boundary_index = _first_boundary_index(normalized.splitlines())
    if boundary_index is None:
        return ThreadContext(latest_body_text=normalized, quoted_history_text=None, has_quoted_history=False)

    latest = _strip_quoted_markers("\n".join(normalized.splitlines()[:boundary_index]))
    quoted = "\n".join(normalized.splitlines()[boundary_index:]).strip() or None
    return ThreadContext(
        latest_body_text=latest or None,
        quoted_history_text=quoted,
        has_quoted_history=bool(quoted),
    )


def latest_body_text(metadata: dict[str, object] | None, fallback_body_text: str | None) -> str | None:
    thread_context = (metadata or {}).get("thread_context")
    if isinstance(thread_context, dict):
        value = thread_context.get("latest_body_text")
        if isinstance(value, str) and value.strip():
            return value
    return fallback_body_text


def _first_boundary_index(lines: list[str]) -> int | None:
    for index, line in enumerate(lines):
        if any(pattern.match(line) for pattern in _BOUNDARY_PATTERNS):
            if _is_header_cluster(lines, index) or "original message" in line.lower() or "wrote:" in line.lower():
                return index
    return None


def _is_header_cluster(lines: list[str], index: int) -> bool:
    window = lines[index : min(len(lines), index + 6)]
    header_count = sum(1 for line in window if re.match(r"^\s*(From|Sent|To|Cc|Subject|Date):\s+.+", line, re.IGNORECASE))
    return header_count >= 2


def _normalize_body_text(body_text: str | None) -> str:
    if not body_text:
        return ""
    value = body_text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def _strip_quoted_markers(value: str) -> str:
    lines = []
    for line in value.splitlines():
        stripped = line.strip()
        if stripped and set(stripped) <= {">"}:
            continue
        lines.append(line)
    return "\n".join(lines).strip()
