from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any
import unittest

from pydantic import ValidationError

from evaluation.retrieval.contracts import (
    RETRIEVAL_PROTOCOL_VERSION,
    CorpusMemoryRecord,
    RetrievalDataset,
    RetrievalDatasetManifest,
    canonical_embedding_text,
    manifest_records_hash,
)

NOW = datetime(2026, 8, 25, tzinfo=timezone.utc)


def _event(index: int, *, user_id: str = "learner_a") -> dict[str, Any]:
    return {
        "event_id": f"event_{user_id}_{index:03d}",
        "idempotency_key": f"idem_{user_id}_{index:03d}",
        "event_type": "answer_attempt",
        "context": {
            "user_id": user_id,
            "exam_id": "postgraduate_entrance_exam",
            "subject_id": "math_1",
        },
        "session_id": "session_001",
        "question_id": f"question_{index:03d}",
        "knowledge_point_ids": [f"math1.linear_algebra.point_{index:03d}"],
        "difficulty": 0.5,
        "answer_correct": False,
        "error_type": "concept_confusion",
        "error_detail": "混淆了相邻概念。",
        "evidence_quality": {
            "confidence": 1.0,
            "is_temporary_exception": False,
            "reasons": [],
        },
        "correction": None,
        "plan_transition": None,
        "occurred_at": (NOW + timedelta(seconds=index)).isoformat(),
    }


def _record(
    write_index: int,
    *,
    user_id: str = "learner_a",
    point_index: int | None = None,
    version: int = 1,
    lifecycle_state: str = "active",
    valid_to: datetime | None = None,
    superseded_by: str | None = None,
    events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    point_index = write_index if point_index is None else point_index
    events = events or [_event(write_index, user_id=user_id)]
    memory_id = f"memory_{user_id}_{point_index:03d}_v{version}"
    memory = {
        "memory_id": memory_id,
        "scope": {
            "user_id": user_id,
            "exam_id": "postgraduate_entrance_exam",
            "subject_id": "math_1",
            "memory_namespace": "mastery",
        },
        "slot_key": f"mastery:math1.linear_algebra.point_{point_index:03d}",
        "value": {"type": "mastery", "level": "low", "score": 0.1},
        "confidence": 0.9,
        "evidence_count": len(events),
        "lifecycle_state": lifecycle_state,
        "version": version,
        "valid_from": (NOW + timedelta(seconds=write_index)).isoformat(),
        "valid_to": None if valid_to is None else valid_to.isoformat(),
        "superseded_by": superseded_by,
        "provenance": [event["event_id"] for event in events],
    }
    parsed = CorpusMemoryRecord.model_validate(
        {
            "write_index": write_index,
            "memory": memory,
            "provenance_events": events,
            "embedding_text": canonical_embedding_text_from_payload(memory),
            "contested_group_id": None,
            "provenance_relations": {event["event_id"]: "created_by" for event in events},
        }
    )
    return parsed.model_dump(mode="json")


def canonical_embedding_text_from_payload(memory: dict[str, Any]) -> str:
    from exam_mem.contracts import LearningMemory

    return canonical_embedding_text(LearningMemory.model_validate(memory))


def _dataset_payload() -> dict[str, Any]:
    corpus = [_record(index) for index in range(50)]

    predecessor_event = _event(50)
    successor_event = _event(51)
    successor_id = "memory_learner_a_050_v2"
    corpus.append(
        _record(
            50,
            point_index=50,
            lifecycle_state="archived",
            valid_to=NOW + timedelta(seconds=51),
            superseded_by=successor_id,
            events=[predecessor_event],
        )
    )
    corpus.append(
        _record(
            51,
            point_index=50,
            version=2,
            events=[predecessor_event, successor_event],
        )
    )
    corpus.append(_record(52, user_id="learner_b", point_index=0))

    common_query = {
        "scope": {
            "user_id": "learner_a",
            "exam_id": "postgraduate_entrance_exam",
            "subject_id": "math_1",
            "memory_namespace": "mastery",
        },
        "top_k": 5,
        "hard_negative_memory_ids": [f"memory_learner_a_{index:03d}_v1" for index in range(1, 11)],
        "must_not_return_memory_ids": [
            "memory_learner_a_050_v1",
            "memory_learner_b_000_v1",
        ],
    }
    return {
        "protocol_version": RETRIEVAL_PROTOCOL_VERSION,
        "dataset_version": "exam_mem_semantic_retrieval_v2",
        "split": "dev",
        "seed": 20260825,
        "policy_version": "retrieval_dataset_v2",
        "embedding_dimension": 1024,
        "document_input_type": "search_document",
        "query_input_type": "search_query",
        "corpus": corpus,
        "queries": [
            {
                **common_query,
                "query_id": "query_answerable_001",
                "query_family": "matrix_rank_confusion",
                "text": "我对第一个知识点的掌握情况怎么样？",
                "relevant_memory_ids": ["memory_learner_a_000_v1"],
                "relevance_grades": {"memory_learner_a_000_v1": 3},
                "expected_no_answer": False,
            },
            {
                **common_query,
                "query_id": "query_no_answer_001",
                "query_family": "unseen_topic_abstention",
                "text": "我有没有学习过复变函数留数定理？",
                "relevant_memory_ids": [],
                "relevance_grades": {},
                "expected_no_answer": True,
            },
        ],
    }


class RetrievalContractTests(unittest.TestCase):
    def test_dataset_reuses_production_memory_and_pins_runtime_embedding_boundary(self) -> None:
        dataset = RetrievalDataset.model_validate(_dataset_payload())

        record = dataset.corpus[0]
        self.assertEqual(record.memory.provenance, [record.provenance_events[0].event_id])
        self.assertEqual(record.embedding_text, canonical_embedding_text(record.memory))
        self.assertEqual(dataset.embedding_dimension, 1024)
        self.assertEqual(dataset.document_input_type, "search_document")
        self.assertEqual(dataset.query_input_type, "search_query")
        self.assertEqual(len(dataset.corpus), 53)
        self.assertEqual(len(dataset.canonical_hash()), 64)

    def test_corpus_rejects_noncanonical_embedding_and_incomplete_relation(self) -> None:
        payload = _record(0)
        payload["embedding_text"] = "用户原始文本不属于 L2 embedding contract"
        payload["provenance_relations"] = {}
        with self.assertRaisesRegex(ValidationError, "provenance_relations"):
            CorpusMemoryRecord.model_validate(payload)

        payload = _record(0)
        payload["embedding_text"] = "{}"
        with self.assertRaisesRegex(ValidationError, "canonical slot_key"):
            CorpusMemoryRecord.model_validate(payload)

    def test_dataset_separates_hard_negatives_from_safety_forbidden_ids(self) -> None:
        payload = _dataset_payload()
        payload["corpus"].append(_record(53, user_id="learner_c", point_index=0))
        payload["queries"][0]["hard_negative_memory_ids"][0] = "memory_learner_c_000_v1"
        with self.assertRaisesRegex(ValidationError, "hard negatives must be retrievable"):
            RetrievalDataset.model_validate(payload)

        payload = _dataset_payload()
        payload["queries"][0]["top_k"] = 6
        with self.assertRaisesRegex(ValidationError, "at least 60"):
            RetrievalDataset.model_validate(payload)

    def test_dataset_requires_terminal_intervals_and_resolved_successor(self) -> None:
        payload = _dataset_payload()
        payload["corpus"][50]["memory"]["valid_to"] = None
        with self.assertRaisesRegex(ValidationError, "terminal memories require valid_to"):
            RetrievalDataset.model_validate(payload)

        payload = _dataset_payload()
        payload["corpus"][50]["memory"]["superseded_by"] = "missing_successor"
        with self.assertRaisesRegex(ValidationError, "superseded_by must resolve"):
            RetrievalDataset.model_validate(payload)

    def test_manifest_pins_hashes_and_prevents_query_family_leakage(self) -> None:
        digest_a = "a" * 64
        digest_b = "b" * 64
        records = [
            {
                "path": "dev.json",
                "split": "dev",
                "dataset_sha256": digest_a,
                "corpus_count": 100,
                "query_count": 20,
                "query_family_ids": ["family_dev"],
            },
            {
                "path": "test.json",
                "split": "test",
                "dataset_sha256": digest_b,
                "corpus_count": 200,
                "query_count": 40,
                "query_family_ids": ["family_test"],
            },
        ]
        from evaluation.retrieval.contracts import RetrievalDatasetFileRecord

        parsed_records = [RetrievalDatasetFileRecord.model_validate(record) for record in records]
        payload = {
            "protocol_version": RETRIEVAL_PROTOCOL_VERSION,
            "dataset_version": "exam_mem_semantic_retrieval_v2",
            "seed": 20260825,
            "generated_at": NOW.isoformat(),
            "records": records,
            "aggregate_sha256": manifest_records_hash(parsed_records),
            "frozen_test_sha256": digest_b,
            "metric_catalog_sha256": "c" * 64,
            "construction_notes": ["人工 Gold 与 corpus 生成逻辑分离。"],
        }
        manifest = RetrievalDatasetManifest.model_validate(payload)
        self.assertEqual(manifest.frozen_test_sha256, digest_b)

        leaked = deepcopy(payload)
        leaked["records"][1]["query_family_ids"] = ["family_dev"]
        leaked_records = [
            RetrievalDatasetFileRecord.model_validate(record) for record in leaked["records"]
        ]
        leaked["aggregate_sha256"] = manifest_records_hash(leaked_records)
        with self.assertRaisesRegex(ValidationError, "query families must be disjoint"):
            RetrievalDatasetManifest.model_validate(leaked)
