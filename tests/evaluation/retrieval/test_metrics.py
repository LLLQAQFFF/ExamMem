from __future__ import annotations

import unittest

from evaluation.contracts.protocol import REQUIRED_METRIC_IDS
from evaluation.retrieval.contracts import RetrievalQuery
from evaluation.retrieval.metrics import (
    EXAM_MEM_METRIC_CATALOG,
    RetrievalObservation,
    StorageWriteObservation,
    compute_retrieval_metrics,
    compute_storage_metrics,
    metric_catalog_sha256,
)

SCOPE = {
    "user_id": "learner_a",
    "exam_id": "postgraduate_entrance_exam",
    "subject_id": "math_1",
    "memory_namespace": "mastery",
}


def _scores(result: object) -> dict[str, float | None]:
    return {score.metric_id: score.value for score in result.scores}


def _check_metric_catalog_preserves_v1_and_preregisters_storage_and_retrieval_v2() -> None:
    metric_ids = [definition.metric_id for definition in EXAM_MEM_METRIC_CATALOG]
    required_v2 = {
        "storage.acceptance_rate",
        "storage.idempotency_rate",
        "storage.invariant_rejection_rate",
        "storage.vector_validity_rate",
        "storage.provenance_integrity_rate",
        "retrieval.recall_at_k",
        "retrieval.precision_at_k",
        "retrieval.hit_at_k",
        "retrieval.mrr",
        "retrieval.ndcg_at_k",
        "retrieval.no_answer_accuracy",
        "retrieval.hard_negative_hit_rate",
        "retrieval.relevant_hard_negative_pairwise_accuracy",
        "retrieval.archived_hit_rate",
        "retrieval.ann_recall_at_k",
        "engineering.production_p95_latency_ms",
        "engineering.exact_p95_latency_ms",
    }

    assert len(metric_ids) == len(set(metric_ids))
    assert REQUIRED_METRIC_IDS <= set(metric_ids)
    assert required_v2 <= set(metric_ids)
    assert all(definition.target is not None for definition in EXAM_MEM_METRIC_CATALOG)
    assert len(metric_catalog_sha256()) == 64

    precision = next(
        definition
        for definition in EXAM_MEM_METRIC_CATALOG
        if definition.metric_id == "retrieval.precision_at_k"
    )
    assert precision.target.threshold <= 0.2


def _check_storage_metrics_keep_dimensions_separate() -> None:
    observations = [
        StorageWriteObservation.model_validate(
            {
                "trial_id": "primary_001",
                "trial_kind": "primary_write",
                "write_index": 0,
                "memory_id": "memory_001",
                "provenance_event_ids": ["event_001"],
                "accepted": True,
                "row_count_delta": 1,
                "round_trip_equal": True,
                "stored_provenance_event_ids": ["event_001"],
                "stored_embedding_dimensions": 1024,
                "stored_embedding_all_finite": True,
                "stored_embedding_nonzero": True,
                "latency_ms": 8.0,
            }
        ),
        StorageWriteObservation.model_validate(
            {
                "trial_id": "replay_001",
                "trial_kind": "idempotent_replay",
                "write_index": 0,
                "memory_id": "memory_001",
                "provenance_event_ids": ["event_001"],
                "accepted": True,
                "row_count_delta": 0,
                "round_trip_equal": True,
                "latency_ms": 3.0,
            }
        ),
        StorageWriteObservation.model_validate(
            {
                "trial_id": "invalid_scope_001",
                "trial_kind": "invariant_rejection",
                "write_index": 1,
                "memory_id": "memory_invalid",
                "provenance_event_ids": ["event_cross_scope"],
                "accepted": False,
                "row_count_delta": 0,
                "error_type": "MemoryProvenanceValidationError",
                "constraint_name": "same_context_provenance",
                "latency_ms": 2.0,
            }
        ),
    ]

    scores = {score.metric_id: score.value for score in compute_storage_metrics(observations)}
    assert scores == {
        "storage.acceptance_rate": 1.0,
        "storage.idempotency_rate": 1.0,
        "storage.invariant_rejection_rate": 1.0,
        "storage.vector_validity_rate": 1.0,
        "storage.provenance_integrity_rate": 1.0,
    }


def _check_storage_observations_are_not_tautological() -> None:
    observations = [
        StorageWriteObservation.model_validate(
            {
                "trial_id": "primary_failed",
                "trial_kind": "primary_write",
                "write_index": 0,
                "memory_id": "memory_primary_failed",
                "provenance_event_ids": ["event_primary_failed"],
                "accepted": False,
                "row_count_delta": 0,
                "error_type": "DatabaseError",
                "latency_ms": 4.0,
            }
        ),
        StorageWriteObservation.model_validate(
            {
                "trial_id": "replay_failed",
                "trial_kind": "idempotent_replay",
                "write_index": 0,
                "memory_id": "memory_replay_failed",
                "provenance_event_ids": ["event_replay_failed"],
                "accepted": False,
                "row_count_delta": 0,
                "error_type": "ReplayConflict",
                "latency_ms": 2.0,
            }
        ),
        StorageWriteObservation.model_validate(
            {
                "trial_id": "invalid_accepted",
                "trial_kind": "invariant_rejection",
                "write_index": 1,
                "memory_id": "memory_invalid_accepted",
                "provenance_event_ids": ["event_invalid_accepted"],
                "accepted": True,
                "row_count_delta": 1,
                "latency_ms": 3.0,
            }
        ),
    ]

    scores = {score.metric_id: score.value for score in compute_storage_metrics(observations)}
    assert scores["storage.acceptance_rate"] == 0.0
    assert scores["storage.idempotency_rate"] == 0.0
    assert scores["storage.invariant_rejection_rate"] == 0.0
    assert scores["storage.vector_validity_rate"] is None
    assert scores["storage.provenance_integrity_rate"] is None


def _check_invariant_rejection_requires_zero_row_growth() -> None:
    observation = StorageWriteObservation.model_validate(
        {
            "trial_id": "invalid_with_side_effect",
            "trial_kind": "invariant_rejection",
            "write_index": 0,
            "memory_id": "memory_invalid_with_side_effect",
            "provenance_event_ids": ["event_invalid_with_side_effect"],
            "accepted": False,
            "row_count_delta": 1,
            "error_type": "DatabaseError",
            "latency_ms": 2.0,
        }
    )

    scores = {score.metric_id: score.value for score in compute_storage_metrics([observation])}
    assert scores["storage.invariant_rejection_rate"] == 0.0


def _queries() -> list[RetrievalQuery]:
    return [
        RetrievalQuery.model_validate(
            {
                "query_id": "answerable",
                "query_family": "family_answerable",
                "text": "矩阵秩的薄弱点",
                "scope": SCOPE,
                "top_k": 5,
                "relevant_memory_ids": ["relevant"],
                "relevance_grades": {"relevant": 3},
                "hard_negative_memory_ids": ["hard_1", "hard_2"],
                "must_not_return_memory_ids": ["archived", "cross_scope"],
                "expected_no_answer": False,
            }
        ),
        RetrievalQuery.model_validate(
            {
                "query_id": "no_answer",
                "query_family": "family_no_answer",
                "text": "从未学习过的主题",
                "scope": SCOPE,
                "top_k": 5,
                "relevant_memory_ids": [],
                "relevance_grades": {},
                "hard_negative_memory_ids": ["hard_3", "hard_4"],
                "must_not_return_memory_ids": ["archived", "cross_scope"],
                "expected_no_answer": True,
            }
        ),
    ]


def _observations(*, hnsw_used: bool = True) -> list[RetrievalObservation]:
    return [
        RetrievalObservation.model_validate(
            {
                "query_id": "answerable",
                "production_result_ids": ["relevant", "archived", "hard_1"],
                "exact_result_ids": ["relevant", "archived", "hard_1"],
                "production_distances": [0.1, 0.2, 0.3],
                "exact_distances": [0.1, 0.2, 0.3],
                "judged_memory_distances": {"relevant": 0.1, "hard_1": 0.3, "hard_2": 0.4},
                "archived_or_invalidated_result_ids": ["archived"],
                "cross_scope_result_ids": [],
                "production_latency_ms": 5.0,
                "exact_latency_ms": 20.0,
                "candidate_count": 50,
                "hnsw_index_used": hnsw_used,
                "execution_plan": {"Node Type": "Index Scan"},
            }
        ),
        RetrievalObservation.model_validate(
            {
                "query_id": "no_answer",
                "production_result_ids": [],
                "exact_result_ids": [],
                "judged_memory_distances": {"hard_3": 0.5, "hard_4": 0.6},
                "archived_or_invalidated_result_ids": [],
                "cross_scope_result_ids": [],
                "production_latency_ms": 8.0,
                "exact_latency_ms": 25.0,
                "candidate_count": 50,
                "hnsw_index_used": hnsw_used,
                "execution_plan": {"Node Type": "Index Scan"},
            }
        ),
    ]


def _check_retrieval_metrics_score_independent_dimensions() -> None:
    report = compute_retrieval_metrics(_queries(), _observations())
    scores = _scores(report)

    assert scores["retrieval.recall_at_k"] == 1.0
    assert abs(scores["retrieval.precision_at_k"] - 0.2) < 1e-12
    assert scores["retrieval.hit_at_k"] == 1.0
    assert scores["retrieval.mrr"] == 1.0
    assert scores["retrieval.ndcg_at_k"] == 1.0
    assert scores["retrieval.no_answer_accuracy"] == 1.0
    assert scores["retrieval.hard_negative_hit_rate"] == 0.5
    assert scores["retrieval.relevant_hard_negative_pairwise_accuracy"] == 1.0
    assert abs(scores["retrieval.archived_hit_rate"] - (1 / 3)) < 1e-12
    assert scores["isolation.cross_scope_leakage_rate"] == 0.0
    assert scores["retrieval.ann_recall_at_k"] == 1.0
    assert report.production_latency.p95_ms == 8.0
    assert report.exact_latency.p95_ms == 25.0


def _check_ann_metric_unmeasured_without_hnsw() -> None:
    report = compute_retrieval_metrics(_queries(), _observations(hnsw_used=False))
    ann = next(score for score in report.scores if score.metric_id == "retrieval.ann_recall_at_k")

    assert ann.value is None
    assert ann.sample_count == 0
    assert ann.reason == "no eligible observations"


class RetrievalMetricTests(unittest.TestCase):
    def test_metric_catalog(self) -> None:
        _check_metric_catalog_preserves_v1_and_preregisters_storage_and_retrieval_v2()

    def test_storage_dimensions(self) -> None:
        _check_storage_metrics_keep_dimensions_separate()

    def test_storage_observations_are_not_tautological(self) -> None:
        _check_storage_observations_are_not_tautological()

    def test_invariant_rejection_requires_zero_row_growth(self) -> None:
        _check_invariant_rejection_requires_zero_row_growth()

    def test_retrieval_metric_dimensions(self) -> None:
        _check_retrieval_metrics_score_independent_dimensions()

    def test_ann_availability(self) -> None:
        _check_ann_metric_unmeasured_without_hnsw()
