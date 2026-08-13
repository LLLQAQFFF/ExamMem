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
    "LEARNING_MEMORY_EMBEDDING_DIMENSION",
    "LearningEventRepository",
    "LifecycleAuditRepository",
    "LearningMemoryRepository",
    "MemoryProvenanceValidationError",
    "MemoryVersionConflict",
    "PostgresLearningMemoryRepository",
    "PostgresLifecycleAuditRepository",
    "PostgresLearningEventRepository",
    "PostgresPracticeCheckpointRepository",
    "PostgresPracticeTraceRepository",
    "PostgresBaselineFactRepository",
    "PostgresStudentModelRepository",
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
    "load_database_settings",
    "metadata",
]
