from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from pydantic import ValidationError
import pytest

from exam_mem.contracts import LearningMemory, MemoryScope, MemoryUpdateCandidate
from exam_mem.lifecycle import (
    CandidateDisplayRangeError,
    DeepTutorRelationClassifierAdapter,
    LifecycleCandidateSnapshot,
    MemoryRelation,
    RelationClassificationError,
    RelationClassifierOutput,
)
from tests.exam_mem.lifecycle.fakes import FakeRelationClassifier

pytestmark = pytest.mark.lifecycle

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
SCOPE = MemoryScope(
    user_id="stage06_relation_user",
    exam_id="postgraduate_entrance_exam",
    subject_id="math_1",
    memory_namespace="error_pattern",
)
SLOT_KEY = "error_pattern:math1.probability.bayes:concept_confusion"


class RecordingCompletion:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    async def __call__(self, **kwargs: object) -> str:
        self.calls.append(kwargs)
        return self.response


class SequencedCompletion:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.calls = 0

    async def __call__(self, **kwargs: object) -> str:
        del kwargs
        response = self.responses[self.calls]
        self.calls += 1
        return response


def _candidate() -> MemoryUpdateCandidate:
    return MemoryUpdateCandidate.model_validate(
        {
            "event_id": "secret_event_primary_key",
            "scope": SCOPE.model_dump(mode="json"),
            "slot_key": SLOT_KEY,
            "proposed_value": {
                "type": "error_pattern",
                "error_type": "concept_confusion",
                "summary": "Confuses prior and posterior probability",
                "details": ["reverses the conditional direction"],
            },
            "evidence": {
                "memory_id": "must_not_leak_from_evidence",
                "row_version": 999,
            },
        }
    )


def _snapshot(
    *,
    memory_id: str,
    version: int,
    row_version: int,
    summary: str,
    scope: MemoryScope = SCOPE,
    slot_key: str = SLOT_KEY,
    state: str = "active",
) -> LifecycleCandidateSnapshot:
    memory = LearningMemory.model_validate(
        {
            "memory_id": memory_id,
            "scope": scope.model_dump(mode="json"),
            "slot_key": slot_key,
            "value": {
                "type": "error_pattern",
                "error_type": "concept_confusion",
                "summary": summary,
                "details": ["existing controlled detail"],
            },
            "confidence": 0.85,
            "evidence_count": 1,
            "lifecycle_state": state,
            "version": version,
            "valid_from": NOW - timedelta(days=version),
            "valid_to": None,
            "superseded_by": None,
            "provenance": [f"secret_provenance_{version}"],
        }
    )
    return LifecycleCandidateSnapshot(
        memory=memory,
        row_version=row_version,
        contested_group_id="secret_contested_group" if state == "contested" else None,
        policy_version="lifecycle_policy_v1",
    )


def _snapshots() -> tuple[LifecycleCandidateSnapshot, ...]:
    newer = _snapshot(
        memory_id="secret_memory_v2",
        version=2,
        row_version=22,
        summary="Newer contested description",
        state="contested",
    )
    older = _snapshot(
        memory_id="secret_memory_v1",
        version=1,
        row_version=11,
        summary="Older active description",
    )
    return newer, older


def _classification_payload(*, display_number: int = 1) -> dict[str, object]:
    return {
        "candidate_display_number": display_number,
        "relation": "complementary",
        "canonical_knowledge_point_id": "math1.probability.bayes",
        "error_type": "concept_confusion",
        "error_summary": "Adds a distinct controlled detail",
        "confidence": 0.88,
        "reason": "The new detail narrows the same error pattern.",
    }


@pytest.mark.asyncio
async def test_adapter_uses_strict_schema_and_safe_display_only_prompt() -> None:
    completion = RecordingCompletion(json.dumps(_classification_payload(display_number=2)))
    adapter = DeepTutorRelationClassifierAdapter(completion)

    resolved = await adapter.classify(_candidate(), _snapshots())

    assert resolved.target_memory_id == "secret_memory_v2"
    assert resolved.classification.relation is MemoryRelation.COMPLEMENTARY
    assert len(completion.calls) == 1
    call = completion.calls[0]
    assert call["temperature"] == 0.0

    response_format = call["response_format"]
    assert isinstance(response_format, dict)
    assert response_format["type"] == "json_schema"
    json_schema = response_format["json_schema"]
    assert isinstance(json_schema, dict)
    assert json_schema["strict"] is True
    schema = json_schema["schema"]
    assert isinstance(schema, dict)
    assert schema["additionalProperties"] is False

    prompt = call["prompt"]
    assert isinstance(prompt, str)
    payload = json.loads(prompt)
    assert payload["output_json_schema"]["additionalProperties"] is False
    assert [item["candidate_display_number"] for item in payload["existing_candidates"]] == [
        1,
        2,
    ]
    assert [item["value"]["summary"] for item in payload["existing_candidates"]] == [
        "Older active description",
        "Newer contested description",
    ]
    for forbidden in (
        "secret_memory_v1",
        "secret_memory_v2",
        "secret_event_primary_key",
        "must_not_leak_from_evidence",
        "secret_provenance",
        "secret_contested_group",
        "row_version",
        "user_id",
    ):
        assert forbidden not in prompt


@pytest.mark.asyncio
async def test_adapter_retries_one_invalid_strict_output_then_resolves() -> None:
    completion = SequencedCompletion(
        ["not-json", json.dumps(_classification_payload(display_number=1))]
    )

    resolved = await DeepTutorRelationClassifierAdapter(completion).classify(
        _candidate(),
        _snapshots(),
    )

    assert completion.calls == 2
    assert resolved.classification.relation is MemoryRelation.COMPLEMENTARY


@pytest.mark.asyncio
async def test_adapter_exhausts_bounded_retries_without_guessing_relation() -> None:
    completion = RecordingCompletion("not-json")

    with pytest.raises(RelationClassificationError) as captured:
        await DeepTutorRelationClassifierAdapter(completion).classify(
            _candidate(),
            _snapshots(),
        )

    assert captured.value.error_code == "relation_classifier_failed"
    assert len(completion.calls) == 2


@pytest.mark.asyncio
async def test_adapter_reuses_deeptutor_json_extraction_for_fenced_output() -> None:
    raw = "classification:\n```json\n" + json.dumps(_classification_payload()) + "\n```"
    adapter = DeepTutorRelationClassifierAdapter(RecordingCompletion(raw))

    resolved = await adapter.classify(_candidate(), _snapshots())

    assert resolved.target_memory_id == "secret_memory_v1"


@pytest.mark.asyncio
async def test_adapter_rejects_extra_fields_and_out_of_range_display_number() -> None:
    extra_field = _classification_payload()
    extra_field["memory_id"] = "llm_must_not_choose_this"
    adapter = DeepTutorRelationClassifierAdapter(RecordingCompletion(json.dumps(extra_field)))
    with pytest.raises(RelationClassificationError) as extra_captured:
        await adapter.classify(_candidate(), _snapshots())
    assert isinstance(extra_captured.value.__cause__, ValidationError)

    out_of_range = DeepTutorRelationClassifierAdapter(
        RecordingCompletion(json.dumps(_classification_payload(display_number=3)))
    )
    with pytest.raises(RelationClassificationError) as range_captured:
        await out_of_range.classify(_candidate(), _snapshots())
    assert isinstance(range_captured.value.__cause__, CandidateDisplayRangeError)


@pytest.mark.asyncio
async def test_adapter_rejects_empty_or_scope_unsafe_pool_before_calling_llm() -> None:
    completion = RecordingCompletion(json.dumps(_classification_payload()))
    adapter = DeepTutorRelationClassifierAdapter(completion)

    with pytest.raises(ValueError, match="at least one candidate snapshot"):
        await adapter.classify(_candidate(), ())
    assert completion.calls == []

    foreign_scope = MemoryScope(
        user_id="another_user",
        exam_id=SCOPE.exam_id,
        subject_id=SCOPE.subject_id,
        memory_namespace=SCOPE.memory_namespace,
    )
    unsafe = _snapshot(
        memory_id="foreign_memory",
        version=1,
        row_version=1,
        summary="Foreign memory",
        scope=foreign_scope,
    )
    with pytest.raises(ValueError, match="must match candidate scope"):
        await adapter.classify(_candidate(), (unsafe,))
    assert completion.calls == []


@pytest.mark.asyncio
async def test_adapter_rejects_slot_drift_and_duplicate_authoritative_ids() -> None:
    completion = RecordingCompletion(json.dumps(_classification_payload()))
    adapter = DeepTutorRelationClassifierAdapter(completion)
    wrong_slot = _snapshot(
        memory_id="wrong_slot_memory",
        version=1,
        row_version=1,
        summary="Wrong slot",
        slot_key="error_pattern:math1.probability.bayes:careless_error",
    )
    with pytest.raises(ValueError, match="must match candidate slot_key"):
        await adapter.classify(_candidate(), (wrong_slot,))

    duplicate = _snapshot(
        memory_id="same_memory",
        version=1,
        row_version=1,
        summary="Same memory",
    )
    with pytest.raises(ValueError, match="memory IDs must be unique"):
        await adapter.classify(_candidate(), (duplicate, duplicate))
    assert completion.calls == []


@pytest.mark.asyncio
async def test_fake_uses_the_same_strict_validation_and_resolution_boundary() -> None:
    candidate = _candidate()
    fake = FakeRelationClassifier({candidate.event_id: _classification_payload(display_number=2)})

    resolved = await fake.classify(candidate, _snapshots())

    assert resolved.target_memory_id == "secret_memory_v2"
    assert fake.calls == [candidate.event_id]


@pytest.mark.asyncio
async def test_fake_rejects_missing_or_invalid_controlled_output() -> None:
    candidate = _candidate()
    missing = FakeRelationClassifier({})
    with pytest.raises(KeyError, match="no fake relation output"):
        await missing.classify(candidate, _snapshots())

    invalid_payload = _classification_payload()
    invalid_payload["relation"] = "similar"
    invalid = FakeRelationClassifier({candidate.event_id: invalid_payload})
    with pytest.raises(ValidationError, match="Input should be"):
        await invalid.classify(candidate, _snapshots())


def test_strict_output_can_be_prevalidated_for_fake_fixture() -> None:
    output = RelationClassifierOutput.model_validate(_classification_payload())
    fake = FakeRelationClassifier({_candidate().event_id: output})

    assert isinstance(fake, FakeRelationClassifier)
