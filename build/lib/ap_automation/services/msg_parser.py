from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from struct import unpack_from
from typing import Any

from ap_automation.services.thread_context import derive_thread_context


END_OF_CHAIN = 0xFFFFFFFE
FREE_SECTOR = 0xFFFFFFFF
FAT_SECTOR = 0xFFFFFFFD
DIFAT_SECTOR = 0xFFFFFFFC


class MsgParseError(ValueError):
    """Raised when a local Outlook MSG file cannot be parsed safely."""


@dataclass(frozen=True)
class ParsedAttachment:
    file_name: str
    content: bytes
    content_type: str | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class ParsedMsg:
    subject: str | None
    sender_email: str | None
    sender_name: str | None
    received_at: datetime | None
    body_text: str | None
    transport_headers: str | None
    attachments: tuple[ParsedAttachment, ...]
    metadata: dict[str, Any]
    body_html: str | None = None


def parse_msg(path: Path) -> ParsedMsg:
    compound = CompoundFile(path.read_bytes())
    streams = compound.streams()

    subject = _first_text(streams, ("0037001F", "0037001E"))
    sender_name = _first_text(streams, ("0C1A001F", "0C1A001E"))
    sender_email = _first_text(streams, ("5D01001F", "5D01001E", "0C1F001F", "0C1F001E"))
    body_text = _first_text(streams, ("1000001F", "1000001E"))
    body_html = _first_html(streams, ("10130102", "1013001F", "1013001E"))
    thread_context = derive_thread_context(body_text)
    transport_headers = _first_text(streams, ("007D001F", "007D001E"))
    received_at = _first_filetime(streams, ("0E060040", "0E060048"))
    attachments = tuple(_attachments(streams))

    return ParsedMsg(
        subject=subject,
        sender_email=sender_email,
        sender_name=sender_name,
        received_at=received_at,
        body_text=body_text,
        body_html=body_html,
        transport_headers=transport_headers,
        attachments=attachments,
        metadata={
            "parser": "local_msg_cfb",
            "stream_count": len(streams),
            "has_body_text": bool(body_text),
            "has_body_html": bool(body_html),
            "has_transport_headers": bool(transport_headers),
            "thread_context": thread_context.to_metadata(),
        },
    )


class CompoundFile:
    def __init__(self, data: bytes) -> None:
        if data[:8] != bytes.fromhex("D0CF11E0A1B11AE1"):
            raise MsgParseError("Source file is not an OLE compound MSG file.")
        self._data = data
        self._sector_size = 1 << _u16(data, 30)
        self._mini_sector_size = 1 << _u16(data, 32)
        self._first_dir_sector = _u32(data, 48)
        self._mini_stream_cutoff = _u32(data, 56)
        self._first_mini_fat_sector = _u32(data, 60)
        self._mini_fat_sector_count = _u32(data, 64)
        self._first_difat_sector = _u32(data, 68)
        self._difat_sector_count = _u32(data, 72)
        self._fat = self._read_fat()
        self._entries = self._read_directory()
        self._root_stream = self._read_regular_stream(self._entries[0].first_sector, self._entries[0].size)
        self._mini_fat = self._read_mini_fat()

    def streams(self) -> dict[tuple[str, ...], bytes]:
        result: dict[tuple[str, ...], bytes] = {}
        self._collect_streams(0, (), result)
        return result

    def _read_fat(self) -> list[int]:
        difat = [_u32(self._data, 76 + index * 4) for index in range(109)]
        next_difat = self._first_difat_sector
        for _ in range(self._difat_sector_count):
            if next_difat in (END_OF_CHAIN, FREE_SECTOR):
                break
            sector = self._sector(next_difat)
            entries_per_sector = self._sector_size // 4
            difat.extend(_u32(sector, index * 4) for index in range(entries_per_sector - 1))
            next_difat = _u32(sector, self._sector_size - 4)

        fat: list[int] = []
        for sector_id in difat:
            if sector_id in (FREE_SECTOR, END_OF_CHAIN, FAT_SECTOR, DIFAT_SECTOR):
                continue
            sector = self._sector(sector_id)
            fat.extend(_u32(sector, index) for index in range(0, self._sector_size, 4))
        if not fat:
            raise MsgParseError("MSG file has no FAT sectors.")
        return fat

    def _read_directory(self) -> list["_DirectoryEntry"]:
        directory = self._read_regular_stream(self._first_dir_sector, None)
        entries: list[_DirectoryEntry] = []
        for offset in range(0, len(directory), 128):
            entry = directory[offset : offset + 128]
            if len(entry) < 128:
                continue
            name_length = _u16(entry, 64)
            if name_length < 2:
                name = ""
            else:
                name = entry[: name_length - 2].decode("utf-16le", errors="replace")
            entries.append(
                _DirectoryEntry(
                    name=name,
                    entry_type=entry[66],
                    left=_u32(entry, 68),
                    right=_u32(entry, 72),
                    child=_u32(entry, 76),
                    first_sector=_u32(entry, 116),
                    size=_u64(entry, 120),
                )
            )
        if not entries or entries[0].entry_type != 5:
            raise MsgParseError("MSG root storage directory is missing.")
        return entries

    def _read_mini_fat(self) -> list[int]:
        if self._first_mini_fat_sector in (END_OF_CHAIN, FREE_SECTOR) or self._mini_fat_sector_count == 0:
            return []
        stream = self._read_regular_stream(self._first_mini_fat_sector, self._mini_fat_sector_count * self._sector_size)
        return [_u32(stream, index) for index in range(0, len(stream), 4)]

    def _read_stream(self, entry: "_DirectoryEntry") -> bytes:
        if entry.size < self._mini_stream_cutoff and entry.entry_type == 2:
            return self._read_mini_stream(entry.first_sector, entry.size)
        return self._read_regular_stream(entry.first_sector, entry.size)

    def _read_regular_stream(self, first_sector: int, size: int | None) -> bytes:
        if first_sector in (END_OF_CHAIN, FREE_SECTOR):
            return b""
        chunks: list[bytes] = []
        sector_id = first_sector
        seen: set[int] = set()
        while sector_id not in (END_OF_CHAIN, FREE_SECTOR):
            if sector_id in seen:
                raise MsgParseError("Cycle detected in MSG sector chain.")
            seen.add(sector_id)
            chunks.append(self._sector(sector_id))
            if sector_id >= len(self._fat):
                raise MsgParseError("MSG sector chain references a missing FAT entry.")
            sector_id = self._fat[sector_id]
        data = b"".join(chunks)
        return data if size is None else data[:size]

    def _read_mini_stream(self, first_sector: int, size: int) -> bytes:
        if first_sector in (END_OF_CHAIN, FREE_SECTOR):
            return b""
        chunks: list[bytes] = []
        sector_id = first_sector
        seen: set[int] = set()
        while sector_id not in (END_OF_CHAIN, FREE_SECTOR):
            if sector_id in seen:
                raise MsgParseError("Cycle detected in MSG mini-sector chain.")
            seen.add(sector_id)
            offset = sector_id * self._mini_sector_size
            chunks.append(self._root_stream[offset : offset + self._mini_sector_size])
            if sector_id >= len(self._mini_fat):
                raise MsgParseError("MSG mini-sector chain references a missing MiniFAT entry.")
            sector_id = self._mini_fat[sector_id]
        return b"".join(chunks)[:size]

    def _sector(self, sector_id: int) -> bytes:
        offset = (sector_id + 1) * self._sector_size
        end = offset + self._sector_size
        if end > len(self._data):
            raise MsgParseError("MSG sector points beyond end of file.")
        return self._data[offset:end]

    def _collect_streams(self, entry_id: int, prefix: tuple[str, ...], result: dict[tuple[str, ...], bytes]) -> None:
        for child_id in self._child_ids(self._entries[entry_id].child):
            entry = self._entries[child_id]
            if entry.entry_type == 1:
                self._collect_streams(child_id, (*prefix, entry.name), result)
            elif entry.entry_type == 2:
                result[(*prefix, entry.name)] = self._read_stream(entry)

    def _child_ids(self, child_id: int) -> list[int]:
        if child_id in (FREE_SECTOR, END_OF_CHAIN) or child_id >= len(self._entries):
            return []
        entry = self._entries[child_id]
        return [*self._child_ids(entry.left), child_id, *self._child_ids(entry.right)]


@dataclass(frozen=True)
class _DirectoryEntry:
    name: str
    entry_type: int
    left: int
    right: int
    child: int
    first_sector: int
    size: int


def _attachments(streams: dict[tuple[str, ...], bytes]) -> list[ParsedAttachment]:
    attachment_roots = sorted({path[0] for path in streams if path and path[0].startswith("__attach_version1.0_")})
    attachments: list[ParsedAttachment] = []
    for index, root in enumerate(attachment_roots):
        scoped = {path[-1]: value for path, value in streams.items() if path and path[0] == root}
        content = _first_named_stream(scoped, ("37010102",))
        if content is None:
            continue
        file_name = (
            _first_named_text(scoped, ("3707001F", "3707001E", "3704001F", "3704001E"))
            or f"attachment-{index + 1}.bin"
        )
        content_type = _first_named_text(scoped, ("370E001F", "370E001E"))
        content_id = _first_named_text(scoped, ("3712001F", "3712001E"))
        attachments.append(
            ParsedAttachment(
                file_name=file_name,
                content=content,
                content_type=content_type,
                metadata={
                    "msg_attachment_storage": root,
                    "content_id": content_id,
                    "is_inline": bool(content_id),
                    "method": _first_named_int(scoped, ("37050003",)),
                },
            )
        )
    return attachments


def _first_text(streams: dict[tuple[str, ...], bytes], property_ids: tuple[str, ...]) -> str | None:
    for path, data in streams.items():
        if len(path) == 1:
            value = _decode_property_stream(path[-1], data, property_ids)
            if value:
                return value
    return None


def _first_html(streams: dict[tuple[str, ...], bytes], property_ids: tuple[str, ...]) -> str | None:
    for path, data in streams.items():
        if len(path) != 1:
            continue
        property_id = _property_id(path[-1])
        if property_id not in property_ids:
            continue
        if property_id.endswith("001F") or property_id.endswith("001E"):
            value = _decode_property_stream(path[-1], data, property_ids)
        else:
            value = _decode_html_bytes(data)
        if value:
            return value
    return None


def _first_filetime(streams: dict[tuple[str, ...], bytes], property_ids: tuple[str, ...]) -> datetime | None:
    for path, data in streams.items():
        if len(path) == 1 and _property_id(path[-1]) in property_ids and len(data) >= 8:
            value = _u64(data, 0)
            if value:
                return datetime.fromtimestamp((value - 116444736000000000) / 10_000_000, tz=timezone.utc)
    return None


def _first_named_stream(streams: dict[str, bytes], property_ids: tuple[str, ...]) -> bytes | None:
    for name, data in streams.items():
        if _property_id(name) in property_ids:
            return data
    return None


def _first_named_text(streams: dict[str, bytes], property_ids: tuple[str, ...]) -> str | None:
    for name, data in streams.items():
        value = _decode_property_stream(name, data, property_ids)
        if value:
            return value
    return None


def _first_named_int(streams: dict[str, bytes], property_ids: tuple[str, ...]) -> int | None:
    for name, data in streams.items():
        if _property_id(name) in property_ids and len(data) >= 4:
            return _u32(data, 0)
    return None


def _decode_property_stream(name: str, data: bytes, property_ids: tuple[str, ...]) -> str | None:
    property_id = _property_id(name)
    if property_id not in property_ids:
        return None
    if property_id.endswith("001F"):
        return data.decode("utf-16le", errors="replace").rstrip("\x00") or None
    if property_id.endswith("001E"):
        return data.decode("cp1252", errors="replace").rstrip("\x00") or None
    return None


def _decode_html_bytes(data: bytes) -> str | None:
    if not data:
        return None
    for encoding in ("utf-8-sig", "utf-16le", "cp1252"):
        try:
            value = data.decode(encoding).rstrip("\x00")
        except UnicodeDecodeError:
            continue
        if "<" in value and ">" in value:
            return value
    return data.decode("cp1252", errors="replace").rstrip("\x00") or None


def _property_id(name: str) -> str:
    if name.startswith("__substg1.0_"):
        return name.removeprefix("__substg1.0_").upper()
    return name.upper()


def _u16(data: bytes, offset: int) -> int:
    return unpack_from("<H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return unpack_from("<I", data, offset)[0]


def _u64(data: bytes, offset: int) -> int:
    return unpack_from("<Q", data, offset)[0]
