from exam_mem.textbooks import build_section_tree, section_documents


def test_section_tree_is_hierarchical_stable_and_page_aware() -> None:
    markdown = "# 第一篇\n导言\n## 第一章\n极限正文\n## 第二章\n导数正文"
    blocks = [
        {"text": "第一篇", "page_idx": 0},
        {"text": "第一章", "page_idx": 4},
        {"text": "第二章", "page_idx": 9},
    ]
    first = build_section_tree(version_id="version-1", markdown=markdown, blocks=blocks)
    second = build_section_tree(version_id="version-1", markdown=markdown, blocks=blocks)

    assert first == second
    assert first[1]["parent_section_id"] == first[0]["section_id"]
    assert first[1]["path"] == ["第一篇", "第一章"]
    assert (first[1]["start_page"], first[1]["end_page"]) == (5, 9)
    assert first[1]["inferred"] is False


def test_headingless_source_is_explicitly_inferred_and_chunks_stay_in_section() -> None:
    sections = build_section_tree(version_id="version-2", markdown="甲" * 600, blocks=[])
    assert len(sections) == 1
    assert sections[0]["inferred"] is True
    documents = section_documents(
        textbook_id="book-1",
        version_id="version-2",
        source_ref="source:" + "0" * 64,
        sections=sections,
        chunk_size=250,
        overlap=25,
    )
    assert len(documents) == 3
    assert {item["metadata"]["section_key"] for item in documents} == {sections[0]["section_key"]}
    assert [item["metadata"]["chunk_order"] for item in documents] == [0, 1, 2]


def test_pdf_outline_recovers_hierarchy_pages_and_page_content() -> None:
    outline = [
        {"level": 1, "title": "人工智能简史", "page": 1},
        {"level": 2, "title": "第1章 起源", "page": 14},
        {"level": 3, "title": "1. 背景", "page": 14},
        {"level": 3, "title": "2. 会议", "page": 19},
        {"level": 2, "title": "第2章 定理证明", "page": 37},
    ]
    pages = [
        {"page_number": page, "text": f"第 {page} 页正文"}
        for page in range(1, 41)
    ]
    pages[13]["text"] = "第1章 起源\n章导言\n1. 背景\n第 14 页正文"
    pages[18]["text"] = "2. 会议\n第 19 页正文"
    pages[36]["text"] = "第2章 定理证明\n第 37 页正文"

    sections = build_section_tree(
        version_id="version-outline",
        markdown="没有 Markdown 标题",
        outline=outline,
        pages=pages,
    )

    assert [item["title"] for item in sections] == [
        "第1章 起源",
        "1. 背景",
        "2. 会议",
        "第2章 定理证明",
    ]
    assert sections[0]["level"] == 1
    assert sections[1]["level"] == 2
    assert sections[1]["parent_section_id"] == sections[0]["section_id"]
    assert sections[1]["path"] == ["第1章 起源", "1. 背景"]
    assert (sections[0]["start_page"], sections[0]["end_page"]) == (14, 36)
    assert (sections[1]["start_page"], sections[1]["end_page"]) == (14, 18)
    assert "章导言" in sections[0]["content"]
    assert "1. 背景" not in sections[0]["content"]
    assert "第 14 页正文" in sections[1]["content"]
    assert "第 18 页正文" in sections[1]["content"]
    assert "第 19 页正文" not in sections[1]["content"]
    assert all(item["inferred"] is False for item in sections)


def test_numeric_page_bookmarks_are_not_treated_as_chapters() -> None:
    outline = [
        {"level": 1, "title": "目录", "page": 6},
        *(
            {"level": 1, "title": str(page), "page": page}
            for page in range(7, 40)
        ),
    ]
    pages = [
        {"page_number": page, "text": f"第 {page} 页"}
        for page in range(1, 40)
    ]

    sections = build_section_tree(
        version_id="version-numeric-outline",
        markdown="仍然没有 Markdown 标题",
        outline=outline,
        pages=pages,
    )

    assert len(sections) == 1
    assert sections[0]["title"] == "Full text"
    assert sections[0]["inferred"] is True
