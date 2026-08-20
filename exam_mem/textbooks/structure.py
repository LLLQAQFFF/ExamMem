"""Deterministic textbook section recovery and chapter-aware chunking."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def build_section_tree(
    *, version_id: str, markdown: str, blocks: Iterable[dict[str, Any]] = ()
) -> tuple[dict[str, Any], ...]:
    """Recover a stable hierarchy without treating fixed-size chunks as chapters."""
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
