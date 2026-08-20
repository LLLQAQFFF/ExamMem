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
