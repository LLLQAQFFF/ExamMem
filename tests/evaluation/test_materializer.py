from __future__ import annotations

from evaluation.materializer import materialize_case
from evaluation.protocols.validation import load_cases
from exam_mem.contracts import MemoryNamespace


def test_every_dev_event_materializes_exactly_the_registered_slots() -> None:
    cases = load_cases("dev")

    for case in cases:
        steps = materialize_case(case)
        assert len(steps) == len(case.events)
        for step in steps:
            assert [candidate.slot_key for candidate in step.candidates] == [
                operation.slot_key for operation in step.gold_operations
            ]
            assert all(candidate.event_id == step.event.event_id for candidate in step.candidates)
            assert all(
                candidate.scope.model_dump(exclude={"memory_namespace"})
                == step.event.context.model_dump()
                for candidate in step.candidates
            )


def test_materializer_derives_values_from_events_not_gold_results() -> None:
    case = next(
        case for case in load_cases("dev") if case.scenario_type.value == "mastery_improvement"
    )

    step = materialize_case(case)[0]
    mastery = next(
        candidate
        for candidate in step.candidates
        if candidate.scope.memory_namespace is MemoryNamespace.MASTERY
    )

    assert mastery.proposed_value.score == (1.0 if step.event.answer_correct else 0.0)
    assert mastery.evidence["evaluation_upstream"] == "gold_normalized_slot"
