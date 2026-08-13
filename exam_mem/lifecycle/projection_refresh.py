"""Post-commit handoff from durable lifecycle writes to disposable L3."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from exam_mem.contracts import LearningContext, LifecycleOperation
from exam_mem.storage.rebuild import StudentModelRebuildResult

from .applier import LifecycleApplicationResult
from .audit import LifecycleApplyState


@dataclass(frozen=True, slots=True)
class ProjectionRefreshRequest:
    """Retryable identity for rebuilding one three-dimensional StudentModel Scope."""

    decision_id: str
    context: LearningContext


class ProjectionRefreshFailed(RuntimeError):
    """A post-commit rebuild failed and may be retried with the same request."""

    error_code = "student_model_rebuild_failed"

    def __init__(self, request: ProjectionRefreshRequest) -> None:
        super().__init__(self.error_code)
        self.request = request


class _StudentModelRebuilder(Protocol):
    async def rebuild(self, context: LearningContext) -> StudentModelRebuildResult: ...


def build_projection_refresh_request(
    application: LifecycleApplicationResult,
) -> ProjectionRefreshRequest | None:
    """Return a post-commit request only when the application changed L2."""
    successful_mutation = application.apply_state in {
        LifecycleApplyState.APPLIED,
        LifecycleApplyState.CONTESTED,
    }
    operation = application.decision.policy_result.decision.operation
    if not successful_mutation or operation is LifecycleOperation.NO_OP:
        return None

    scope = application.decision.policy_result.scope
    return ProjectionRefreshRequest(
        decision_id=application.decision.decision_id,
        context=LearningContext(
            user_id=scope.user_id,
            exam_id=scope.exam_id,
            subject_id=scope.subject_id,
        ),
    )


class PostCommitProjectionRefresher:
    """Run Stage 05 rebuild after lifecycle commit, in a separate transaction."""

    def __init__(self, rebuild_service: _StudentModelRebuilder) -> None:
        self._rebuild_service = rebuild_service

    async def refresh(
        self,
        request: ProjectionRefreshRequest,
    ) -> StudentModelRebuildResult:
        try:
            return await self._rebuild_service.rebuild(request.context)
        except Exception as exc:
            raise ProjectionRefreshFailed(request) from exc


__all__ = [
    "PostCommitProjectionRefresher",
    "ProjectionRefreshFailed",
    "ProjectionRefreshRequest",
    "build_projection_refresh_request",
]
