"""Add isolated append-only and vector baseline facts for Stage 07.

Revision ID: 0005_practice_backend_facts
Revises: 0004_lifecycle_audit_contract
Create Date: 2026-08-12
"""

from typing import Sequence, Union

from alembic import op
from pgvector.sqlalchemy import Vector
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from exam_mem.storage.models import LEARNING_MEMORY_EMBEDDING_DIMENSION

# revision identifiers, used by Alembic.
revision: str = "0005_practice_backend_facts"
down_revision: Union[str, Sequence[str], None] = "0004_lifecycle_audit_contract"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _require_empty_baseline_facts(action: str) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM baseline_memory_facts LIMIT 1) THEN
                RAISE EXCEPTION
                    'cannot {action} practice backend facts while table contains rows';
            END IF;
        END;
        $$
        """
    )


def upgrade() -> None:
    op.create_table(
        "baseline_memory_facts",
        sa.Column("backend_mode", sa.Text(), nullable=False),
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("exam_id", sa.Text(), nullable=False),
        sa.Column("subject_id", sa.Text(), nullable=False),
        sa.Column("memory_namespace", sa.Text(), nullable=False),
        sa.Column("slot_key", sa.Text(), nullable=False),
        sa.Column("value", postgresql.JSONB(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column(
            "content_embedding",
            Vector(LEARNING_MEMORY_EMBEDDING_DIMENSION),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "backend_mode IN ('append_only', 'vector')",
            name="ck_baseline_memory_facts_backend_mode",
        ),
        sa.CheckConstraint(
            "memory_namespace IN ('mastery', 'error_pattern', 'plan', 'profile', 'preference')",
            name="ck_baseline_memory_facts_namespace",
        ),
        sa.CheckConstraint(
            "value ->> 'type' = memory_namespace",
            name="ck_baseline_memory_facts_value_namespace",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence) = 'object'",
            name="ck_baseline_memory_facts_evidence_object",
        ),
        sa.CheckConstraint(
            "(backend_mode = 'append_only' AND content_embedding IS NULL) "
            "OR (backend_mode = 'vector' AND content_embedding IS NOT NULL)",
            name="ck_baseline_memory_facts_embedding_mode",
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["learning_events.event_id"],
            name="fk_baseline_memory_facts_event_id",
        ),
        sa.PrimaryKeyConstraint(
            "backend_mode",
            "event_id",
            "slot_key",
            name="pk_baseline_memory_facts",
        ),
    )
    op.create_index(
        "ix_baseline_memory_facts_mode_scope_slot_created",
        "baseline_memory_facts",
        [
            "backend_mode",
            "user_id",
            "exam_id",
            "subject_id",
            "memory_namespace",
            "slot_key",
            "created_at",
            "event_id",
        ],
    )
    op.create_index(
        "ix_baseline_memory_facts_content_embedding_hnsw",
        "baseline_memory_facts",
        ["content_embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"content_embedding": "vector_cosine_ops"},
        postgresql_where=sa.text("backend_mode = 'vector' AND content_embedding IS NOT NULL"),
    )
    op.execute(
        """
        CREATE TRIGGER tr_baseline_memory_facts_append_only
        BEFORE UPDATE OR DELETE ON baseline_memory_facts
        FOR EACH ROW EXECUTE FUNCTION exam_mem_reject_append_only_mutation()
        """
    )


def downgrade() -> None:
    _require_empty_baseline_facts("downgrade")

    op.execute("DROP TRIGGER tr_baseline_memory_facts_append_only ON baseline_memory_facts")
    op.drop_index(
        "ix_baseline_memory_facts_content_embedding_hnsw",
        table_name="baseline_memory_facts",
    )
    op.drop_index(
        "ix_baseline_memory_facts_mode_scope_slot_created",
        table_name="baseline_memory_facts",
    )
    op.drop_table("baseline_memory_facts")
