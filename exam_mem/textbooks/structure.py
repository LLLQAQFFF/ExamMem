"""Deterministic textbook section recovery and chapter-aware chunking."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable
import unicodedata

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def build_section_tree(
    *,
    version_id: str,
    markdown: str,
    blocks: Iterable[dict[str, Any]] = (),
    outline: Iterable[dict[str, Any]] = (),
    pages: Iterable[dict[str, Any]] = (),
) -> tuple[dict[str, Any], ...]:
    """Recover a stable hierarchy without treating fixed-size chunks as chapters."""
    outlined = _outline_section_tree(version_id=version_id, outline=outline, pages=pages)
    if outlined:
        return outlined

    lines = markdown.splitlines()
    headings: list[tuple[int, int, str]] = []
    for line_number, line in enumerate(lines):
        match = _HEADING.match(line)
        if match:
            headings.append((line_number, len(match.group(1)), match.group(2).strip()))
    inferred = not headings
    if inferred:
        headings = [(0, 1, "Full text")]

    page_by_title, maximum_page = _block_pages(blocks)
    stack: list[dict[str, Any]] = []
    output: list[dict[str, Any]] = []
    for order, (line_number, level, title) in enumerate(headings):
        while stack and int(stack[-1]["level"]) >= level:
            stack.pop()
        path = [*(item["title"] for item in stack), title]
        key_material = "\x1f".join((version_id, *path, str(order))).encode()
        section_key = hashlib.sha256(key_material).hexdigest()[:24]
        section_id = (
            f"section:{hashlib.sha256((version_id + ':' + section_key).encode()).hexdigest()[:32]}"
        )
        content_start = line_number if inferred else line_number + 1
        content_end = headings[order + 1][0] if order + 1 < len(headings) else len(lines)
        content = "\n".join(lines[content_start:content_end]).strip()
        start_page = page_by_title.get(_normalize(title))
        next_page = None
        if order + 1 < len(headings):
            next_page = page_by_title.get(_normalize(headings[order + 1][2]))
        end_page = (
            max(start_page, next_page - 1)
            if start_page is not None and next_page is not None
            else maximum_page
            if start_page is not None
            else None
        )
        section = {
            "section_id": section_id,
            "section_key": section_key,
            "parent_section_id": None if not stack else stack[-1]["section_id"],
            "level": level,
            "order": order,
            "title": title,
            "path": path,
            "start_page": start_page,
            "end_page": end_page,
            "content": content,
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            "confidence": 0.5 if inferred else 1.0,
            "inferred": inferred,
        }
        output.append(section)
        stack.append(section)
    return tuple(output)


def _outline_section_tree(
    *,
    version_id: str,
    outline: Iterable[dict[str, Any]],
    pages: Iterable[dict[str, Any]],
) -> tuple[dict[str, Any], ...]:
    page_text = _page_text(pages)
    entries = _usable_outline(outline, available_pages=set(page_text))
    if not entries or not any(text for text in page_text.values()):
        return ()

    maximum_page = max(page_text)
    stack: list[dict[str, Any]] = []
    output: list[dict[str, Any]] = []
    for order, entry in enumerate(entries):
        level = int(entry["level"])
        while stack and int(stack[-1]["level"]) >= level:
            stack.pop()
        level = min(level, len(stack) + 1)
        title = str(entry["title"])
        start_page = int(entry["page"])
        path = [*(item["title"] for item in stack), title]
        key_material = "\x1f".join((version_id, *path, str(order))).encode()
        section_key = hashlib.sha256(key_material).hexdigest()[:24]
        section_id = (
            f"section:{hashlib.sha256((version_id + ':' + section_key).encode()).hexdigest()[:32]}"
        )
        content = _outline_content(
            page_text=page_text,
            entries=entries,
            position=order,
            maximum_page=maximum_page,
        )
        section = {
            "section_id": section_id,
            "section_key": section_key,
            "parent_section_id": None if not stack else stack[-1]["section_id"],
            "level": level,
            "order": order,
            "title": title,
            "path": path,
            "start_page": start_page,
            "end_page": _outline_end_page(entries, order, maximum_page),
            "content": content,
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            "confidence": 1.0,
            "inferred": False,
        }
        output.append(section)
        stack.append(section)
    return tuple(output)


def _page_text(pages: Iterable[dict[str, Any]]) -> dict[int, str]:
    output: dict[int, str] = {}
    for item in pages:
        if not isinstance(item, dict):
            continue
        try:
            page_number = int(item.get("page_number"))
        except (TypeError, ValueError):
            continue
        if page_number > 0:
            output[page_number] = str(item.get("text") or "").strip()
    return output


def _usable_outline(
    outline: Iterable[dict[str, Any]], *, available_pages: set[int]
) -> tuple[dict[str, Any], ...]:
    entries: list[dict[str, Any]] = []
    for item in outline:
        if not isinstance(item, dict):
            continue
        try:
            level = int(item.get("level"))
            page = int(item.get("page"))
        except (TypeError, ValueError):
            continue
        title = _clean_title(item.get("title"))
        if level > 0 and page in available_pages and title:
            entries.append({"level": level, "title": title, "page": page})
    if len(entries) < 2 or any(
        current["page"] > following["page"] for current, following in zip(entries, entries[1:])
    ):
        return ()

    meaningful = [entry for entry in entries if any(char.isalpha() for char in entry["title"])]
    if len(meaningful) < 2 or len(meaningful) / len(entries) < 0.5:
        return ()

    minimum_level = min(int(entry["level"]) for entry in meaningful)
    if (
        len(meaningful) > 1
        and int(meaningful[0]["level"]) == minimum_level
        and sum(int(entry["level"]) == minimum_level for entry in meaningful) == 1
    ):
        meaningful = meaningful[1:]
        minimum_level += 1
    if not meaningful:
        return ()

    return tuple(
        {
            **entry,
            "level": max(1, int(entry["level"]) - minimum_level + 1),
        }
        for entry in meaningful
    )


def _outline_content(
    *,
    page_text: dict[int, str],
    entries: tuple[dict[str, Any], ...],
    position: int,
    maximum_page: int,
) -> str:
    current = entries[position]
    start_page = int(current["page"])
    following = entries[position + 1] if position + 1 < len(entries) else None
    next_page = int(following["page"]) if following is not None else None
    end_page = maximum_page if next_page is None else next_page - 1
    start_text = page_text.get(start_page, "")
    start_offset = _title_offset(start_text, str(current["title"]))
    if next_page == start_page and following is not None:
        end_offset = _title_offset(start_text, str(following["title"]), after=start_offset + 1)
        return start_text[start_offset:end_offset].strip()

    chunks = [start_text[start_offset:].strip()]
    chunks.extend(
        text for page in range(start_page + 1, end_page + 1) if (text := page_text.get(page, ""))
    )
    return "\n\n".join(chunk for chunk in chunks if chunk).strip()


def _title_offset(text: str, title: str, *, after: int = 0) -> int:
    compact_title = "".join(char for char in title if not char.isspace())
    compact_text: list[str] = []
    positions: list[int] = []
    for position, char in enumerate(text):
        if not char.isspace():
            compact_text.append(char)
            positions.append(position)
    compact_after = next(
        (position for position, original in enumerate(positions) if original >= after),
        len(positions),
    )
    found = "".join(compact_text).find(compact_title, compact_after)
    return positions[found] if found >= 0 else (len(text) if after else 0)


def _clean_title(value: object) -> str:
    clean = "".join(
        char for char in str(value or "") if not unicodedata.category(char).startswith("C")
    )
    return " ".join(clean.split())


def _outline_end_page(entries: tuple[dict[str, Any], ...], position: int, maximum_page: int) -> int:
    current = entries[position]
    start_page = int(current["page"])
    level = int(current["level"])
    for following in entries[position + 1 :]:
        if int(following["level"]) <= level:
            return max(start_page, int(following["page"]) - 1)
    return maximum_page


def section_documents(
    *,
    textbook_id: str,
    version_id: str,
    source_ref: str,
    sections: Iterable[dict[str, Any]],
    chunk_size: int = 1800,
    overlap: int = 200,
) -> tuple[dict[str, Any], ...]:
    """Chunk only inside section boundaries and preserve complete provenance."""
    if chunk_size < 200 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("invalid chapter-aware chunk policy")
    documents: list[dict[str, Any]] = []
    for section in sections:
        text = str(section.get("content") or "").strip()
        if not text:
            continue
        offset = 0
        chunk_order = 0
        while offset < len(text):
            end = min(len(text), offset + chunk_size)
            if end < len(text):
                boundary = text.rfind("\n", offset + chunk_size // 2, end)
                if boundary > offset:
                    end = boundary
            chunk = text[offset:end].strip()
            if chunk:
                metadata = {
                    "textbook_id": textbook_id,
                    "textbook_version_id": version_id,
                    "section_id": section["section_id"],
                    "section_key": section["section_key"],
                    "section_path": " / ".join(section["path"]),
                    "start_page": section.get("start_page"),
                    "end_page": section.get("end_page"),
                    "chunk_order": chunk_order,
                    "source_ref": source_ref,
                }
                documents.append({"text": chunk, "metadata": metadata})
                chunk_order += 1
            if end >= len(text):
                break
            offset = max(offset + 1, end - overlap)
    return tuple(documents)


def _block_pages(blocks: Iterable[dict[str, Any]]) -> tuple[dict[str, int], int | None]:
    mapping: dict[str, int] = {}
    maximum: int | None = None
    for block in blocks:
        if not isinstance(block, dict):
            continue
        raw_page = block.get("page_idx", block.get("page", block.get("page_number")))
        try:
            page = int(raw_page) + (1 if "page_idx" in block else 0)
        except (TypeError, ValueError):
            continue
        maximum = page if maximum is None else max(maximum, page)
        text = str(block.get("text") or block.get("content") or "").strip()
        if text:
            mapping.setdefault(_normalize(text.lstrip("# ")), page)
    return mapping, maximum


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())
