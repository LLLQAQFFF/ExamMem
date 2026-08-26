from __future__ import annotations

import pytest

from deeptutor_plugins.exam_mem.textbook_study_plan import build_textbook_study_plan

SECTIONS = (
    {
        "section_id": "chapter-1",
        "parent_section_id": None,
        "order": 0,
        "title": "第一章 基础",
        "path": ["第一章 基础"],
    },
    {
        "section_id": "section-1-1",
        "parent_section_id": "chapter-1",
        "order": 1,
        "title": "基本概念",
        "path": ["第一章 基础", "基本概念"],
    },
    {
        "section_id": "section-1-2",
        "parent_section_id": "chapter-1",
        "order": 2,
        "title": "基本方法",
        "path": ["第一章 基础", "基本方法"],
    },
    {
        "section_id": "chapter-2",
        "parent_section_id": None,
        "order": 3,
        "title": "第二章 应用",
        "path": ["第二章 应用"],
    },
)


def test_whole_textbook_becomes_modules_and_independent_objectives() -> None:
    result = build_textbook_study_plan(
        plan_id="plan-1",
        plan_name="人工智能简史学习计划",
        textbook_title="人工智能简史",
        sections=SECTIONS,
        scope_section_id=None,
    )

    subject = result.tree.subjects[0]
    assert subject.name == "人工智能简史"
    assert [module.name for module in subject.modules] == ["第一章 基础", "第二章 应用"]
    assert [
        objective.name for module in subject.modules for objective in module.knowledge_points
    ] == ["基本概念", "基本方法", "第二章 应用"]
    assert result.tree.subjects[0].id != "chapter-1"
    assert result.scope_section_ids == (
        "chapter-1",
        "section-1-1",
        "section-1-2",
        "chapter-2",
    )
    first_objective = subject.modules[0].knowledge_points[0]
    assert [
        candidate.textbook_section_id
        for candidate in result.candidates
        if candidate.objective_id == first_objective.id
    ] == ["chapter-1", "section-1-1"]


def test_selected_chapter_limits_the_plan_and_candidate_scope() -> None:
    result = build_textbook_study_plan(
        plan_id="plan-2",
        plan_name="第一章学习计划",
        textbook_title="人工智能简史",
        sections=SECTIONS,
        scope_section_id="chapter-1",
    )

    assert [module.name for module in result.tree.subjects[0].modules] == ["第一章 基础"]
    assert result.scope_section_ids == ("chapter-1", "section-1-1", "section-1-2")
    assert {candidate.textbook_section_id for candidate in result.candidates} == {
        "chapter-1",
        "section-1-1",
        "section-1-2",
    }


def test_selected_leaf_becomes_one_objective() -> None:
    result = build_textbook_study_plan(
        plan_id="plan-3",
        plan_name="基本概念学习计划",
        textbook_title="人工智能简史",
        sections=SECTIONS,
        scope_section_id="section-1-1",
    )

    module = result.tree.subjects[0].modules[0]
    assert module.name == "基本概念"
    assert [item.name for item in module.knowledge_points] == ["基本概念"]
    assert [item.textbook_section_id for item in result.candidates] == ["section-1-1"]


def test_unknown_scope_is_rejected() -> None:
    with pytest.raises(ValueError, match="not in this textbook version"):
        build_textbook_study_plan(
            plan_id="plan-4",
            plan_name="无效计划",
            textbook_title="人工智能简史",
            sections=SECTIONS,
            scope_section_id="other-book-section",
        )


def test_front_and_back_matter_are_not_projected_as_learning_objectives() -> None:
    sections = (
        {
            "section_id": "author",
            "parent_section_id": None,
            "order": 0,
            "title": "作者简介",
            "path": ["作者简介"],
        },
        {
            "section_id": "preface",
            "parent_section_id": None,
            "order": 1,
            "title": "前言",
            "path": ["前言"],
        },
        {
            "section_id": "toc",
            "parent_section_id": None,
            "order": 2,
            "title": "目录",
            "path": ["目录"],
        },
        {
            "section_id": "chapter",
            "parent_section_id": None,
            "order": 3,
            "title": "第一章 基础",
            "path": ["第一章 基础"],
        },
        {
            "section_id": "references",
            "parent_section_id": None,
            "order": 4,
            "title": "参考文献指南",
            "path": ["参考文献指南"],
        },
    )

    result = build_textbook_study_plan(
        plan_id="plan-front-matter",
        plan_name="教材学习计划",
        textbook_title="教材",
        sections=sections,
        scope_section_id=None,
    )

    assert [module.name for module in result.tree.subjects[0].modules] == ["第一章 基础"]
    assert [
        objective.name
        for module in result.tree.subjects[0].modules
        for objective in module.knowledge_points
    ] == ["第一章 基础"]
    assert {candidate.textbook_section_id for candidate in result.candidates} == {"chapter"}
