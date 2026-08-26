"""Neutral PDF navigation signals for structure-aware consumers."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def extract_pdf_navigation(source_path: str | Path) -> dict[str, tuple[dict[str, Any], ...]]:
    """Return PDF bookmarks and per-page text without exposing PyMuPDF objects."""
    path = Path(source_path)
    if path.suffix.lower() != ".pdf":
        return {"outline": (), "pages": ()}

    import pymupdf

    try:
        with pymupdf.open(path) as document:
            page_count = len(document)
            outline = tuple(
                {
                    "level": int(level),
                    "title": _clean_title(title),
                    "page": int(page),
                }
                for level, title, page in document.get_toc(simple=True)
                if int(level) > 0
                and 1 <= int(page) <= page_count
                and _clean_title(title)
            )
            pages = tuple(
                {
                    "page_number": page_number,
                    "text": page.get_text("text", sort=True).strip(),
                }
                for page_number, page in enumerate(document, start=1)
            )
    except (OSError, RuntimeError, ValueError) as exc:
        logger.warning("Unable to extract PDF navigation from %s: %s", path.name, exc)
        return {"outline": (), "pages": ()}
    return {"outline": outline, "pages": pages}


def _clean_title(value: object) -> str:
    return " ".join(str(value).replace("\x00", "").split())


__all__ = ["extract_pdf_navigation"]
