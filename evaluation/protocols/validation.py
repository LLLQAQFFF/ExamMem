"""Load and validate the frozen ExamMem evaluation protocol artifacts."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any

from evaluation.contracts.case import DatasetSplit, EvaluationCase
from evaluation.contracts.protocol import ProtocolConfig
from exam_mem.contracts import LifecycleOperation, LifecycleState

EVALUATION_ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_DIR = EVALUATION_ROOT / "protocols"
DATASET_ROOT = EVALUATION_ROOT / "datasets"
REVIEW_ROOT = EVALUATION_ROOT / "reviews"


class ArtifactValidationError(ValueError):
    """Raised when versioned evaluation artifacts are incomplete or inconsistent."""


class GoldReplayError(ArtifactValidationError):
    """Raised when declared Gold operations do not reproduce a Gold state."""


def load_protocol(version: str) -> ProtocolConfig:
    path = PROTOCOL_DIR / f"{version}.json"
    if not path.is_file():
        raise ArtifactValidationError(f"protocol file does not exist: {path}")
    return ProtocolConfig.model_validate_json(path.read_text(encoding="utf-8"))


def load_cases(split: DatasetSplit | str) -> list[EvaluationCase]:
    split_value = DatasetSplit(split)
    dataset_dir = DATASET_ROOT / split_value.value
    if not dataset_dir.is_dir():
        raise ArtifactValidationError(f"dataset split directory does not exist: {dataset_dir}")

    paths = sorted(dataset_dir.glob("*.json"))
    if not paths:
        raise ArtifactValidationError(f"dataset split is empty: {split_value.value}")

    cases = [EvaluationCase.model_validate_json(path.read_text(encoding="utf-8")) for path in paths]
    wrong_split = [case.case_id for case in cases if case.metadata.split is not split_value]
    if wrong_split:
        raise ArtifactValidationError(
            f"cases declare the wrong split {split_value.value}: {wrong_split}"
        )
    case_ids = [case.case_id for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ArtifactValidationError("case_id values must be unique within a split")
    return cases


def _validate_review_records(cases: list[EvaluationCase]) -> dict[str, int]:
    review_dir = REVIEW_ROOT / DatasetSplit.PROTOCOL_CHECK.value
    paths = sorted(review_dir.glob("*.json"))
    reviews: dict[str, dict[str, Any]] = {}
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        scenario_type = payload.get("scenario_type")
        if not isinstance(scenario_type, str):
            raise ArtifactValidationError(f"review is missing scenario_type: {path.name}")
        if scenario_type in reviews:
            raise ArtifactValidationError(f"duplicate review for scenario_type: {scenario_type}")
        reviews[scenario_type] = payload

    cases_by_scenario: dict[str, set[str]] = {}
    for case in cases:
        cases_by_scenario.setdefault(case.scenario_type.value, set()).add(case.case_id)
    if set(reviews) != set(cases_by_scenario):
        raise ArtifactValidationError(
            "review scenario types must exactly match protocol_check cases"
        )

    pending_reviews = 0
    completed_independent_reviews = 0
    agreed_case_count = 0
    for scenario_type, case_ids in cases_by_scenario.items():
        review = reviews[scenario_type]
        reviewed_case_ids = review.get("case_ids")
        if not isinstance(reviewed_case_ids, list) or set(reviewed_case_ids) != case_ids:
            raise ArtifactValidationError(f"review case_ids do not match dataset: {scenario_type}")
        if review.get("primary_label_source") not in {
            "project_owner_interactive_annotation",
            "project_owner_multiple_choice_annotation",
        }:
            raise ArtifactValidationError(
                f"review has an unknown primary label source: {scenario_type}"
            )
        blind_review = review.get("blind_self_review")
        if not isinstance(blind_review, dict):
            raise ArtifactValidationError(f"review is missing blind_self_review: {scenario_type}")
        if blind_review.get("minimum_interval_days") != 1:
            raise ArtifactValidationError(f"review interval must remain one day: {scenario_type}")
        blind_review_completed = blind_review.get("completed") is True

        independent_review = review.get("independent_human_review")
        independent_review_completed = False
        if independent_review is not None:
            if not isinstance(independent_review, dict):
                raise ArtifactValidationError(
                    f"independent_human_review must be an object: {scenario_type}"
                )
            independent_case_ids = independent_review.get("reviewed_case_ids")
            if not isinstance(independent_case_ids, list) or set(independent_case_ids) != case_ids:
                raise ArtifactValidationError(
                    f"independent review case_ids do not match dataset: {scenario_type}"
                )
            answers = independent_review.get("answers")
            if not isinstance(answers, list) or len(answers) != len(case_ids):
                raise ArtifactValidationError(
                    f"independent review must record one answer per case: {scenario_type}"
                )
            if independent_review.get("asserted_no_access_to_primary_labels") is not True:
                raise ArtifactValidationError(
                    f"independent reviewer must assert label isolation: {scenario_type}"
                )
            conflicts = independent_review.get("conflicts")
            agreement = independent_review.get("agreement_with_primary")
            if not isinstance(conflicts, list) or not isinstance(agreement, bool):
                raise ArtifactValidationError(
                    f"independent review must record agreement and conflicts: {scenario_type}"
                )
            if agreement and conflicts:
                raise ArtifactValidationError(
                    f"full agreement cannot contain conflicts: {scenario_type}"
                )
            independent_review_completed = (
                independent_review.get("status") == "completed_without_revision"
            )
            if independent_review_completed:
                completed_independent_reviews += 1
                if agreement:
                    agreed_case_count += len(case_ids)

        if not blind_review_completed and not independent_review_completed:
            pending_reviews += 1

    return {
        "review_count": len(reviews),
        "pending_review_count": pending_reviews,
        "pending_blind_review_count": pending_reviews,
        "completed_independent_human_review_count": completed_independent_reviews,
        "independent_review_agreed_case_count": agreed_case_count,
    }


def validate_dataset(
    split: DatasetSplit | str,
    *,
    protocol_version: str,
) -> dict[str, Any]:
    split_value = DatasetSplit(split)
    protocol = load_protocol(protocol_version)
    cases = load_cases(split_value)
    split_rule = next(rule for rule in protocol.dataset_splits if rule.split is split_value)
    if len(cases) != split_rule.case_count:
        raise ArtifactValidationError(
            f"{split_value.value} requires {split_rule.case_count} cases, found {len(cases)}"
        )

    scenario_counts = Counter(case.scenario_type.value for case in cases)
    summary: dict[str, Any] = {
        "split": split_value.value,
        "case_count": len(cases),
        "scenario_counts": dict(sorted(scenario_counts.items())),
    }
    if split_value is DatasetSplit.PROTOCOL_CHECK:
        expected_counts = {
            quota.scenario_type.value: quota.protocol_check_count
            for quota in protocol.scenario_quotas
        }
        if scenario_counts != Counter(expected_counts):
            raise ArtifactValidationError(
                "protocol_check cases do not match frozen scenario quotas"
            )
        summary.update(_validate_review_records(cases))
    return summary


def _remove_from_all_states(states: dict[str, set[str]], memory_id: str) -> str:
    matches = [name for name, memory_ids in states.items() if memory_id in memory_ids]
    if len(matches) != 1:
        raise GoldReplayError(
            f"memory {memory_id} must exist in exactly one state, found {matches}"
        )
    states[matches[0]].remove(memory_id)
    return matches[0]


def _add_new_memory(states: dict[str, set[str]], memory_id: str | None, state: str) -> None:
    if memory_id is None:
        raise GoldReplayError(f"operation requires a result memory in state {state}")
    if any(memory_id in memory_ids for memory_ids in states.values()):
        raise GoldReplayError(f"result memory already exists: {memory_id}")
    states[state].add(memory_id)


def replay_case(case: EvaluationCase) -> int:
    states = {state.value: set() for state in LifecycleState}
    version_relations: set[tuple[str, str, str]] = set()
    for memory in case.initial_memory:
        states[memory.lifecycle_state.value].add(memory.memory_id)
        if memory.superseded_by is not None:
            version_relations.add((memory.memory_id, memory.superseded_by, "superseded_by"))

    operations_by_step: dict[str, list[Any]] = {}
    for operation in case.gold_operations:
        operations_by_step.setdefault(operation.step_id, []).append(operation)

    expected_by_step = {state.step_id: state for state in case.gold_states}
    for step_id, operations in operations_by_step.items():
        for operation in operations:
            if operation.operation is LifecycleOperation.NO_OP:
                continue
            if operation.operation is LifecycleOperation.ADD:
                _add_new_memory(states, operation.result_memory_id, LifecycleState.ACTIVE.value)
                continue
            if operation.operation is LifecycleOperation.CONTESTED:
                _add_new_memory(states, operation.result_memory_id, LifecycleState.CONTESTED.value)
                continue
            if operation.operation is LifecycleOperation.INVALIDATE:
                if not operation.target_memory_ids:
                    raise GoldReplayError("INVALIDATE requires at least one target")
                for memory_id in operation.target_memory_ids:
                    _remove_from_all_states(states, memory_id)
                    states[LifecycleState.INVALIDATED.value].add(memory_id)
                continue

            if not operation.target_memory_ids:
                raise GoldReplayError(f"{operation.operation.value} requires at least one target")
            target_states = [
                _remove_from_all_states(states, memory_id)
                for memory_id in operation.target_memory_ids
            ]
            for memory_id in operation.target_memory_ids:
                states[LifecycleState.ARCHIVED.value].add(memory_id)
                version_relations.add(
                    (memory_id, operation.result_memory_id or "", "superseded_by")
                )

            result_state = LifecycleState.ACTIVE.value
            if operation.operation is LifecycleOperation.MERGE and set(target_states) == {
                LifecycleState.CONTESTED.value
            }:
                result_state = LifecycleState.CONTESTED.value
            _add_new_memory(states, operation.result_memory_id, result_state)

        expected = expected_by_step[step_id]
        expected_states = {
            LifecycleState.ACTIVE.value: set(expected.active_memory_ids),
            LifecycleState.ARCHIVED.value: set(expected.archived_memory_ids),
            LifecycleState.INVALIDATED.value: set(expected.invalidated_memory_ids),
            LifecycleState.CONTESTED.value: set(expected.contested_memory_ids),
        }
        if states != expected_states:
            raise GoldReplayError(
                f"{case.case_id}/{step_id} state mismatch: "
                f"actual={states}, expected={expected_states}"
            )
        expected_relations = {
            (
                relation.predecessor_memory_id,
                relation.successor_memory_id,
                relation.relation.value,
            )
            for relation in expected.version_relations
        }
        if version_relations != expected_relations:
            raise GoldReplayError(
                f"{case.case_id}/{step_id} version relation mismatch: "
                f"actual={version_relations}, expected={expected_relations}"
            )
    return len(operations_by_step)


def replay_split(
    split: DatasetSplit | str,
    *,
    protocol_version: str,
) -> dict[str, Any]:
    split_value = DatasetSplit(split)
    validate_dataset(split_value, protocol_version=protocol_version)
    cases = load_cases(split_value)
    step_count = sum(replay_case(case) for case in cases)
    return {
        "split": split_value.value,
        "case_count": len(cases),
        "step_count": step_count,
    }


__all__ = [
    "ArtifactValidationError",
    "GoldReplayError",
    "load_cases",
    "load_protocol",
    "replay_case",
    "replay_split",
    "validate_dataset",
]
