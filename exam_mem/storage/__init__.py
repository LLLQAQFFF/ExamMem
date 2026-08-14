"""PostgreSQL storage metadata for ExamMem Learning Memory."""

from importlib import import_module

from .audit_repository import (
    AuditLinkError,
    AuditRepositoryInvariantError,
    LifecycleAuditRepository,
    PostgresLifecycleAuditRepository,
)
from .baseline_fact_repository import (
    BaselineFactAppendResult,
    BaselineFactRecord,
    BaselineFactRepository,
    BaselineFactSourceEventError,
    PostgresBaselineFactRepository,
)
from .config import DatabaseSettings, load_database_settings
from .event_repository import (
    AppendResult,
    AppendStatus,
    EventLookupError,
    EventTargetValidationError,
    EventWatermarkError,
    LearningEventRepository,
    PostgresLearningEventRepository,
)
from .memory_repository import (
    LearningMemoryRepository,
    MemoryProvenanceValidationError,
    MemoryVersionConflict,
    PostgresLearningMemoryRepository,
    RepositoryInvariantError,
)
from .models import LEARNING_MEMORY_EMBEDDING_DIMENSION, metadata
from .rebuild import (
    DEFAULT_EVENT_PAGE_SIZE,
    STUDENT_MODEL_PROJECTION_VERSION,
    RebuildInputError,
    StudentModelRebuildResult,
    StudentModelRebuildService,
)
from .student_model_repository import (
    PostgresStudentModelRepository,
    ProjectionConflict,
    StudentModelRepository,
    StudentModelSnapshot,
)

_LAZY_EXPORTS = {
    "AssessmentConflict": (
        ".assessment_repository",
        "AssessmentConflict",
    ),
    "AssessmentNotFound": (
        ".assessment_repository",
        "AssessmentNotFound",
    ),
    "PostgresAssessmentRepository": (
        ".assessment_repository",
        "PostgresAssessmentRepository",
    ),
    "GradeReviewAppendResult": (
        ".grade_review_repository",
        "GradeReviewAppendResult",
    ),
    "PostgresGradeReviewRepository": (
        ".grade_review_repository",
        "PostgresGradeReviewRepository",
    ),
    "PostgresExamProductRepository": (
        ".product_repository",
        "PostgresExamProductRepository",
    ),
    "PostgresLearningArchiveRepository": (
        ".learning_archive_repository",
        "PostgresLearningArchiveRepository",
    ),
    "LearningObservationConflict": (
        ".learning_observation_repository",
        "LearningObservationConflict",
    ),
    "PostgresLearningObservationRepository": (
        ".learning_observation_repository",
        "PostgresLearningObservationRepository",
    ),
    "CommittedPostgresPracticeCheckpointRepository": (
        ".practice_checkpoint_repository",
        "CommittedPostgresPracticeCheckpointRepository",
    ),
    "CommittedPostgresPracticeTraceRepository": (
        ".practice_trace_repository",
        "CommittedPostgresPracticeTraceRepository",
    ),
    "PostgresPracticeCheckpointRepository": (
        ".practice_checkpoint_repository",
        "PostgresPracticeCheckpointRepository",
    ),
    "PracticeCheckpointAppendResult": (
        ".practice_checkpoint_repository",
        "PracticeCheckpointAppendResult",
    ),
    "PracticeCheckpointIdentityError": (
        ".practice_checkpoint_repository",
        "PracticeCheckpointIdentityError",
    ),
    "PracticeCheckpointRecord": (
        ".practice_checkpoint_repository",
        "PracticeCheckpointRecord",
    ),
    "PracticeCheckpointRepository": (
        ".practice_checkpoint_repository",
        "PracticeCheckpointRepository",
    ),
    "PostgresPracticeTraceRepository": (
        ".practice_trace_repository",
        "PostgresPracticeTraceRepository",
    ),
    "PostgresStudyPlanRepository": (
        ".study_plan_repository",
        "PostgresStudyPlanRepository",
    ),
    "StudyPlanConflict": (
        ".study_plan_repository",
        "StudyPlanConflict",
    ),
    "StudyPlanNotFound": (
        ".study_plan_repository",
        "StudyPlanNotFound",
    ),
    "PracticeTraceAppendResult": (
        ".practice_trace_repository",
        "PracticeTraceAppendResult",
    ),
    "PracticeTraceRepository": (
        ".practice_trace_repository",
        "PracticeTraceRepository",
    ),
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
    "AssessmentConflict",
    "AssessmentNotFound",
    "AuditLinkError",
    "AuditRepositoryInvariantError",
    "AppendResult",
    "AppendStatus",
    "BaselineFactAppendResult",
    "BaselineFactRecord",
    "BaselineFactRepository",
    "BaselineFactSourceEventError",
    "CommittedPostgresPracticeCheckpointRepository",
    "CommittedPostgresPracticeTraceRepository",
    "DEFAULT_EVENT_PAGE_SIZE",
    "DatabaseSettings",
    "EventTargetValidationError",
    "EventLookupError",
    "EventWatermarkError",
    "GradeReviewAppendResult",
    "LEARNING_MEMORY_EMBEDDING_DIMENSION",
    "LearningEventRepository",
    "LifecycleAuditRepository",
    "LearningMemoryRepository",
    "LearningObservationConflict",
    "MemoryProvenanceValidationError",
    "MemoryVersionConflict",
    "PostgresLearningMemoryRepository",
    "PostgresLearningArchiveRepository",
    "PostgresLearningObservationRepository",
    "PostgresGradeReviewRepository",
    "PostgresExamProductRepository",
    "PostgresLifecycleAuditRepository",
    "PostgresLearningEventRepository",
    "PostgresPracticeCheckpointRepository",
    "PostgresPracticeTraceRepository",
    "PostgresBaselineFactRepository",
    "PostgresAssessmentRepository",
    "PostgresStudentModelRepository",
    "PostgresStudyPlanRepository",
    "ProjectionConflict",
    "PracticeCheckpointAppendResult",
    "PracticeCheckpointIdentityError",
    "PracticeCheckpointRecord",
    "PracticeCheckpointRepository",
    "PracticeTraceAppendResult",
    "PracticeTraceRepository",
    "RebuildInputError",
    "RepositoryInvariantError",
    "STUDENT_MODEL_PROJECTION_VERSION",
    "StudentModelRebuildResult",
    "StudentModelRebuildService",
    "StudentModelRepository",
    "StudentModelSnapshot",
    "StudyPlanConflict",
    "StudyPlanNotFound",
    "load_database_settings",
    "metadata",
]
