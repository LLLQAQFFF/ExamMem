"""Scoped product read models derived from existing ExamMem facts."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from exam_mem.contracts import LearningContext
from exam_mem.practice.checkpoint import PracticeWorkflowCheckpoint

from .grade_review_repository import PostgresGradeReviewRepository
from .models import (
    assessment_attempts,
    assessments,
    learning_events,
    learning_memories,
    lifecycle_decisions,
    memory_change_log,
    practice_trace_spans,
    practice_workflow_checkpoints,
)


class PostgresExamProductRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def list_practice_sessions(self, context: LearningContext) -> list[dict[str, Any]]:
        rows = await self._checkpoint_rows(context)
        by_session: dict[str, list[Any]] = defaultdict(list)
        for row in rows:
            by_session[row["practice_session_id"]].append(row)
        sessions = []
        for practice_session_id, session_rows in by_session.items():
            latest = session_rows[0]
            started_at = min(row["created_at"] for row in session_rows)
            checkpoints = [
                PracticeWorkflowCheckpoint.model_validate(row["payload"]) for row in session_rows
            ]
            latest_checkpoint = checkpoints[0]
            answer_count = sum(item.context.submitted_answer is not None for item in checkpoints)
            grades = [item.grade_result for item in checkpoints if item.grade_result is not None]
            score, score_invalid = _grade_summary(grades)
            sessions.append(
                {
                    "practice_session_id": practice_session_id,
                    "trace_id": latest["trace_id"],
                    "started_at": started_at.isoformat(),
                    "step_state": latest["step_state"],
                    "updated_at": latest["updated_at"].isoformat(),
                    "answer_count": answer_count,
                    "score": score,
                    "score_invalid": score_invalid,
                    "correct_count": sum(grade.correct for grade in grades),
                    "current_checkpoint": _public_checkpoint(latest_checkpoint),
                    "runtime": (
                        None
                        if latest_checkpoint.runtime_snapshot is None
                        else latest_checkpoint.runtime_snapshot.model_dump(mode="json")
                    ),
                }
            )
        sessions.sort(
            key=lambda session: (session["started_at"], session["practice_session_id"]),
            reverse=True,
        )
        total_attempts = len(sessions)
        for index, session in enumerate(sessions):
            session["attempt_number"] = total_attempts - index
        return sessions

    async def get_practice_session(
        self, context: LearningContext, practice_session_id: str
    ) -> dict[str, Any] | None:
        sessions = await self.list_practice_sessions(context)
        summary = next(
            (item for item in sessions if item["practice_session_id"] == practice_session_id),
            None,
        )
        if summary is None:
            return None
        rows = [
            row
            for row in await self._checkpoint_rows(context)
            if row["practice_session_id"] == practice_session_id
        ]
        checkpoints = [PracticeWorkflowCheckpoint.model_validate(row["payload"]) for row in rows]
        trace_id = str(summary["trace_id"])
        trace_rows = (
            (
                await self._connection.execute(
                    select(practice_trace_spans)
                    .where(practice_trace_spans.c.trace_id == trace_id)
                    .order_by(practice_trace_spans.c.step_id)
                )
            )
            .mappings()
            .all()
        )
        decision_rows = (
            (
                await self._connection.execute(
                    select(lifecycle_decisions)
                    .where(lifecycle_decisions.c.trace_id == trace_id)
                    .order_by(lifecycle_decisions.c.created_at)
                )
            )
            .mappings()
            .all()
        )
        change_rows = (
            (
                await self._connection.execute(
                    select(memory_change_log)
                    .where(memory_change_log.c.trace_id == trace_id)
                    .order_by(memory_change_log.c.created_at)
                )
            )
            .mappings()
            .all()
        )
        reviews = await PostgresGradeReviewRepository(self._connection).list_scope(context)
        assessment = (
            (
                await self._connection.execute(
                    select(
                        assessment_attempts.c.attempt_id,
                        assessment_attempts.c.assessment_version,
                        assessments.c.assessment_id,
                        assessments.c.title,
                        assessments.c.taxonomy_version,
                    )
                    .join(
                        assessments,
                        assessments.c.assessment_id == assessment_attempts.c.assessment_id,
                    )
                    .where(
                        assessment_attempts.c.user_id == context.user_id,
                        assessment_attempts.c.practice_session_id == practice_session_id,
                        assessments.c.user_id == context.user_id,
                        assessments.c.exam_id == context.exam_id,
                        assessments.c.subject_id == context.subject_id,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        return {
            **summary,
            "assessment": None if assessment is None else dict(assessment),
            "checkpoints": [_review_checkpoint(checkpoint) for checkpoint in reversed(checkpoints)],
            "attempt_summary": _attempt_summary(checkpoints),
            "trace": [_trace_payload(row) for row in trace_rows],
            "lifecycle": {
                "decisions": [_audit_payload(row) for row in decision_rows],
                "changes": [_audit_payload(row) for row in change_rows],
            },
            "grade_reviews": [
                event.model_dump(mode="json")
                for event in reviews
                if event.practice_session_id == practice_session_id
            ],
        }

    async def list_issues(self, context: LearningContext) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        rows = await self._checkpoint_rows(context)
        trace_ids = sorted({str(row["trace_id"]) for row in rows})
        if trace_ids:
            trace_rows = (
                (
                    await self._connection.execute(
                        select(practice_trace_spans)
                        .where(practice_trace_spans.c.trace_id.in_(trace_ids))
                        .order_by(practice_trace_spans.c.trace_id, practice_trace_spans.c.step_id)
                    )
                )
                .mappings()
                .all()
            )
            completed = {
                (row["trace_id"], row["span_name"])
                for row in trace_rows
                if row["status"] == "completed"
            }
            for row in trace_rows:
                if row["status"] != "failed":
                    continue
                resolved = (row["trace_id"], row["span_name"]) in completed
                issues.append(
                    {
                        "issue_id": f"workflow:{row['trace_id']}:{row['step_id']}",
                        "type": "workflow_failure",
                        "status": "resolved" if resolved else "open",
                        "trace_id": row["trace_id"],
                        "summary": row["error_code"],
                    }
                )
        for event in await PostgresGradeReviewRepository(self._connection).list_scope(context):
            if event.action.value != "dispute":
                continue
            chain = await PostgresGradeReviewRepository(self._connection).list_chain(
                context, event.review_chain_id
            )
            issues.append(
                {
                    "issue_id": f"grade:{event.review_chain_id}",
                    "type": "grade_disputed",
                    "status": "resolved" if len(chain) > 1 else "open",
                    "practice_session_id": event.practice_session_id,
                    "summary": event.reason,
                }
            )
        contested_rows = (
            (
                await self._connection.execute(
                    select(learning_memories.c.memory_id, learning_memories.c.slot_key).where(
                        learning_memories.c.user_id == context.user_id,
                        learning_memories.c.exam_id == context.exam_id,
                        learning_memories.c.subject_id == context.subject_id,
                        learning_memories.c.lifecycle_state == "contested",
                    )
                )
            )
            .mappings()
            .all()
        )
        issues.extend(
            {
                "issue_id": f"contested:{row['memory_id']}",
                "type": "contested_evidence",
                "status": "evidence_conflict",
                "memory_id": row["memory_id"],
                "summary": row["slot_key"],
            }
            for row in contested_rows
        )
        correction_rows = (
            (
                await self._connection.execute(
                    select(
                        learning_events.c.event_id,
                        learning_events.c.correction_statement,
                    ).where(
                        learning_events.c.user_id == context.user_id,
                        learning_events.c.exam_id == context.exam_id,
                        learning_events.c.subject_id == context.subject_id,
                        learning_events.c.event_type == "explicit_correction",
                    )
                )
            )
            .mappings()
            .all()
        )
        issues.extend(
            {
                "issue_id": f"memory_inaccurate:{row['event_id']}",
                "type": "memory_inaccurate",
                "status": "resolved",
                "summary": row["correction_statement"],
            }
            for row in correction_rows
        )
        for row in rows:
            checkpoint = PracticeWorkflowCheckpoint.model_validate(row["payload"])
            if checkpoint.projection_requests and not checkpoint.projection_refreshed:
                issues.append(
                    {
                        "issue_id": f"projection:{row['practice_session_id']}:{row['checkpoint_key']}",
                        "type": "projection_pending",
                        "status": "open",
                        "practice_session_id": row["practice_session_id"],
                        "summary": "Student Model projection is pending recovery.",
                    }
                )
        return issues

    async def _checkpoint_rows(self, context: LearningContext) -> list[Any]:
        return (
            (
                await self._connection.execute(
                    select(practice_workflow_checkpoints)
                    .where(
                        practice_workflow_checkpoints.c.user_id == context.user_id,
                        practice_workflow_checkpoints.c.exam_id == context.exam_id,
                        practice_workflow_checkpoints.c.subject_id == context.subject_id,
                    )
                    .order_by(
                        practice_workflow_checkpoints.c.updated_at.desc(),
                        practice_workflow_checkpoints.c.checkpoint_key.desc(),
                    )
                )
            )
            .mappings()
            .all()
        )


def _public_checkpoint(checkpoint: PracticeWorkflowCheckpoint) -> dict[str, Any]:
    question = (
        None
        if checkpoint.context.catalog_completed
        else checkpoint.recommended_question or checkpoint.context.current_question
    )
    return {
        "checkpoint_key": checkpoint.checkpoint_key,
        "step_state": checkpoint.context.step_state.value,
        "answered_question_count": len(checkpoint.context.answered_question_ids),
        "question_count": len(checkpoint.context.question_catalog),
        "completed": checkpoint.context.catalog_completed,
        "question": (
            None
            if question is None
            else question.model_dump(mode="json", exclude={"reference_answer", "grading_rubric"})
        ),
        "grade_result": (
            None
            if checkpoint.grade_result is None
            else checkpoint.grade_result.model_dump(mode="json")
        ),
        "grade_artifact": (
            None
            if checkpoint.grade_artifact_identity is None
            else {
                "identity": checkpoint.grade_artifact_identity.model_dump(mode="json"),
                "reused": checkpoint.grade_reused_from_checkpoint is not None,
                "source_checkpoint": checkpoint.grade_reused_from_checkpoint,
            }
        ),
        "diagnosis_result": (
            None
            if checkpoint.diagnosis_result is None
            else checkpoint.diagnosis_result.model_dump(mode="json")
        ),
        "recommendation": (
            None
            if checkpoint.recommendation is None
            else checkpoint.recommendation.model_dump(mode="json")
        ),
    }


def _review_checkpoint(checkpoint: PracticeWorkflowCheckpoint) -> dict[str, Any]:
    """Expose answer material only after that exact question was submitted."""

    payload = _public_checkpoint(checkpoint)
    submission = checkpoint.context.submitted_answer
    question = checkpoint.context.current_question
    if submission is None or question is None:
        return payload
    payload.update(
        {
            "question": question.model_dump(mode="json"),
            "submitted_answer": {
                "answer": submission.answer,
                "submitted_at": submission.submitted_at.isoformat(),
            },
            "learning_event_id": (
                None if checkpoint.learning_event is None else checkpoint.learning_event.event_id
            ),
            "mapped_knowledge_point_ids": list(checkpoint.mapped_knowledge_point_ids),
        }
    )
    return payload


def _attempt_summary(checkpoints: list[PracticeWorkflowCheckpoint]) -> dict[str, Any]:
    answered = [item for item in checkpoints if item.context.submitted_answer is not None]
    grades = [item.grade_result for item in answered if item.grade_result is not None]
    strengths = sorted(
        {
            point
            for item in answered
            if item.grade_result is not None and item.grade_result.correct
            for point in item.mapped_knowledge_point_ids
        }
    )
    weak_points = sorted(
        {
            point
            for item in answered
            if item.grade_result is not None and not item.grade_result.correct
            for point in item.mapped_knowledge_point_ids
        }
    )
    error_patterns = sorted(
        {
            item.diagnosis_result.error_type.value
            for item in answered
            if item.diagnosis_result is not None and item.diagnosis_result.error_type is not None
        }
    )
    next_actions = [
        {
            "knowledge_point_id": item.recommendation.target_knowledge_point_id,
            "reason_codes": list(item.recommendation.reason_codes),
            "source_memory_ids": list(item.recommendation.source_memory_ids),
        }
        for item in answered
        if item.recommendation is not None
    ]
    score, score_invalid = _grade_summary(grades)
    return {
        "question_count": max(
            (len(item.context.question_catalog) for item in checkpoints), default=0
        ),
        "answered_count": len(answered),
        "correct_count": sum(grade.correct for grade in grades),
        "score": score,
        "score_invalid": score_invalid,
        "strengths": strengths,
        "weak_points": weak_points,
        "error_patterns": error_patterns,
        "next_actions": next_actions,
    }


def _grade_summary(grades: list[Any]) -> tuple[float | None, bool]:
    if not grades:
        return None, False
    if any(not 0.0 <= grade.score <= 1.0 for grade in grades):
        return None, True
    return sum(grade.score for grade in grades) / len(grades), False


def _trace_payload(row: Any) -> dict[str, Any]:
    return {
        "step_id": row["step_id"],
        "name": row["span_name"],
        "status": row["status"],
        "output_summary": row["output_summary"],
        "versions": row["versions"],
        "error_code": row["error_code"],
        "related_record_ids": row["related_record_ids"],
    }


def _audit_payload(row: Any) -> dict[str, Any]:
    return {
        key: value.isoformat() if hasattr(value, "isoformat") else value
        for key, value in row.items()
        if key != "input_summary"
    }


__all__ = ["PostgresExamProductRepository"]
