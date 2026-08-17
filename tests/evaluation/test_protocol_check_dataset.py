from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any

import pytest

from evaluation.contracts.case import EvaluationCase

DATASET_DIR = Path(__file__).resolve().parents[2] / "evaluation" / "datasets" / "protocol_check"


def _load_case(filename: str) -> EvaluationCase:
    payload: dict[str, Any] = json.loads((DATASET_DIR / filename).read_text(encoding="utf-8"))
    return EvaluationCase.model_validate(payload)


@pytest.mark.protocol
@pytest.mark.schema
def test_independent_semantic_duplicate_merges_into_new_version() -> None:
    case = _load_case("semantic_duplicate_001.json")
    operation = case.gold_operations[0]
    state = case.gold_states[0]

    assert operation.operation == "MERGE"
    assert operation.result_memory_id == "error_dimension_v2"
    assert state.active_memory_ids == ["error_dimension_v2"]
    assert state.archived_memory_ids == ["error_dimension_v1"]
    assert case.events[0].event_id not in case.initial_memory[0].provenance


@pytest.mark.protocol
@pytest.mark.schema
def test_idempotent_replay_is_no_op_without_new_version() -> None:
    case = _load_case("semantic_duplicate_002.json")
    operation = case.gold_operations[0]
    state = case.gold_states[0]

    assert operation.operation == "NO_OP"
    assert operation.result_memory_id is None
    assert state.active_memory_ids == ["error_dimension_v1"]
    assert state.archived_memory_ids == []
    assert case.events[0].event_id in case.initial_memory[0].provenance


@pytest.mark.protocol
@pytest.mark.schema
def test_same_slot_complementary_detail_merges_into_successor() -> None:
    case = _load_case("complementary_evidence_001.json")
    operation = case.gold_operations[0]
    state = case.gold_states[0]

    assert operation.operation == "MERGE"
    assert operation.result_memory_id == "error_conditional_probability_v2"
    assert operation.extracted_fields.error_detail == case.events[0].error_detail
    assert state.active_memory_ids == ["error_conditional_probability_v2"]
    assert state.archived_memory_ids == ["error_conditional_probability_v1"]


@pytest.mark.protocol
@pytest.mark.schema
def test_different_error_slot_adds_independent_active_memory() -> None:
    case = _load_case("complementary_evidence_002.json")
    operation = case.gold_operations[0]
    state = case.gold_states[0]

    assert operation.operation == "ADD"
    assert operation.candidate_memory_ids == []
    assert set(state.active_memory_ids) == {
        "error_eigen_concept_v1",
        "error_eigen_calculation_v1",
    }
    assert state.archived_memory_ids == []


@pytest.mark.protocol
@pytest.mark.schema
def test_single_correct_merges_evidence_without_mastery_level_change() -> None:
    case = _load_case("mastery_improvement_001.json")
    operation = case.gold_operations[0]
    state = case.gold_states[0]

    assert operation.operation == "MERGE"
    assert operation.result_memory_id == "mastery_matrix_rank_low_v2"
    assert state.active_memory_ids == ["mastery_matrix_rank_low_v2"]
    assert case.gold_actions[0].action_type == "recommend_review"


@pytest.mark.protocol
@pytest.mark.schema
def test_sustained_correct_supersedes_improving_with_high() -> None:
    case = _load_case("mastery_improvement_002.json")

    assert [operation.operation.value for operation in case.gold_operations] == [
        "MERGE",
        "MERGE",
        "SUPERSEDE",
    ]
    assert case.gold_operations[-1].evidence_event_ids == [
        "event_total_probability_201",
        "event_total_probability_202",
        "event_total_probability_203",
    ]
    assert case.gold_states[-1].active_memory_ids == ["mastery_total_probability_high_v4"]
    assert case.gold_actions[-1].action_type == "avoid_over_review"
    assert case.metadata.policy_parameters["minimum_consecutive_correct"] == 3
    assert case.metadata.policy_parameters["minimum_distinct_sessions"] == 2


@pytest.mark.protocol
@pytest.mark.schema
def test_single_concept_error_creates_contested_evidence_without_downgrade() -> None:
    case = _load_case("mastery_decline_001.json")
    operation = case.gold_operations[0]
    state = case.gold_states[0]

    assert operation.operation == "CONTESTED"
    assert state.active_memory_ids == ["mastery_eigenvector_high_v1"]
    assert state.contested_memory_ids == ["mastery_eigenvector_decline_contested_v2"]
    assert case.gold_actions[0].action_type == "no_action"


@pytest.mark.protocol
@pytest.mark.schema
def test_sustained_concept_errors_resolve_contest_with_decline() -> None:
    case = _load_case("mastery_decline_002.json")

    assert [operation.operation.value for operation in case.gold_operations] == [
        "CONTESTED",
        "MERGE",
        "SUPERSEDE",
    ]
    assert case.gold_states[-1].active_memory_ids == ["mastery_bayes_improving_v4"]
    assert case.gold_states[-1].contested_memory_ids == []
    assert case.gold_actions[-1].action_type == "recommend_review"
    assert case.metadata.policy_parameters["minimum_consecutive_incorrect"] == 3


@pytest.mark.protocol
@pytest.mark.schema
def test_isolated_careless_error_changes_no_l2_memory() -> None:
    case = _load_case("accidental_error_001.json")

    assert [operation.operation_id for operation in case.gold_operations] == [
        "step_001:mastery",
        "step_001:error_pattern",
    ]
    assert [operation.operation.value for operation in case.gold_operations] == [
        "NO_OP",
        "NO_OP",
    ]
    assert case.gold_states[0].active_memory_ids == ["mastery_determinant_high_v1"]
    assert case.gold_actions[0].action_type == "no_action"


@pytest.mark.protocol
@pytest.mark.schema
def test_repeated_careless_error_strengthens_pattern_not_mastery_decline() -> None:
    case = _load_case("accidental_error_002.json")
    operations = {operation.operation_id: operation for operation in case.gold_operations}

    assert operations["step_001:mastery"].operation == "NO_OP"
    assert operations["step_001:error_pattern"].operation == "MERGE"
    assert set(case.gold_states[0].active_memory_ids) == {
        "mastery_matrix_operation_high_v1",
        "error_matrix_sign_careless_v2",
    }
    assert case.gold_actions[0].action_type == "recommend_review"


@pytest.mark.protocol
@pytest.mark.schema
def test_repeated_errors_promote_contested_pattern_to_stable_weakness() -> None:
    case = _load_case("stable_weakness_001.json")
    operations_by_step = {
        step_id: [
            operation.operation.value
            for operation in case.gold_operations
            if operation.step_id == step_id
        ]
        for step_id in ("step_001", "step_002", "step_003")
    }

    assert operations_by_step == {
        "step_001": ["MERGE", "CONTESTED"],
        "step_002": ["MERGE", "MERGE"],
        "step_003": ["SUPERSEDE", "SUPERSEDE"],
    }
    assert set(case.gold_states[-1].active_memory_ids) == {
        "mastery_random_variable_low_v4",
        "error_random_variable_formula_stable_v3",
    }
    assert case.gold_states[-1].contested_memory_ids == []
    assert all(action.action_type == "recommend_review" for action in case.gold_actions)


@pytest.mark.protocol
@pytest.mark.schema
def test_existing_stable_weakness_merges_both_memory_dimensions() -> None:
    case = _load_case("stable_weakness_002.json")

    assert [operation.operation.value for operation in case.gold_operations] == [
        "MERGE",
        "MERGE",
    ]
    assert set(case.gold_states[0].active_memory_ids) == {
        "mastery_vector_space_low_v2",
        "error_vector_space_definition_v2",
    }
    assert case.gold_actions[0].action_type == "recommend_review"


@pytest.mark.protocol
@pytest.mark.schema
def test_authoritative_correction_invalidates_wholly_false_diagnosis() -> None:
    case = _load_case("explicit_correction_001.json")
    event = case.events[0]
    operation = case.gold_operations[0]

    assert event.event_type == "explicit_correction"
    assert event.correction is not None
    assert event.correction.source == "grader_audit"
    assert operation.operation == "INVALIDATE"
    assert case.gold_states[0].invalidated_memory_ids == ["error_bayes_formula_v1"]
    assert case.gold_actions[0].action_type == "no_action"


@pytest.mark.protocol
@pytest.mark.schema
def test_authoritative_correction_supersedes_overbroad_diagnosis() -> None:
    case = _load_case("explicit_correction_002.json")
    operation = case.gold_operations[0]

    assert operation.operation == "SUPERSEDE"
    assert operation.result_memory_id == "error_conditional_density_normalization_v2"
    assert case.gold_states[0].active_memory_ids == ["error_conditional_density_normalization_v2"]
    assert case.gold_states[0].archived_memory_ids == ["error_conditional_probability_broad_v1"]
    assert case.gold_actions[0].action_type == "recommend_review"


@pytest.mark.protocol
@pytest.mark.schema
def test_low_confidence_ambiguous_grade_changes_no_l2_memory() -> None:
    case = _load_case("low_confidence_exception_001.json")
    event = case.events[0]

    assert event.evidence_quality.confidence == 0.32
    assert event.evidence_quality.is_temporary_exception is False
    assert {reason.value for reason in event.evidence_quality.reasons} == {
        "low_grader_confidence",
        "ambiguous_response",
    }
    assert [operation.operation.value for operation in case.gold_operations] == [
        "NO_OP",
        "NO_OP",
    ]
    assert case.gold_states[0].active_memory_ids == ["mastery_matrix_rank_high_v1"]
    assert case.gold_actions[0].action_type == "no_action"
    assert case.metadata.policy_parameters["low_quality_events_count_toward_l2_thresholds"] is False


@pytest.mark.protocol
@pytest.mark.schema
def test_temporary_guided_success_does_not_upgrade_mastery() -> None:
    case = _load_case("low_confidence_exception_002.json")
    event = case.events[0]
    operation = case.gold_operations[0]

    assert event.answer_correct is True
    assert event.evidence_quality.confidence == 1.0
    assert event.evidence_quality.is_temporary_exception is True
    assert [reason.value for reason in event.evidence_quality.reasons] == [
        "user_reported_exception"
    ]
    assert operation.operation == "NO_OP"
    assert operation.result_memory_id is None
    assert case.gold_states[0].active_memory_ids == ["mastery_conditional_probability_low_v1"]
    assert case.gold_actions[0].action_type == "recommend_review"


@pytest.mark.protocol
@pytest.mark.schema
def test_completed_plan_is_invalidated_and_stops_driving_review() -> None:
    case = _load_case("plan_transition_001.json")
    event = case.events[0]
    operation = case.gold_operations[0]

    assert event.event_type == "plan_transition"
    assert event.plan_transition is not None
    assert event.plan_transition.source == "practice_progress"
    assert event.plan_transition.to_status == "completed"
    assert operation.operation == "INVALIDATE"
    assert operation.result_memory_id is None
    assert case.gold_states[0].invalidated_memory_ids == ["plan_conditional_probability_review_v1"]
    assert case.gold_actions[0].action_type == "no_action"


@pytest.mark.protocol
@pytest.mark.schema
def test_ambiguous_plan_cancellation_creates_contested_branch() -> None:
    case = _load_case("plan_transition_002.json")
    event = case.events[0]
    operation = case.gold_operations[0]

    assert event.plan_transition is not None
    assert event.plan_transition.source == "user"
    assert event.plan_transition.to_status == "cancelled"
    assert event.evidence_quality.confidence == 0.45
    assert operation.operation == "CONTESTED"
    assert case.gold_states[0].active_memory_ids == ["plan_probability_review_v1"]
    assert case.gold_states[0].contested_memory_ids == [
        "plan_probability_review_cancel_contested_v2"
    ]
    assert case.gold_actions[0].action_type == "recommend_review"


@pytest.mark.protocol
@pytest.mark.schema
def test_seventh_distinct_error_detail_is_preserved_without_storage_cap() -> None:
    case = _load_case("multi_value_error_pattern_001.json")
    operation = case.gold_operations[0]
    initial_value = case.initial_memory[0].value
    expected_value = operation.expected_result_value

    assert initial_value.type == "error_pattern"
    assert len(initial_value.details) == 6
    assert operation.operation == "MERGE"
    assert expected_value is not None
    assert expected_value.type == "error_pattern"
    assert len(expected_value.details) == 7
    assert case.events[0].error_detail in expected_value.details
    assert case.metadata.policy_parameters["stored_error_detail_limit"] is None


@pytest.mark.protocol
@pytest.mark.schema
def test_semantic_duplicate_detail_adds_evidence_without_duplicate_value() -> None:
    case = _load_case("multi_value_error_pattern_002.json")
    operation = case.gold_operations[0]
    expected_value = operation.expected_result_value

    assert operation.operation == "MERGE"
    assert expected_value is not None
    assert expected_value.type == "error_pattern"
    assert expected_value.details == ["计算全概率时遗漏一个互斥事件分支"]
    assert case.events[0].error_detail not in expected_value.details
    assert case.events[0].event_id not in case.initial_memory[0].provenance
    assert operation.evidence_event_ids == [case.events[0].event_id]


@pytest.mark.protocol
@pytest.mark.schema
def test_same_slot_across_users_updates_only_event_owner() -> None:
    case = _load_case("cross_scope_interference_001.json")
    event = case.events[0]
    operation = case.gold_operations[0]

    assert event.context.user_id == "user_002"
    assert operation.candidate_memory_ids == ["error_user_002_eigenvalue_concept_v1"]
    assert operation.target_memory_ids == ["error_user_002_eigenvalue_concept_v1"]
    assert "error_user_001_eigenvalue_concept_v1" not in (operation.candidate_memory_ids)
    assert set(case.gold_states[0].active_memory_ids) == {
        "error_user_001_eigenvalue_concept_v1",
        "error_user_002_eigenvalue_concept_v2",
    }
    assert case.metadata.policy_parameters["cross_user_candidate_count"] == 0


@pytest.mark.protocol
@pytest.mark.schema
def test_similar_terms_across_users_never_share_candidate_pool() -> None:
    case = _load_case("cross_scope_interference_002.json")
    event = case.events[0]
    operation = case.gold_operations[0]
    probability_memory, linear_algebra_memory = case.initial_memory

    assert event.context.user_id == "user_001"
    assert probability_memory.scope.user_id == "user_002"
    assert linear_algebra_memory.scope.user_id == "user_001"
    assert event.context.subject_id == "math_1"
    assert probability_memory.scope.subject_id == "math_1"
    assert operation.candidate_memory_ids == ["error_linear_independence_v1"]
    assert "error_probability_independence_v1" not in (operation.candidate_memory_ids)
    assert set(case.gold_states[0].active_memory_ids) == {
        "error_probability_independence_v1",
        "error_linear_independence_v2",
    }
    assert case.metadata.policy_parameters["exact_scope_filter_before_similarity"] is True
    assert case.metadata.policy_parameters["cross_user_candidate_count"] == 0


@pytest.mark.protocol
@pytest.mark.schema
def test_old_low_state_is_superseded_by_recent_cross_session_success() -> None:
    case = _load_case("long_range_change_001.json")

    assert [operation.operation.value for operation in case.gold_operations] == [
        "MERGE",
        "MERGE",
        "SUPERSEDE",
    ]
    final_operation = case.gold_operations[-1]
    assert final_operation.expected_result_value is not None
    assert final_operation.expected_result_value.type == "mastery"
    assert final_operation.expected_result_value.level == "high"
    assert case.gold_states[-1].active_memory_ids == ["mastery_quadratic_form_high_v4"]
    assert case.gold_actions[-1].action_type == "avoid_over_review"
    assert case.metadata.policy_parameters["long_gap_starts_new_version_chain"] is False


@pytest.mark.protocol
@pytest.mark.schema
def test_long_gap_error_does_not_reactivate_archived_low_state() -> None:
    case = _load_case("long_range_change_002.json")
    operation = case.gold_operations[0]
    state = case.gold_states[0]

    assert operation.candidate_memory_ids == ["mastery_bayes_high_v2"]
    assert operation.operation == "CONTESTED"
    assert state.active_memory_ids == ["mastery_bayes_high_v2"]
    assert state.archived_memory_ids == ["mastery_bayes_low_v1"]
    assert state.contested_memory_ids == ["mastery_bayes_decline_contested_v3"]
    assert case.gold_actions[0].action_type == "recommend_review"
    assert case.metadata.policy_parameters["time_alone_changes_mastery"] is False
    assert case.metadata.policy_parameters["archived_memory_can_reactivate"] is False


@pytest.mark.protocol
@pytest.mark.schema
def test_protocol_check_contains_exactly_two_cases_per_scenario() -> None:
    cases = [_load_case(path.name) for path in sorted(DATASET_DIR.glob("*.json"))]
    counts = Counter(case.scenario_type.value for case in cases)

    assert len(cases) == 24
    assert len(counts) == 12
    assert set(counts.values()) == {2}
