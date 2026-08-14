"""Seven single-responsibility DeepTutor Tool adapters for ExamMem practice."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from enum import Enum
import json
from typing import Any, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field

from deeptutor.plugins.host_services import BaseTool, ToolDefinition, ToolResult
from exam_mem.contracts import LearningContext, LearningEvent, MemoryScope, MemoryUpdateCandidate

from .contracts import (
    AnswerSubmission,
    DiagnosisResult,
    GradeResult,
    PracticeContext,
    Question,
    Recommendation,
)
from .error_analyzer import DeepTutorErrorAnalyzerAdapter
from .grading import DeepTutorAnswerGraderAdapter
from .knowledge_mapper import DeepTutorKnowledgeMapperAdapter
from .memory import MemoryWriteResult
from .question_retriever import QuestionRetriever


class PracticeToolNotBoundError(RuntimeError):
    error_code = "practice_tool_not_bound"


class _ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _QuestionRetrieverInput(_ToolInput):
    scope: MemoryScope
    target_knowledge_point_id: str
    target_difficulty: float = Field(ge=0.0, le=1.0)
    exclude_question_ids: tuple[str, ...] = ()


class _AnswerGraderInput(_ToolInput):
    question: Question
    submission: AnswerSubmission


class _KnowledgeMapperInput(_ToolInput):
    question: Question


class _ErrorAnalyzerInput(_ToolInput):
    question: Question
    submission: AnswerSubmission
    grade_result: GradeResult
    knowledge_point_ids: tuple[str, ...] = Field(min_length=1)


class _MemoryReaderInput(_ToolInput):
    context: LearningContext


class _MemoryWriterInput(_ToolInput):
    event: LearningEvent
    candidates: tuple[MemoryUpdateCandidate, ...]


class _RecommendationInput(_ToolInput):
    context: PracticeContext
    exclude_question_ids: tuple[str, ...] = ()


class _MemoryReaderPort(Protocol):
    async def query_state(self, context: LearningContext): ...  # noqa: ANN201

    async def retrieve(self, scope: MemoryScope, query: str, top_k: int): ...  # noqa: ANN201

    async def snapshot(self, context: LearningContext) -> dict: ...


class _MemoryWriterPort(Protocol):
    async def write(
        self,
        event: LearningEvent,
        candidates: list[MemoryUpdateCandidate],
    ) -> MemoryWriteResult: ...

    async def refresh_after_commit(self, result: MemoryWriteResult) -> None: ...


class _RecommendationPort(Protocol):
    async def recommend(
        self,
        context: PracticeContext,
        *,
        exclude_question_ids: Sequence[str] = (),
    ) -> tuple[Recommendation, Question]: ...


class QuestionRetrieverTool(BaseTool):
    def __init__(self, retriever: QuestionRetriever | None = None) -> None:
        self._retriever = retriever

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="question_retriever",
            description="Select one scoped ExamMem question without Memory side effects.",
            raw_parameters=_QuestionRetrieverInput.model_json_schema(),
        )

    async def retrieve(
        self,
        *,
        scope: MemoryScope,
        target_knowledge_point_id: str,
        target_difficulty: float,
        exclude_question_ids: Sequence[str] = (),
    ) -> Question:
        if self._retriever is None:
            raise PracticeToolNotBoundError("question_retriever requires a turn-bound catalog")
        return await self._retriever.retrieve(
            scope=scope,
            target_knowledge_point_id=target_knowledge_point_id,
            target_difficulty=target_difficulty,
            exclude_question_ids=exclude_question_ids,
        )

    async def retrieve_syllabus_fallback(
        self,
        *,
        scope: MemoryScope,
        exclude_question_ids: Sequence[str] = (),
    ) -> Question:
        if self._retriever is None:
            raise PracticeToolNotBoundError("question_retriever requires a turn-bound catalog")
        return await self._retriever.retrieve_syllabus_fallback(
            scope=scope,
            exclude_question_ids=exclude_question_ids,
        )

    def fallback_target_knowledge_point_id(self, question: Question) -> str:
        if self._retriever is None:
            raise PracticeToolNotBoundError("question_retriever requires a turn-bound catalog")
        return self._retriever.fallback_target_knowledge_point_id(question)

    async def execute(self, **kwargs: Any) -> ToolResult:
        return await _execute_validated(
            _QuestionRetrieverInput,
            kwargs,
            lambda request: self.retrieve(
                scope=request.scope,
                target_knowledge_point_id=request.target_knowledge_point_id,
                target_difficulty=request.target_difficulty,
                exclude_question_ids=request.exclude_question_ids,
            ),
        )


class AnswerGraderTool(BaseTool):
    def __init__(self, grader=None) -> None:  # noqa: ANN001
        self._grader = grader or DeepTutorAnswerGraderAdapter()

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="answer_grader",
            description="Grade one answer without inferring long-term mastery.",
            raw_parameters=_AnswerGraderInput.model_json_schema(),
        )

    async def grade(self, question: Question, submission: AnswerSubmission) -> GradeResult:
        return await self._grader.grade(question, submission)

    async def execute(self, **kwargs: Any) -> ToolResult:
        return await _execute_validated(
            _AnswerGraderInput,
            kwargs,
            lambda request: self.grade(request.question, request.submission),
        )


class KnowledgeMapperTool(BaseTool):
    def __init__(
        self,
        mapper=None,  # noqa: ANN001
        *,
        taxonomy_version: str = "math1_v1",
        taxonomy=None,  # noqa: ANN001
    ) -> None:
        self._mapper = mapper or DeepTutorKnowledgeMapperAdapter(
            taxonomy_version, taxonomy=taxonomy
        )

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="knowledge_mapper",
            description="Normalize question concepts only through the frozen taxonomy.",
            raw_parameters=_KnowledgeMapperInput.model_json_schema(),
        )

    async def map(self, question: Question):  # noqa: ANN201
        return await self._mapper.map(question)

    async def execute(self, **kwargs: Any) -> ToolResult:
        return await _execute_validated(
            _KnowledgeMapperInput,
            kwargs,
            lambda request: self.map(request.question),
        )


class ErrorAnalyzerTool(BaseTool):
    def __init__(self, analyzer=None) -> None:  # noqa: ANN001
        self._analyzer = analyzer or DeepTutorErrorAnalyzerAdapter()

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="error_analyzer",
            description="Classify error evidence without deciding any database update.",
            raw_parameters=_ErrorAnalyzerInput.model_json_schema(),
        )

    async def analyze(
        self,
        question: Question,
        submission: AnswerSubmission,
        grade_result: GradeResult,
        knowledge_point_ids: Sequence[str],
    ) -> DiagnosisResult:
        return await self._analyzer.analyze(
            question,
            submission,
            grade_result,
            knowledge_point_ids,
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        return await _execute_validated(
            _ErrorAnalyzerInput,
            kwargs,
            lambda request: self.analyze(
                request.question,
                request.submission,
                request.grade_result,
                request.knowledge_point_ids,
            ),
        )


class MemoryReaderTool(BaseTool):
    def __init__(self, reader: _MemoryReaderPort | None = None) -> None:
        self._reader = reader

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="memory_reader",
            description="Read ExamMem state through an exact Scope without terminal L2 leakage.",
            raw_parameters=_MemoryReaderInput.model_json_schema(),
        )

    async def query_state(self, context: LearningContext):  # noqa: ANN201
        if self._reader is None:
            raise PracticeToolNotBoundError("memory_reader requires a selected Backend")
        return await self._reader.query_state(context)

    async def execute(self, **kwargs: Any) -> ToolResult:
        return await _execute_validated(
            _MemoryReaderInput,
            kwargs,
            lambda request: self.query_state(request.context),
        )


class MemoryWriterTool(BaseTool):
    def __init__(self, writer: _MemoryWriterPort | None = None) -> None:
        self._writer = writer

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="memory_writer",
            description=(
                "Capability-owned validated Learning Event writer; unbound LLM calls are refused."
            ),
            raw_parameters=_MemoryWriterInput.model_json_schema(),
        )

    async def write(
        self,
        event: LearningEvent,
        candidates: list[MemoryUpdateCandidate],
    ) -> MemoryWriteResult:
        if self._writer is None:
            raise PracticeToolNotBoundError(
                "memory_writer is restricted to a validated exam_practice turn"
            )
        return await self._writer.write(event, candidates)

    async def refresh_after_commit(self, result: MemoryWriteResult) -> None:
        if self._writer is None:
            raise PracticeToolNotBoundError(
                "memory_writer is restricted to a validated exam_practice turn"
            )
        await self._writer.refresh_after_commit(result)

    async def execute(self, **kwargs: Any) -> ToolResult:
        return await _execute_validated(
            _MemoryWriterInput,
            kwargs,
            lambda request: self.write(request.event, list(request.candidates)),
        )


class RecommendationTool(BaseTool):
    def __init__(self, recommender: _RecommendationPort | None = None) -> None:
        self._recommender = recommender

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="recommendation",
            description="Apply recommendation_policy_v1 and return an explainable next question.",
            raw_parameters=_RecommendationInput.model_json_schema(),
        )

    async def recommend(
        self,
        context: PracticeContext,
        *,
        exclude_question_ids: Sequence[str] = (),
    ) -> tuple[Recommendation, Question]:
        if self._recommender is None:
            raise PracticeToolNotBoundError("recommendation requires a turn-bound Backend")
        return await self._recommender.recommend(
            context,
            exclude_question_ids=exclude_question_ids,
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        return await _execute_validated(
            _RecommendationInput,
            kwargs,
            lambda request: self.recommend(
                request.context,
                exclude_question_ids=request.exclude_question_ids,
            ),
        )


EXAM_MEM_PRACTICE_TOOL_TYPES = (
    QuestionRetrieverTool,
    AnswerGraderTool,
    KnowledgeMapperTool,
    ErrorAnalyzerTool,
    MemoryReaderTool,
    MemoryWriterTool,
    RecommendationTool,
)
EXAM_MEM_PRACTICE_TOOL_NAMES = (
    "question_retriever",
    "answer_grader",
    "knowledge_mapper",
    "error_analyzer",
    "memory_reader",
    "memory_writer",
    "recommendation",
)


async def _execute_structured(operation) -> ToolResult:  # noqa: ANN001
    try:
        result = await operation()
    except Exception as exc:
        error_code = getattr(exc, "error_code", None) or "practice_tool_failed"
        return ToolResult(
            content=json.dumps(
                {"error_code": error_code, "message": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
            ),
            metadata={"error_code": error_code},
            success=False,
        )
    payload = _json_value(result)
    return ToolResult(
        content=json.dumps(payload, ensure_ascii=False, sort_keys=True),
        metadata={"result": payload},
    )


async def _execute_validated(model, kwargs, operation) -> ToolResult:  # noqa: ANN001
    return await _execute_structured(lambda: operation(model.model_validate(kwargs)))


def _json_value(value):  # noqa: ANN001, ANN201
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {item.name: _json_value(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    return value


__all__ = [
    "AnswerGraderTool",
    "EXAM_MEM_PRACTICE_TOOL_NAMES",
    "EXAM_MEM_PRACTICE_TOOL_TYPES",
    "ErrorAnalyzerTool",
    "KnowledgeMapperTool",
    "MemoryReaderTool",
    "MemoryWriterTool",
    "PracticeToolNotBoundError",
    "QuestionRetrieverTool",
    "RecommendationTool",
]
