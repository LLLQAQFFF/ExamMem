"""Create the Stage 05 Learning Memory schema.

Revision ID: 0001_learning_memory_schema
Revises:
Create Date: 2026-08-11
"""

from typing import Sequence, Union

from alembic import op
from pgvector.sqlalchemy import Vector
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from exam_mem.storage.models import LEARNING_MEMORY_EMBEDDING_DIMENSION

# revision identifiers, used by Alembic.
revision: str = "0001_learning_memory_schema"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "learning_events",
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("exam_id", sa.Text(), nullable=False),
        sa.Column("subject_id", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=True),
        sa.Column("question_id", sa.Text(), nullable=True),
        sa.Column("knowledge_point_ids", postgresql.JSONB(), nullable=False),
        sa.Column("primary_knowledge_point_id", sa.Text(), nullable=True),
        sa.Column("difficulty", sa.Float(), nullable=True),
        sa.Column("answer_correct", sa.Boolean(), nullable=True),
        sa.Column("error_type", sa.Text(), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("evidence_quality", postgresql.JSONB(), nullable=False),
        sa.Column("correction_source", sa.Text(), nullable=True),
        sa.Column("correction_statement", sa.Text(), nullable=True),
        sa.Column("plan_transition_status", sa.Text(), nullable=True),
        sa.Column("plan_transition_source", sa.Text(), nullable=True),
        sa.Column("plan_transition_reason", sa.Text(), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("trace_id", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "event_type IN ('answer_attempt', 'explicit_correction', 'plan_transition')",
            name="ck_learning_events_event_type",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(knowledge_point_ids) = 'array' "
            "AND jsonb_array_length(knowledge_point_ids) > 0",
            name="ck_learning_events_knowledge_points_nonempty",
        ),
        sa.CheckConstraint(
            "difficulty IS NULL OR (difficulty >= 0.0 AND difficulty <= 1.0)",
            name="ck_learning_events_difficulty_range",
        ),
        sa.CheckConstraint(
            "(event_type = 'answer_attempt' "
            "AND question_id IS NOT NULL "
            "AND difficulty IS NOT NULL "
            "AND answer_correct IS NOT NULL "
            "AND correction_source IS NULL "
            "AND correction_statement IS NULL "
            "AND plan_transition_status IS NULL "
            "AND plan_transition_source IS NULL "
            "AND plan_transition_reason IS NULL) "
            "OR (event_type = 'explicit_correction' "
            "AND correction_source IS NOT NULL "
            "AND correction_statement IS NOT NULL "
            "AND question_id IS NULL "
            "AND difficulty IS NULL "
            "AND answer_correct IS NULL "
            "AND error_type IS NULL "
            "AND error_detail IS NULL "
            "AND plan_transition_status IS NULL "
            "AND plan_transition_source IS NULL "
            "AND plan_transition_reason IS NULL) "
            "OR (event_type = 'plan_transition' "
            "AND plan_transition_status IS NOT NULL "
            "AND plan_transition_source IS NOT NULL "
            "AND plan_transition_reason IS NOT NULL "
            "AND question_id IS NULL "
            "AND difficulty IS NULL "
            "AND answer_correct IS NULL "
            "AND error_type IS NULL "
            "AND error_detail IS NULL "
            "AND correction_source IS NULL "
            "AND correction_statement IS NULL)",
            name="ck_learning_events_payload_shape",
        ),
        sa.CheckConstraint(
            "correction_source IS NULL OR correction_source IN ('user', 'teacher', 'grader_audit')",
            name="ck_learning_events_correction_source",
        ),
        sa.CheckConstraint(
            "plan_transition_source IS NULL "
            "OR plan_transition_source IN ('user', 'system', 'practice_progress')",
            name="ck_learning_events_plan_transition_source",
        ),
        sa.CheckConstraint(
            "plan_transition_status IS NULL "
            "OR plan_transition_status IN "
            "('planned', 'in_progress', 'completed', 'cancelled', 'expired')",
            name="ck_learning_events_plan_transition_status",
        ),
        sa.CheckConstraint(
            "schema_version >= 1",
            name="ck_learning_events_schema_version",
        ),
        sa.PrimaryKeyConstraint("event_id"),
        sa.UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uq_learning_events_user_idempotency",
        ),
    )

    op.create_table(
        "learning_memories",
        sa.Column("memory_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("exam_id", sa.Text(), nullable=False),
        sa.Column("subject_id", sa.Text(), nullable=False),
        sa.Column("memory_namespace", sa.Text(), nullable=False),
        sa.Column("slot_key", sa.Text(), nullable=False),
        sa.Column("value", postgresql.JSONB(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence_count", sa.Integer(), nullable=False),
        sa.Column("lifecycle_state", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by", sa.Text(), nullable=True),
        sa.Column("contested_group_id", sa.Text(), nullable=True),
        sa.Column(
            "content_embedding",
            Vector(LEARNING_MEMORY_EMBEDDING_DIMENSION),
            nullable=True,
        ),
        sa.Column("policy_version", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "memory_namespace IN ('mastery', 'error_pattern', 'plan', 'profile', 'preference')",
            name="ck_learning_memories_namespace",
        ),
        sa.CheckConstraint(
            "value ->> 'type' = memory_namespace",
            name="ck_learning_memories_value_namespace",
        ),
        sa.CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0",
            name="ck_learning_memories_confidence_range",
        ),
        sa.CheckConstraint(
            "evidence_count >= 1",
            name="ck_learning_memories_evidence_count",
        ),
        sa.CheckConstraint("version >= 1", name="ck_learning_memories_version"),
        sa.CheckConstraint("row_version >= 1", name="ck_learning_memories_row_version"),
        sa.CheckConstraint(
            "lifecycle_state IN ('active', 'archived', 'invalidated', 'contested')",
            name="ck_learning_memories_lifecycle_state",
        ),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_to >= valid_from",
            name="ck_learning_memories_valid_interval",
        ),
        sa.CheckConstraint(
            "lifecycle_state <> 'active' OR valid_to IS NULL",
            name="ck_learning_memories_active_open_interval",
        ),
        sa.CheckConstraint(
            "lifecycle_state NOT IN ('archived', 'invalidated') OR valid_to IS NOT NULL",
            name="ck_learning_memories_terminal_closed_interval",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by"],
            ["learning_memories.memory_id"],
        ),
        sa.PrimaryKeyConstraint("memory_id"),
        sa.UniqueConstraint(
            "user_id",
            "exam_id",
            "subject_id",
            "memory_namespace",
            "slot_key",
            "version",
            name="uq_learning_memories_scope_slot_version",
        ),
    )

    op.create_table(
        "event_correction_targets",
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("memory_id", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["event_id"], ["learning_events.event_id"]),
        sa.ForeignKeyConstraint(["memory_id"], ["learning_memories.memory_id"]),
        sa.PrimaryKeyConstraint(
            "event_id",
            "memory_id",
            name="pk_event_correction_targets",
        ),
    )

    op.create_table(
        "event_plan_transition_targets",
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("memory_id", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["event_id"], ["learning_events.event_id"]),
        sa.ForeignKeyConstraint(["memory_id"], ["learning_memories.memory_id"]),
        sa.UniqueConstraint(
            "event_id",
            name="uq_event_plan_transition_targets_event",
        ),
    )

    op.create_table(
        "memory_provenance",
        sa.Column("memory_id", sa.Text(), nullable=False),
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("relation_type", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "relation_type IN ('created_by', 'merged_from', 'contradicted_by', 'invalidated_by')",
            name="ck_memory_provenance_relation_type",
        ),
        sa.ForeignKeyConstraint(["event_id"], ["learning_events.event_id"]),
        sa.ForeignKeyConstraint(["memory_id"], ["learning_memories.memory_id"]),
        sa.PrimaryKeyConstraint(
            "memory_id",
            "event_id",
            "relation_type",
            name="pk_memory_provenance",
        ),
    )

    op.create_table(
        "student_model_snapshots",
        sa.Column("snapshot_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("exam_id", sa.Text(), nullable=False),
        sa.Column("subject_id", sa.Text(), nullable=False),
        sa.Column("model", postgresql.JSONB(), nullable=False),
        sa.Column("projection_version", sa.Integer(), nullable=False),
        sa.Column("source_event_watermark", sa.Text(), nullable=False),
        sa.Column("source_memory_watermark", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "projection_version >= 1",
            name="ck_student_model_snapshots_projection_version",
        ),
        sa.PrimaryKeyConstraint("snapshot_id"),
    )

    op.create_table(
        "lifecycle_decisions",
        sa.Column("decision_id", sa.Text(), nullable=False),
        sa.Column("input_summary", postgresql.JSONB(), nullable=False),
        sa.Column("candidate_memory_ids", postgresql.JSONB(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("policy_version", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("decision_id"),
    )

    op.create_table(
        "memory_change_log",
        sa.Column("change_id", sa.Text(), nullable=False),
        sa.Column("before_state", postgresql.JSONB(), nullable=True),
        sa.Column("after_state", postgresql.JSONB(), nullable=True),
        sa.Column("apply_state", sa.Text(), nullable=False),
        sa.Column("trace_id", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("change_id"),
    )

    op.create_index(
        "ix_learning_memories_scope_slot_state",
        "learning_memories",
        [
            "user_id",
            "exam_id",
            "subject_id",
            "memory_namespace",
            "slot_key",
            "lifecycle_state",
        ],
        unique=False,
    )
    op.create_index(
        "uq_learning_memories_scope_slot_active",
        "learning_memories",
        ["user_id", "exam_id", "subject_id", "memory_namespace", "slot_key"],
        unique=True,
        postgresql_where=sa.text("lifecycle_state = 'active'"),
    )
    op.create_index(
        "ix_learning_memories_content_embedding_hnsw",
        "learning_memories",
        ["content_embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"content_embedding": "vector_cosine_ops"},
        postgresql_where=sa.text("content_embedding IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_learning_memories_content_embedding_hnsw",
        table_name="learning_memories",
        postgresql_using="hnsw",
    )
    op.drop_index(
        "uq_learning_memories_scope_slot_active",
        table_name="learning_memories",
        postgresql_where=sa.text("lifecycle_state = 'active'"),
    )
    op.drop_index(
        "ix_learning_memories_scope_slot_state",
        table_name="learning_memories",
    )
    op.drop_table("memory_change_log")
    op.drop_table("lifecycle_decisions")
    op.drop_table("student_model_snapshots")
    op.drop_table("memory_provenance")
    op.drop_table("event_plan_transition_targets")
    op.drop_table("event_correction_targets")
    op.drop_table("learning_memories")
    op.drop_table("learning_events")

    # The extension can be shared by other schemas, so Stage 05 does not remove it.
