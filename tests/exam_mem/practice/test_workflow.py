from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest

from exam_mem.contracts import MemoryScope
from exam_mem.domain import KnowledgePointNormalizationResult, load_taxonomy
from exam_mem.practice import (
    AnswerSubmission,
    DiagnosisResult,
    ExamPracticeWorkflow,
    GradeResult,
    MemoryWriteResult,
    PracticeContext,
    PracticeMemoryCandidateBuilder,
    PracticeSpanName,
    PracticeState,
    PracticeWorkflowError,
    Question,
    Recommendation,
)
from exam_mem.storage import (
    AppendStatus,
    PracticeCheckpointAppendResult,
    PracticeCheckpointRecord,
    PracticeTraceAppendResult,
)

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
KP = "math1.probability.bayes"
SCOPE = MemoryScope(
    user_id="workflow_user",
    exam_id="postgraduate_entrance_exam",
    subject_id="math_1",
    memory_namespace="mastery",
)


def _question(question_id: str, difficulty: float = 0.5) -> Question:
    return Question(
        question_id=question_id,
        stem="Calculate the requested conditional probability.",
        knowledge_point_ids=[KP],
        difficulty=difficulty,
        reference_answer="Apply Bayes' theorem.",
        grading_rubric={"required_steps": ["apply_bayes"]},
    )


def _context(*, trace_id: str = "trace:workflow:001") -> PracticeContext:
    question = _question("question:bayes:001")
    submission = AnswerSubmission(
        practice_session_id="practice:workflow:001",
        question_id=question.question_id,
        answer="I reversed the conditional probability.",
        submitted_at=NOW,
        idempotency_key="answer:workflow:001",
    )
    return PracticeContext(
        practice_session_id=submission.practice_session_id,
        scope=SCOPE,
        current_question=question,
        submitted_answer=submission,
        step_state=PracticeState.ANSWER_RECEIVED,
        trace_id=trace_id,
    )


class FakeCheckpointRepository:
    def __init__(self) -> None:
        self.records: dict[tuple[str, str], PracticeCheckpointRecord] = {}

    async def create(self, checkpoint):  # noqa: ANN001, ANN201
        key = (checkpoint.context.practice_session_id, checkpoint.checkpoint_key)
        existing = self.records.get(key)
        if existing is not None:
            status = (
                AppendStatus.EXISTING
                if existing.checkpoint == checkpoint
                else AppendStatus.CONFLICT
            )
            return PracticeCheckpointAppendResult(status=status, record=existing)
        record = PracticeCheckpointRecord(
            checkpoint=checkpoint,
            row_version=1,
            created_at=NOW,
            updated_at=NOW,
        )
        self.records[key] = record
        return PracticeCheckpointAppendResult(status=AppendStatus.CREATED, record=record)

    async def get(self, context, practice_session_id, checkpoint_key):  # noqa: ANN001, ANN201
        record = self.records.get((practice_session_id, checkpoint_key))
        if record is None:
            return None
        stored = record.checkpoint.context.scope
        if (stored.user_id, stored.exam_id, stored.subject_id) != (
            context.user_id,
            context.exam_id,
            context.subject_id,
        ):
            return None
        return record

    async def advance(self, checkpoint, *, expected_row_version):  # noqa: ANN001, ANN201
        key = (checkpoint.context.practice_session_id, checkpoint.checkpoint_key)
        existing = self.records[key]
        if existing.row_version != expected_row_version:
            return None
        record = PracticeCheckpointRecord(
            checkpoint=checkpoint,
            row_version=existing.row_version + 1,
            created_at=existing.created_at,
            updated_at=NOW,
        )
        self.records[key] = record
        return record

    async def find_issued_question(
        self,
        context,
        practice_session_id,
        question_id,
    ):  # noqa: ANN001, ANN201
        for record in reversed(tuple(self.records.values())):
            checkpoint = record.checkpoint
            scope = checkpoint.context.scope
            if checkpoint.context.practice_session_id != practice_session_id:
                continue
            if (scope.user_id, scope.exam_id, scope.subject_id) != (
                context.user_id,
                context.exam_id,
                context.subject_id,
            ):
                continue
            for question in (
                checkpoint.recommended_question,
                checkpoint.context.current_question,
            ):
                if question is not None and question.question_id == question_id:
                    return question
        return None


class FakeTraceRepository:
    def __init__(self) -> None:
        self.spans = []

    async def next_step_id(self, trace_id):  # noqa: ANN001, ANN201
        return sum(span.trace_id == trace_id for span in self.spans) + 1

    async def append(self, span):  # noqa: ANN001, ANN201
        self.spans.append(span)
        return PracticeTraceAppendResult(status=AppendStatus.CREATED, span=span)

    async def list_trace(self, trace_id):  # noqa: ANN001, ANN201
        return [span for span in self.spans if span.trace_id == trace_id]


@dataclass
class FakeGrader:
    calls: int = 0

    async def grade(self, question, submission):  # noqa: ANN001, ANN201
        del question, submission
        self.calls += 1
        return GradeResult(
            correct=False,
            score=0.2,
            matched_rubric_items=[],
            missed_rubric_items=["apply_bayes"],
            evidence=["The conditional direction was reversed."],
            grader_version="answer_grader_v1",
        )


@dataclass
class FakeMapper:
    knowledge_point_id: str = KP
    calls: int = 0

    async def map(self, question):  # noqa: ANN001, ANN201
        del question
        self.calls += 1
        return KnowledgePointNormalizationResult(
            primary_knowledge_point_id=self.knowledge_point_id,
            primary_confidence=1.0,
        )


@dataclass
class FakeAnalyzer:
    calls: int = 0

    async def analyze(
        self,
        question,
        submission,
        grade_result,
        knowledge_point_ids,
    ):  # noqa: ANN001, ANN201
        del question, submission, grade_result
        self.calls += 1
        return DiagnosisResult(
            knowledge_point_ids=list(knowledge_point_ids),
            error_type=None if "unknown" in knowledge_point_ids else "concept_confusion",
            explanation="The conditional direction was reversed.",
            confidence=0.8,
            analyzer_version="error_analyzer_v1",
        )


@dataclass
class FakeMemoryWriter:
    fail_writes: int = 0
    calls: int = 0
    events: list = field(default_factory=list)
    candidate_counts: list[int] = field(default_factory=list)

    async def write(self, event, candidates):  # noqa: ANN001, ANN201
        self.calls += 1
        if self.fail_writes:
            self.fail_writes -= 1
            raise RuntimeError("temporary memory failure")
        self.events.append(event)
        self.candidate_counts.append(len(candidates))
        return MemoryWriteResult(decisions=(), projection_requests=())

    async def refresh_after_commit(self, result):  # noqa: ANN001, ANN201
        del result


@dataclass
class FakeRecommendationTool:
    calls: int = 0

    async def recommend(self, context, *, exclude_question_ids=()):  # noqa: ANN001, ANN201
        del context
        self.calls += 1
        question = _question(
            "question:bayes:002" if exclude_question_ids else "question:bayes:001",
            difficulty=0.4 if exclude_question_ids else 0.5,
        )
        return (
            Recommendation(
                question_id=question.question_id,
                target_knowledge_point_id=KP,
                target_difficulty=question.difficulty,
                reason_codes=["weakness"],
                source_memory_ids=[],
                policy_version="recommendation_policy_v1",
            ),
            question,
        )


def _workflow(
    *,
    checkpoints: FakeCheckpointRepository | None = None,
    traces: FakeTraceRepository | None = None,
    grader: FakeGrader | None = None,
    mapper: FakeMapper | None = None,
    analyzer: FakeAnalyzer | None = None,
    writer: FakeMemoryWriter | None = None,
    recommendation: FakeRecommendationTool | None = None,
):
    dependencies = {
        "checkpoints": checkpoints or FakeCheckpointRepository(),
        "traces": traces or FakeTraceRepository(),
        "grader": grader or FakeGrader(),
        "mapper": mapper or FakeMapper(),
        "analyzer": analyzer or FakeAnalyzer(),
        "writer": writer or FakeMemoryWriter(),
        "recommendation": recommendation or FakeRecommendationTool(),
    }
    workflow = ExamPracticeWorkflow(
        checkpoint_repository=dependencies["checkpoints"],
        trace_repository=dependencies["traces"],
        answer_grader=dependencies["grader"],
        knowledge_mapper=dependencies["mapper"],
        error_analyzer=dependencies["analyzer"],
        memory_candidate_builder=PracticeMemoryCandidateBuilder(load_taxonomy("math1_v1")),
        memory_writer=dependencies["writer"],
        recommendation_tool=dependencies["recommendation"],
    )
    return workflow, dependencies


async def _issue_first_question(
    workflow: ExamPracticeWorkflow,
    context: PracticeContext,
) -> None:
    await workflow.run(
        PracticeContext(
            practice_session_id=context.practice_session_id,
            scope=context.scope,
            trace_id=context.trace_id,
        )
    )


async def test_wrong_answer_runs_to_recommendation_and_replay_skips_side_effects() -> None:
    workflow, deps = _workflow()
    await _issue_first_question(workflow, _context())

    first = await workflow.run(_context())
    second = await workflow.run(_context())

    assert first.checkpoint.context.step_state is PracticeState.RECOMMENDED
    assert first.checkpoint.learning_event is not None
    assert second.replayed is True
    assert deps["grader"].calls == 1
    assert deps["mapper"].calls == 1
    assert deps["analyzer"].calls == 1
    assert deps["writer"].calls == 1
    assert deps["recommendation"].calls == 2
    span_names = [span.name for span in deps["traces"].spans]
    assert PracticeSpanName.ANSWER_GRADED in span_names
    assert PracticeSpanName.EVENT_APPENDED in span_names
    assert PracticeSpanName.QUESTION_RECOMMENDED in span_names


async def test_memory_failure_resumes_from_diagnosed_without_regrading() -> None:
    writer = FakeMemoryWriter(fail_writes=1)
    workflow, deps = _workflow(writer=writer)
    await _issue_first_question(workflow, _context())

    with pytest.raises(PracticeWorkflowError) as captured:
        await workflow.run(_context())
    resumed = await workflow.run(_context())

    assert captured.value.error_code == "memory_writer_failed"
    assert captured.value.step_state is PracticeState.DIAGNOSED
    assert captured.value.checkpoint is not None
    assert captured.value.checkpoint.grade_result is not None
    assert captured.value.checkpoint.diagnosis_result is not None
    assert captured.value.checkpoint.context.step_state is PracticeState.DIAGNOSED
    assert resumed.resumed_from_state is PracticeState.DIAGNOSED
    assert resumed.checkpoint.context.step_state is PracticeState.RECOMMENDED
    assert deps["grader"].calls == 1
    assert deps["mapper"].calls == 1
    assert deps["analyzer"].calls == 1
    assert writer.calls == 2


async def test_unknown_mapping_keeps_l1_and_skips_invalid_l2_candidates() -> None:
    mapper = FakeMapper(knowledge_point_id="unknown")
    writer = FakeMemoryWriter()
    workflow, _ = _workflow(mapper=mapper, writer=writer)
    await _issue_first_question(
        workflow,
        _context(trace_id="trace:workflow:unknown"),
    )

    result = await workflow.run(_context(trace_id="trace:workflow:unknown"))

    assert result.checkpoint.learning_event is not None
    assert result.checkpoint.learning_event.knowledge_point_ids == ["unknown"]
    assert writer.candidate_counts == [0]


async def test_submission_rejects_question_material_not_issued_by_server() -> None:
    workflow, deps = _workflow()
    context = _context()
    await _issue_first_question(workflow, context)
    assert context.current_question is not None
    tampered = context.model_copy(
        update={
            "current_question": context.current_question.model_copy(
                update={"reference_answer": "Client supplied replacement answer."}
            )
        }
    )

    with pytest.raises(PracticeWorkflowError) as captured:
        await workflow.run(tampered)

    assert captured.value.error_code == "practice_question_not_issued"
    assert deps["grader"].calls == 0
    assert deps["writer"].calls == 0
