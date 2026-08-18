"""Structured contracts for the ExamMem practice workflow."""

from importlib import import_module

from .catalog import stage07_practice_questions, stage07_question
from .checkpoint import (
    PracticeRuntimeSnapshot,
    PracticeWorkflowCheckpoint,
    checkpoint_key_for_context,
)
from .contracts import (
    AnswerSubmission,
    DiagnosisResult,
    GradeArtifactIdentity,
    GradeResult,
    PracticeContext,
    PracticeState,
    Question,
    Recommendation,
)
from .corrections import (
    ConfirmedCorrectionRelationClassifier,
    CorrectionError,
    ExplicitCorrectionRequest,
    ExplicitCorrectionResult,
    ExplicitCorrectionService,
    QueryServiceRecommendationRefresher,
    ResolvedCorrectionTarget,
    recognize_correction_intent,
)
from .error_analyzer import DeepTutorErrorAnalyzerAdapter, ErrorAnalysisCompletion
from .grading import DeepTutorAnswerGraderAdapter, GradingCompletion
from .knowledge_mapper import (
    CatalogKnowledgeMapper,
    DeepTutorKnowledgeMapperAdapter,
    KnowledgeMappingCompletion,
    KnowledgePointExtraction,
    KnowledgePointSignal,
)
from .learning_profile import (
    LEARNING_PROFILE_POLICY_VERSION,
    KnowledgePointProfile,
    LearningProfile,
    LearningProfileSummary,
    ReviewQueueItem,
    build_learning_profile,
)
from .learning_profile_service import LearningProfileQueryService
from .memory import (
    MemoryReader,
    MemoryWriter,
    MemoryWriteResult,
    PracticeMemoryCandidateBuilder,
    ProjectionRefreshExecutor,
    ProjectionRequestSource,
)
from .memory_workbench import (
    LearningMemoryDetail,
    LearningMemoryEvidence,
    LearningMemoryListRequest,
    LearningMemoryQueryService,
    LearningMemorySummary,
)
from .question_adapter import DeepTutorQuestionAdapter, DeepTutorQuizPair
from .question_retriever import QuestionCatalog, QuestionRetrievalError, QuestionRetriever
from .recommendation import (
    RecommendationCandidate,
    RecommendationFeatures,
    RecommendationPolicyV1,
    RecommendationPolicyV1Config,
    RecommendationScore,
)
from .review import GradeReviewAction, GradeReviewEvent
from .trace import (
    PracticeSpanName,
    PracticeSpanStatus,
    PracticeTracePersistenceError,
    PracticeTraceRecorder,
    PracticeTraceSpan,
)

_LAZY_EXPORTS = {
    "PRACTICE_CONTEXT_METADATA_KEY": (".capability", "PRACTICE_CONTEXT_METADATA_KEY"),
    "ExamPracticeCapability": (".capability", "ExamPracticeCapability"),
    "PracticeCapabilityInputError": (".capability", "PracticeCapabilityInputError"),
    "AnswerGrader": (".workflow", "AnswerGrader"),
    "ErrorAnalyzer": (".workflow", "ErrorAnalyzer"),
    "ExamPracticeWorkflow": (".workflow", "ExamPracticeWorkflow"),
    "KnowledgeMapper": (".workflow", "KnowledgeMapper"),
    "PracticeMemoryWriter": (".workflow", "PracticeMemoryWriter"),
    "PracticeRecommendationTool": (".workflow", "PracticeRecommendationTool"),
    "PracticeWorkflowError": (".workflow", "PracticeWorkflowError"),
    "PracticeWorkflowResult": (".workflow", "PracticeWorkflowResult"),
    "WorkflowEventSink": (".workflow", "WorkflowEventSink"),
    "PRACTICE_QUESTIONS_METADATA_KEY": (
        ".provider",
        "PRACTICE_QUESTIONS_METADATA_KEY",
    ),
    "PracticeRuntimeConfigurationError": (
        ".provider",
        "PracticeRuntimeConfigurationError",
    ),
    "PracticeRuntimeProvider": (".provider", "PracticeRuntimeProvider"),
    "PlanTransitionError": (".plan_transitions", "PlanTransitionError"),
    "PlanCancellationIntent": (".plan_transitions", "PlanCancellationIntent"),
    "PlanTransitionRequest": (".plan_transitions", "PlanTransitionRequest"),
    "PlanTransitionResult": (".plan_transitions", "PlanTransitionResult"),
    "PlanTransitionService": (".plan_transitions", "PlanTransitionService"),
    "PracticeProgressTransitionRequest": (
        ".plan_transitions",
        "PracticeProgressTransitionRequest",
    ),
    "ResolvedPlanTarget": (".plan_transitions", "ResolvedPlanTarget"),
    "recognize_plan_cancellation_intent": (
        ".plan_transitions",
        "recognize_plan_cancellation_intent",
    ),
    "SystemPlanExpirationRequest": (
        ".plan_transitions",
        "SystemPlanExpirationRequest",
    ),
    "UserPlanCancellationRequest": (
        ".plan_transitions",
        "UserPlanCancellationRequest",
    ),
    "AnswerGraderTool": (".tools", "AnswerGraderTool"),
    "ErrorAnalyzerTool": (".tools", "ErrorAnalyzerTool"),
    "KnowledgeMapperTool": (".tools", "KnowledgeMapperTool"),
    "MemoryReaderTool": (".tools", "MemoryReaderTool"),
    "MemoryWriterTool": (".tools", "MemoryWriterTool"),
    "QuestionRetrieverTool": (".tools", "QuestionRetrieverTool"),
    "RecommendationTool": (".tools", "RecommendationTool"),
}


def __getattr__(name: str):  # noqa: ANN202
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = target
    value = getattr(import_module(module_name, __name__), attribute_name)
    globals()[name] = value
    return value


__all__ = [
    "AnswerSubmission",
    "AnswerGrader",
    "CatalogKnowledgeMapper",
    "DeepTutorAnswerGraderAdapter",
    "DeepTutorErrorAnalyzerAdapter",
    "DeepTutorKnowledgeMapperAdapter",
    "DeepTutorQuestionAdapter",
    "DeepTutorQuizPair",
    "DiagnosisResult",
    "ConfirmedCorrectionRelationClassifier",
    "CorrectionError",
    "ErrorAnalysisCompletion",
    "ErrorAnalyzer",
    "ExplicitCorrectionRequest",
    "ExplicitCorrectionResult",
    "ExplicitCorrectionService",
    "ExamPracticeCapability",
    "ExamPracticeWorkflow",
    "GradeResult",
    "GradeArtifactIdentity",
    "GradeReviewAction",
    "GradeReviewEvent",
    "GradingCompletion",
    "KnowledgeMappingCompletion",
    "KnowledgeMapper",
    "KnowledgePointExtraction",
    "KnowledgePointSignal",
    "LEARNING_PROFILE_POLICY_VERSION",
    "KnowledgePointProfile",
    "LearningProfile",
    "LearningProfileSummary",
    "LearningProfileQueryService",
    "MemoryReader",
    "MemoryWriteResult",
    "MemoryWriter",
    "LearningMemoryDetail",
    "LearningMemoryEvidence",
    "LearningMemoryListRequest",
    "LearningMemoryQueryService",
    "LearningMemorySummary",
    "PRACTICE_CONTEXT_METADATA_KEY",
    "PRACTICE_QUESTIONS_METADATA_KEY",
    "PlanTransitionError",
    "PlanCancellationIntent",
    "PlanTransitionRequest",
    "PlanTransitionResult",
    "PlanTransitionService",
    "PracticeCapabilityInputError",
    "PracticeMemoryWriter",
    "PracticeContext",
    "PracticeMemoryCandidateBuilder",
    "PracticeState",
    "PracticeSpanName",
    "PracticeSpanStatus",
    "PracticeTracePersistenceError",
    "PracticeTraceRecorder",
    "PracticeTraceSpan",
    "PracticeRecommendationTool",
    "PracticeRuntimeConfigurationError",
    "PracticeRuntimeSnapshot",
    "PracticeRuntimeProvider",
    "PracticeProgressTransitionRequest",
    "PracticeWorkflowCheckpoint",
    "PracticeWorkflowError",
    "PracticeWorkflowResult",
    "Question",
    "QuestionCatalog",
    "QuestionRetrievalError",
    "QuestionRetriever",
    "QueryServiceRecommendationRefresher",
    "ResolvedCorrectionTarget",
    "ResolvedPlanTarget",
    "ProjectionRefreshExecutor",
    "ProjectionRequestSource",
    "Recommendation",
    "RecommendationCandidate",
    "RecommendationFeatures",
    "RecommendationPolicyV1",
    "RecommendationPolicyV1Config",
    "RecommendationScore",
    "ReviewQueueItem",
    "SystemPlanExpirationRequest",
    "AnswerGraderTool",
    "ErrorAnalyzerTool",
    "KnowledgeMapperTool",
    "MemoryReaderTool",
    "MemoryWriterTool",
    "QuestionRetrieverTool",
    "RecommendationTool",
    "UserPlanCancellationRequest",
    "WorkflowEventSink",
    "checkpoint_key_for_context",
    "build_learning_profile",
    "recognize_correction_intent",
    "recognize_plan_cancellation_intent",
    "stage07_practice_questions",
    "stage07_question",
]
