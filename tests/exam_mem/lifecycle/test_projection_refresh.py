from __future__ import annotations

from datetime import datetime, timezone
from typing import cast

import pytest

from exam_mem.contracts import (
    LearningContext,
    LearningEvent,
    LearningMemory,
    LifecycleDecision,
    LifecycleOperation,
    MemoryScope,
    MemoryUpdateCandidate,
)
from exam_mem.lifecycle import (
    LifecycleApplicationResult,
    LifecycleApplyState,
    LifecycleChangeAuditRecord,
    LifecycleDecisionAuditRecord,
    LifecycleMemorySnapshot,
    LifecyclePolicyInput,
    LifecyclePolicyResult,
    PostCommitProjectionRefresher,
    ProjectionRefreshFailed,
    build_projection_refresh_request,
)
from exam_mem.storage import StudentModelRebuildResult

pytestmark = [pytest.mark.asyncio, pytest.mark.lifecycle]

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
SCOPE = MemoryScope(
    user_id="stage06_projection_user",
    exam_id="postgraduate_entrance_exam",
    subject_id="math_1",
    memory_namespace="mastery",
)
SLOT_KEY = "mastery:math1.linear_algebra.matrix_rank"


class _RebuildService:
    def __init__(self) -> None:
        self.calls = []
        self.fail_next = True
        self.result = cast(StudentModelRebuildResult, object())

    async def rebuild(self, context: LearningContext) -> StudentModelRebuildResult:
        self.calls.append(context)
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("injected projection failure")
        return self.result


def _application(
    operation: LifecycleOperation,
    apply_state: LifecycleApplyState,
) -> LifecycleApplicationResult:
    event = LearningEvent.model_validate(
        {
            "event_id": "stage06_projection_event",
            "idempotency_key": "idem:stage06_projection_event",
            "event_type": "answer_attempt",
            "context": {
                "user_id": SCOPE.user_id,
                "exam_id": SCOPE.exam_id,
                "subject_id": SCOPE.subject_id,
            },
            "session_id": "stage06_projection_session",
            "question_id": "stage06_projection_question",
            "knowledge_point_ids": ["math1.linear_algebra.matrix_rank"],
            "difficulty": 0.5,
            "answer_correct": True,
            "occurred_at": NOW,
        }
    )
    candidate = MemoryUpdateCandidate.model_validate(
        {
            "event_id": event.event_id,
            "scope": SCOPE.model_dump(mode="json"),
            "slot_key": SLOT_KEY,
            "proposed_value": {"type": "mastery", "level": "high", "score": 0.9},
            "evidence": {"source": "projection_refresh_test"},
        }
    )
    policy_input = LifecyclePolicyInput(
        event=event,
        candidate=candidate,
        evaluated_at=NOW,
    )
    policy_result = LifecyclePolicyResult(
        event_id=event.event_id,
        scope=SCOPE,
        slot_key=SLOT_KEY,
        decision=LifecycleDecision(
            operation=operation,
            target_memory_ids=[],
            reason_code="projection_refresh_test",
            confidence=0.9,
            policy_version="lifecycle_policy_v1",
        ),
    )
    decision = LifecycleDecisionAuditRecord(
        decision_id=f"stage06_projection_{operation.value.lower()}",
        trace_id="stage06_projection_trace",
        policy_input=policy_input,
        policy_result=policy_result,
        created_at=NOW,
    )
    if apply_state is LifecycleApplyState.IDEMPOTENT:
        change = LifecycleChangeAuditRecord(
            change_id=f"{decision.decision_id}:idempotent",
            decision_id=decision.decision_id,
            trace_id=decision.trace_id,
            apply_state=apply_state,
            recorded_at=NOW,
        )
    else:
        memory = LearningMemory(
            memory_id="stage06_projection_memory_v1",
            scope=SCOPE,
            slot_key=SLOT_KEY,
            value=candidate.proposed_value,
            confidence=0.9,
            evidence_count=1,
            lifecycle_state="active",
            version=1,
            valid_from=NOW,
            valid_to=None,
            superseded_by=None,
            provenance=[event.event_id],
        )
        after = LifecycleMemorySnapshot(
            memory=memory,
            row_version=1,
            policy_version="lifecycle_policy_v1",
        )
        change = LifecycleChangeAuditRecord(
            change_id=f"{decision.decision_id}:applied",
            decision_id=decision.decision_id,
            trace_id=decision.trace_id,
            apply_state=apply_state,
            memory_id=memory.memory_id,
            after_state=after,
            actual_row_version=1,
            recorded_at=NOW,
        )
    return LifecycleApplicationResult(decision=decision, changes=(change,))


async def test_successful_l2_mutation_builds_three_dimensional_refresh_request() -> None:
    application = _application(LifecycleOperation.ADD, LifecycleApplyState.APPLIED)

    request = build_projection_refresh_request(application)

    assert request is not None
    assert request.decision_id == application.decision.decision_id
    assert request.context == application.decision.policy_input.event.context


async def test_idempotent_no_op_does_not_rebuild_unchanged_l3() -> None:
    application = _application(LifecycleOperation.NO_OP, LifecycleApplyState.IDEMPOTENT)

    assert build_projection_refresh_request(application) is None


async def test_failed_post_commit_refresh_preserves_retry_identity_and_then_succeeds() -> None:
    request = build_projection_refresh_request(
        _application(LifecycleOperation.ADD, LifecycleApplyState.APPLIED)
    )
    assert request is not None
    rebuild_service = _RebuildService()
    refresher = PostCommitProjectionRefresher(rebuild_service)

    with pytest.raises(ProjectionRefreshFailed) as failure:
        await refresher.refresh(request)

    assert failure.value.error_code == "student_model_rebuild_failed"
    assert failure.value.request == request
    assert isinstance(failure.value.__cause__, RuntimeError)

    result = await refresher.refresh(failure.value.request)

    assert result is rebuild_service.result
    assert rebuild_service.calls == [request.context, request.context]
