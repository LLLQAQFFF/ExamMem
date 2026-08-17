from __future__ import annotations

from pathlib import Path

import pytest

from evaluation.contracts.case import EvaluationCase
from evaluation.protocols.validation import load_cases
from exam_mem.domain import KnowledgePointStatus, load_taxonomy, validate_slot_key

DATASET_DIR = Path(__file__).resolve().parents[2] / "evaluation" / "datasets" / "protocol_check"


def _knowledge_point_ids(case: EvaluationCase) -> set[str]:
    knowledge_point_ids = {
        knowledge_point_id
        for event in case.events
        for knowledge_point_id in event.knowledge_point_ids
    }
    for operation in case.gold_operations:
        knowledge_point_ids.update(operation.extracted_fields.knowledge_point_ids)
        knowledge_point_ids.update(operation.canonical_knowledge_point_ids)
    for action in case.gold_actions:
        knowledge_point_ids.update(action.knowledge_point_ids)
    return knowledge_point_ids


@pytest.mark.protocol
@pytest.mark.scope
def test_protocol_check_uses_the_stage_four_mvp_identity() -> None:
    for case in load_cases("protocol_check"):
        scopes = [memory.scope for memory in case.initial_memory]
        contexts = [event.context for event in case.events]
        query_scopes = [query.scope for query in case.queries]

        assert all(
            item.exam_id == "postgraduate_entrance_exam" and item.subject_id == "math_1"
            for item in (*scopes, *contexts, *query_scopes)
        ), case.case_id
        assert case.metadata.gold_revision == 2


@pytest.mark.protocol
@pytest.mark.taxonomy
def test_protocol_check_references_only_active_taxonomy_leaves() -> None:
    taxonomy = load_taxonomy("math1_v1")

    for case in load_cases("protocol_check"):
        for knowledge_point_id in _knowledge_point_ids(case):
            node = taxonomy.get(knowledge_point_id)
            assert node is not None, (case.case_id, knowledge_point_id)
            assert node.status is KnowledgePointStatus.ACTIVE
            assert taxonomy.children_of(node.id) == ()


@pytest.mark.protocol
@pytest.mark.scope
@pytest.mark.slot_key
def test_protocol_check_slots_follow_the_stage_four_grammar() -> None:
    taxonomy = load_taxonomy("math1_v1")

    def assert_taxonomy_slot(slot_key: str) -> None:
        namespace, _, remainder = slot_key.partition(":")
        if namespace not in {"mastery", "error_pattern"}:
            return
        knowledge_point_id = remainder.split(":", maxsplit=1)[0]
        node = taxonomy.get(knowledge_point_id)
        assert node is not None
        assert node.status is KnowledgePointStatus.ACTIVE
        assert taxonomy.children_of(node.id) == ()

    for case in load_cases("protocol_check"):
        for memory in case.initial_memory:
            slot_key = validate_slot_key(memory.slot_key)
            assert slot_key.partition(":")[0] == memory.scope.memory_namespace.value
            assert_taxonomy_slot(slot_key)

        for operation in case.gold_operations:
            slot_key = validate_slot_key(operation.slot_key)
            namespace, _, remainder = slot_key.partition(":")
            if namespace in {"mastery", "error_pattern"}:
                knowledge_point_id = remainder.split(":", maxsplit=1)[0]
                assert knowledge_point_id in operation.canonical_knowledge_point_ids
                assert_taxonomy_slot(slot_key)
            elif namespace == "plan":
                assert slot_key == "plan:postgraduate_entrance_exam:math_1"


@pytest.mark.protocol
@pytest.mark.scope
def test_similar_independence_terms_remain_cross_user_isolated() -> None:
    case = next(
        case
        for case in load_cases("protocol_check")
        if case.case_id == "similar_independence_terms_remain_subject_isolated_002"
    )

    probability_memory, linear_algebra_memory = case.initial_memory
    operation = case.gold_operations[0]

    assert probability_memory.scope.user_id == "user_002"
    assert linear_algebra_memory.scope.user_id == "user_001"
    assert case.events[0].context.user_id == "user_001"
    assert operation.candidate_memory_ids == [linear_algebra_memory.memory_id]
    assert probability_memory.memory_id not in operation.candidate_memory_ids


@pytest.mark.protocol
def test_protocol_check_contains_no_legacy_identity_strings() -> None:
    payload = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(DATASET_DIR.glob("*.json"))
    )

    assert '"exam_id": "postgraduate_math_1"' not in payload
    assert '"subject_id": "linear_algebra"' not in payload
    assert '"subject_id": "probability_theory"' not in payload
    assert '"linear_algebra.' not in payload
    assert '"probability.' not in payload
    assert "mastery:linear_algebra:" not in payload
    assert "mastery:probability:" not in payload
    assert "error_pattern:linear_algebra:" not in payload
    assert "error_pattern:probability:" not in payload
    assert "plan:probability:" not in payload
