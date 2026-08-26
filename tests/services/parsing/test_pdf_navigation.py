from __future__ import annotations

from pathlib import Path

import pymupdf

from deeptutor.services.parsing.pdf_navigation import extract_pdf_navigation


def test_extract_pdf_navigation_returns_outline_and_page_text(tmp_path: Path) -> None:
    source = tmp_path / "book.pdf"
    document = pymupdf.open()
    first = document.new_page()
    first.insert_text((72, 72), "Book title")
    second = document.new_page()
    second.insert_text((72, 72), "Chapter one")
    document.set_toc([[1, "Book title", 1], [2, "Chapter one", 2]])
    document.save(source)
    document.close()

    navigation = extract_pdf_navigation(source)

    assert navigation["outline"] == (
        {"level": 1, "title": "Book title", "page": 1},
        {"level": 2, "title": "Chapter one", "page": 2},
    )
    assert navigation["pages"][0]["page_number"] == 1
    assert "Book title" in navigation["pages"][0]["text"]
    assert navigation["pages"][1]["page_number"] == 2
    assert "Chapter one" in navigation["pages"][1]["text"]


def test_extract_pdf_navigation_ignores_non_pdf(tmp_path: Path) -> None:
    source = tmp_path / "book.md"
    source.write_text("# Chapter", encoding="utf-8")

    assert extract_pdf_navigation(source) == {"outline": (), "pages": ()}
