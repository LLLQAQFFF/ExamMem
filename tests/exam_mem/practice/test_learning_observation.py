from __future__ import annotations

import json

import pytest

from exam_mem.practice.learning_observation import (
    KnowledgePointOption,
    LearningObservationAgent,
)

pytestmark = pytest.mark.asyncio


POINTS = (KnowledgePointOption(id="kp-limit", name="函数极限", module_name="高等数学"),)


async def test_chat_agent_keeps_small_talk_outside_learning_records() -> None:
    calls: list[dict[str, object]] = []

    async def completion(**kwargs: object) -> str:
        calls.append(kwargs)
        return json.dumps(
            {
                "related_to_study": False,
                "knowledge_point_ids": [],
                "summary": "",
                "rationale": "只是闲聊",
                "confidence": 0.98,
            },
            ensure_ascii=False,
        )

    draft = await LearningObservationAgent(completion).analyze(
        channel="chat",
        transcript=({"id": "turn-1", "role": "user", "content": "今天天气不错"},),
        knowledge_points=POINTS,
        language="zh",
    )

    assert draft.related_to_study is False
    assert draft.knowledge_point_ids == []
    assert "普通聊天可能包含闲聊" in str(calls[0]["system_prompt"])
    assert "简体中文" in str(calls[0]["system_prompt"])


async def test_learning_path_agent_cannot_change_its_bound_knowledge_point() -> None:
    async def completion(**kwargs: object) -> str:
        prompt = json.loads(str(kwargs["prompt"]))
        assert prompt["fixed_knowledge_point_id"] == "kp-limit"
        return json.dumps(
            {
                "related_to_study": True,
                "knowledge_point_ids": ["kp-other"],
                "summary": "学习了别的内容",
                "rationale": "模型越界",
                "confidence": 0.8,
            },
            ensure_ascii=False,
        )

    with pytest.raises(ValueError, match="unknown knowledge points"):
        await LearningObservationAgent(completion).analyze(
            channel="learning_path",
            transcript=({"id": "turn-1", "role": "user", "content": "学习函数极限"},),
            knowledge_points=POINTS,
            language="zh",
            fixed_knowledge_point_id="kp-limit",
        )


async def test_learning_path_agent_records_exposure_without_mastery_authority() -> None:
    calls: list[dict[str, object]] = []

    async def completion(**kwargs: object) -> str:
        calls.append(kwargs)
        return json.dumps(
            {
                "related_to_study": True,
                "knowledge_point_ids": ["kp-limit"],
                "summary": "复习了函数极限的定义。",
                "rationale": "对话中出现了定义和例题。",
                "confidence": 0.91,
            },
            ensure_ascii=False,
        )

    draft = await LearningObservationAgent(completion).analyze(
        channel="learning_path",
        transcript=({"id": "turn-1", "role": "user", "content": "请解释函数极限"},),
        knowledge_points=POINTS,
        language="zh",
        fixed_knowledge_point_id="kp-limit",
    )

    assert draft.knowledge_point_ids == ["kp-limit"]
    assert "不得声称已经掌握" in str(calls[0]["system_prompt"])
    assert "无权更新掌握度" in str(calls[0]["system_prompt"])
