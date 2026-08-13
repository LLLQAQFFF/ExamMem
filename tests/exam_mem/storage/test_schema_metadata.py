from __future__ import annotations

import importlib

from pgvector.sqlalchemy import Vector
import pytest
from sqlalchemy import CheckConstraint, PrimaryKeyConstraint, UniqueConstraint
from sqlalchemy.schema import MetaData, Table

pytestmark = [pytest.mark.database, pytest.mark.schema]

EXPECTED_TABLES = {
    "baseline_memory_facts",
    "event_correction_targets",
    "event_plan_transition_targets",
    "learning_events",
    "learning_memories",
    "lifecycle_decisions",
    "memory_change_log",
    "memory_provenance",
    "practice_trace_spans",
    "practice_workflow_checkpoints",
    "student_model_snapshots",
}

EXPECTED_COLUMNS = {
    "baseline_memory_facts": {
        "backend_mode",
        "event_id",
        "user_id",
        "exam_id",
        "subject_id",
        "memory_namespace",
        "slot_key",
        "value",
        "evidence",
        "content_embedding",
        "created_at",
    },
    "learning_events": {
        "event_id",
        "idempotency_key",
        "user_id",
        "exam_id",
        "subject_id",
        "event_type",
        "session_id",
        "question_id",
        "knowledge_point_ids",
        "primary_knowledge_point_id",
        "difficulty",
        "answer_correct",
        "error_type",
        "error_detail",
        "evidence_quality",
        "correction_source",
        "correction_statement",
        "plan_transition_status",
        "plan_transition_source",
        "plan_transition_reason",
        "raw_payload",
        "occurred_at",
        "created_at",
        "trace_id",
        "schema_version",
    },
    "learning_memories": {
        "memory_id",
        "user_id",
        "exam_id",
        "subject_id",
        "memory_namespace",
        "slot_key",
        "value",
        "confidence",
        "evidence_count",
        "lifecycle_state",
        "version",
        "row_version",
        "valid_from",
        "valid_to",
        "superseded_by",
        "contested_group_id",
        "content_embedding",
        "policy_version",
        "created_at",
        "updated_at",
    },
    "event_correction_targets": {"event_id", "memory_id", "created_at"},
    "event_plan_transition_targets": {"event_id", "memory_id", "created_at"},
    "memory_provenance": {"memory_id", "event_id", "relation_type", "created_at"},
    "student_model_snapshots": {
        "snapshot_id",
        "user_id",
        "exam_id",
        "subject_id",
        "model",
        "projection_version",
        "source_event_watermark",
        "source_memory_watermark",
        "created_at",
    },
    "lifecycle_decisions": {
        "decision_id",
        "trace_id",
        "event_id",
        "input_summary",
        "candidate_memory_ids",
        "operation",
        "reason",
        "confidence",
        "policy_version",
        "created_at",
    },
    "memory_change_log": {
        "change_id",
        "decision_id",
        "before_state",
        "after_state",
        "apply_state",
        "memory_id",
        "expected_row_version",
        "actual_row_version",
        "error_code",
        "trace_id",
        "created_at",
    },
    "practice_workflow_checkpoints": {
        "practice_session_id",
        "checkpoint_key",
        "user_id",
        "exam_id",
        "subject_id",
        "trace_id",
        "step_state",
        "payload",
        "row_version",
        "created_at",
        "updated_at",
    },
    "practice_trace_spans": {
        "trace_id",
        "step_id",
        "span_name",
        "status",
        "input_summary",
        "output_summary",
        "versions",
        "started_at",
        "completed_at",
        "duration_ms",
        "retry_count",
        "llm_calls",
        "input_tokens",
        "output_tokens",
        "error_code",
        "related_record_ids",
        "created_at",
    },
}

SCOPE_COLUMNS = ("user_id", "exam_id", "subject_id", "memory_namespace")


def _load_metadata() -> MetaData:
    models = importlib.import_module("exam_mem.storage.models")
    metadata = getattr(models, "metadata")
    assert isinstance(metadata, MetaData)
    return metadata


def _column_names(table: Table) -> set[str]:
    return {column.name for column in table.columns}


def _unique_column_sets(table: Table) -> set[tuple[str, ...]]:
    return {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def _primary_key_columns(table: Table) -> tuple[str, ...]:
    constraint = next(item for item in table.constraints if isinstance(item, PrimaryKeyConstraint))
    return tuple(column.name for column in constraint.columns)


def _foreign_key_targets(table: Table) -> set[tuple[str, str]]:
    return {
        (foreign_key.parent.name, foreign_key.target_fullname) for foreign_key in table.foreign_keys
    }


def test_stage07_metadata_adds_only_frozen_practice_runtime_tables() -> None:
    metadata = _load_metadata()

    assert set(metadata.tables) == EXPECTED_TABLES
    for table_name, expected_columns in EXPECTED_COLUMNS.items():
        assert _column_names(metadata.tables[table_name]) == expected_columns


def test_stage07_metadata_encodes_checkpoint_cas_and_append_only_trace_identity() -> None:
    metadata = _load_metadata()
    checkpoints = metadata.tables["practice_workflow_checkpoints"]
    traces = metadata.tables["practice_trace_spans"]

    assert _primary_key_columns(checkpoints) == (
        "practice_session_id",
        "checkpoint_key",
    )
    assert _primary_key_columns(traces) == ("trace_id", "step_id")
    assert checkpoints.c.row_version.nullable is False
    assert any(
        tuple(column.name for column in index.columns) == ("trace_id",)
        for index in checkpoints.indexes
    )


def test_stage05_metadata_encodes_idempotency_versions_and_target_relations() -> None:
    metadata = _load_metadata()
    events = metadata.tables["learning_events"]
    memories = metadata.tables["learning_memories"]
    correction_targets = metadata.tables["event_correction_targets"]
    plan_targets = metadata.tables["event_plan_transition_targets"]
    provenance = metadata.tables["memory_provenance"]

    assert ("user_id", "idempotency_key") in _unique_column_sets(events)
    assert (*SCOPE_COLUMNS, "slot_key", "version") in _unique_column_sets(memories)

    assert _primary_key_columns(correction_targets) == ("event_id", "memory_id")
    assert ("event_id",) in _unique_column_sets(plan_targets)
    assert _primary_key_columns(provenance) == (
        "memory_id",
        "event_id",
        "relation_type",
    )

    assert _foreign_key_targets(correction_targets) == {
        ("event_id", "learning_events.event_id"),
        ("memory_id", "learning_memories.memory_id"),
    }
    assert _foreign_key_targets(plan_targets) == {
        ("event_id", "learning_events.event_id"),
        ("memory_id", "learning_memories.memory_id"),
    }
    assert _foreign_key_targets(provenance) == {
        ("event_id", "learning_events.event_id"),
        ("memory_id", "learning_memories.memory_id"),
    }
    assert ("superseded_by", "learning_memories.memory_id") in _foreign_key_targets(memories)
    superseded_by = next(
        foreign_key
        for foreign_key in memories.foreign_keys
        if foreign_key.parent.name == "superseded_by"
    )
    assert superseded_by.constraint.deferrable is True
    assert superseded_by.constraint.initially == "DEFERRED"


def test_stage06_metadata_links_decisions_changes_events_and_memories() -> None:
    metadata = _load_metadata()
    decisions = metadata.tables["lifecycle_decisions"]
    changes = metadata.tables["memory_change_log"]

    assert _foreign_key_targets(decisions) == {
        ("event_id", "learning_events.event_id"),
    }
    assert _foreign_key_targets(changes) == {
        ("decision_id", "lifecycle_decisions.decision_id"),
        ("memory_id", "learning_memories.memory_id"),
    }
    assert changes.c.before_state.type.none_as_null is True
    assert changes.c.after_state.type.none_as_null is True
    assert any(
        tuple(column.name for column in index.columns) == ("trace_id", "created_at", "decision_id")
        for index in decisions.indexes
    )
    assert any(
        tuple(column.name for column in index.columns) == ("decision_id", "created_at", "change_id")
        for index in changes.indexes
    )


def test_stage05_metadata_encodes_scoped_candidate_and_single_active_indexes() -> None:
    memories = _load_metadata().tables["learning_memories"]
    indexes = list(memories.indexes)

    assert any(
        tuple(column.name for column in index.columns)
        == (*SCOPE_COLUMNS, "slot_key", "lifecycle_state")
        and not index.unique
        for index in indexes
    )
    assert any(
        tuple(column.name for column in index.columns) == (*SCOPE_COLUMNS, "slot_key")
        and index.unique
        and index.dialect_options["postgresql"].get("where") is not None
        for index in indexes
    )


def test_stage05_metadata_uses_the_measured_embedding_dimension() -> None:
    memories = _load_metadata().tables["learning_memories"]
    embedding = memories.c.content_embedding

    assert isinstance(embedding.type, Vector)
    assert embedding.type.dim == 1024
    assert embedding.nullable is True
    assert any(
        tuple(column.name for column in index.columns) == ("content_embedding",)
        and index.dialect_options["postgresql"].get("using") == "hnsw"
        and index.dialect_options["postgresql"].get("ops")
        == {"content_embedding": "vector_cosine_ops"}
        and index.dialect_options["postgresql"].get("where") is not None
        for index in memories.indexes
    )


def test_stage07_baseline_fact_metadata_is_scoped_append_only_storage() -> None:
    facts = _load_metadata().tables["baseline_memory_facts"]

    assert _primary_key_columns(facts) == ("backend_mode", "event_id", "slot_key")
    assert _foreign_key_targets(facts) == {
        ("event_id", "learning_events.event_id"),
    }
    assert {
        constraint.name
        for constraint in facts.constraints
        if isinstance(constraint, CheckConstraint)
    } >= {
        "ck_baseline_memory_facts_backend_mode",
        "ck_baseline_memory_facts_namespace",
        "ck_baseline_memory_facts_value_namespace",
        "ck_baseline_memory_facts_evidence_object",
        "ck_baseline_memory_facts_embedding_mode",
    }
    assert not {
        "lifecycle_state",
        "version",
        "row_version",
        "valid_from",
        "valid_to",
        "superseded_by",
        "policy_version",
    } & _column_names(facts)
    assert any(
        tuple(column.name for column in index.columns)
        == ("backend_mode", *SCOPE_COLUMNS, "slot_key", "created_at", "event_id")
        and not index.unique
        for index in facts.indexes
    )


def test_stage07_vector_baseline_reuses_the_frozen_embedding_dimension() -> None:
    facts = _load_metadata().tables["baseline_memory_facts"]
    embedding = facts.c.content_embedding

    assert isinstance(embedding.type, Vector)
    assert embedding.type.dim == 1024
    assert embedding.nullable is True
    assert any(
        tuple(column.name for column in index.columns) == ("content_embedding",)
        and index.dialect_options["postgresql"].get("using") == "hnsw"
        and index.dialect_options["postgresql"].get("ops")
        == {"content_embedding": "vector_cosine_ops"}
        and index.dialect_options["postgresql"].get("where") is not None
        for index in facts.indexes
    )
