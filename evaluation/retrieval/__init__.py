"""Independent semantic-retrieval-v2 contracts and metrics for ExamMem."""

from .contracts import (
    CorpusMemoryRecord,
    RetrievalDataset,
    RetrievalDatasetFileRecord,
    RetrievalDatasetManifest,
    RetrievalQuery,
    RetrievalSplit,
    canonical_embedding_text,
    canonical_sha256,
)
from .metrics import (
    EXAM_MEM_METRIC_CATALOG,
    RetrievalObservation,
    StorageWriteObservation,
    compute_retrieval_metrics,
    compute_storage_metrics,
    metric_catalog_sha256,
)

__all__ = [
    "CorpusMemoryRecord",
    "EXAM_MEM_METRIC_CATALOG",
    "RetrievalDataset",
    "RetrievalDatasetFileRecord",
    "RetrievalDatasetManifest",
    "RetrievalQuery",
    "RetrievalSplit",
    "RetrievalObservation",
    "StorageWriteObservation",
    "canonical_embedding_text",
    "canonical_sha256",
    "compute_retrieval_metrics",
    "compute_storage_metrics",
    "metric_catalog_sha256",
]
