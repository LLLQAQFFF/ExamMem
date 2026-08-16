"""Add versioned assessments and finite attempts.

Revision ID: 0009_assessments
Revises: 0008_study_plans
Create Date: 2026-08-14
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0009_assessments"
down_revision: Union[str, Sequence[str], None] = "0008_study_plans"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "assessments",
        sa.Column("assessment_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("exam_id", sa.Text(), nullable=False),
        sa.Column("subject_id", sa.Text(), nullable=False),
        sa.Column("taxonomy_version", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("knowledge_point_ids", postgresql.JSONB(), nullable=False),
        sa.Column("latest_version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("assessment_id", name="pk_assessments"),
        sa.CheckConstraint("btrim(title) <> ''", name="ck_assessments_title_nonempty"),
        sa.CheckConstraint(
            "jsonb_typeof(knowledge_point_ids) = 'array' "
            "AND jsonb_array_length(knowledge_point_ids) > 0",
            name="ck_assessments_knowledge_points",
        ),
        sa.CheckConstraint("latest_version >= 1", name="ck_assessments_latest_version"),
    )
    op.create_index(
        "ix_assessments_scope_updated",
        "assessments",
        ["user_id", "exam_id", "subject_id", "updated_at", "assessment_id"],
    )
    op.create_table(
        "assessment_versions",
        sa.Column(
            "assessment_id", sa.Text(), sa.ForeignKey("assessments.assessment_id"), nullable=False
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("question_catalog", postgresql.JSONB(), nullable=False),
        sa.Column("generation", postgresql.JSONB(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("assessment_id", "version", name="pk_assessment_versions"),
        sa.CheckConstraint("version >= 1", name="ck_assessment_versions_version"),
        sa.CheckConstraint(
            "jsonb_typeof(question_catalog) = 'array' AND jsonb_array_length(question_catalog) > 0",
            name="ck_assessment_versions_catalog",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(generation) = 'object'",
            name="ck_assessment_versions_generation",
        ),
        sa.CheckConstraint("length(content_hash) = 64", name="ck_assessment_versions_hash"),
    )
    op.execute(
        """
        CREATE TRIGGER tr_assessment_versions_append_only
        BEFORE UPDATE OR DELETE ON assessment_versions
        FOR EACH ROW EXECUTE FUNCTION exam_mem_reject_append_only_mutation()
        """
    )
    op.create_table(
        "assessment_attempts",
        sa.Column("attempt_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("assessment_id", sa.Text(), nullable=False),
        sa.Column("assessment_version", sa.Integer(), nullable=False),
        sa.Column("practice_session_id", sa.Text(), nullable=False),
        sa.Column("trace_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("attempt_id", name="pk_assessment_attempts"),
        sa.ForeignKeyConstraint(
            ["assessment_id", "assessment_version"],
            ["assessment_versions.assessment_id", "assessment_versions.version"],
            name="fk_assessment_attempts_version",
        ),
        sa.UniqueConstraint(
            "user_id",
            "practice_session_id",
            name="uq_assessment_attempts_practice_session",
        ),
        sa.UniqueConstraint("user_id", "trace_id", name="uq_assessment_attempts_trace"),
        sa.CheckConstraint(
            "status IN ('in_progress', 'completed', 'failed')",
            name="ck_assessment_attempts_status",
        ),
        sa.CheckConstraint(
            "(status = 'completed' AND completed_at IS NOT NULL) "
            "OR (status <> 'completed' AND completed_at IS NULL)",
            name="ck_assessment_attempts_completion",
        ),
    )
    op.create_index(
        "ix_assessment_attempts_assessment_started",
        "assessment_attempts",
        ["user_id", "assessment_id", "started_at", "attempt_id"],
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM assessments LIMIT 1)
               OR EXISTS (SELECT 1 FROM assessment_versions LIMIT 1)
               OR EXISTS (SELECT 1 FROM assessment_attempts LIMIT 1) THEN
                RAISE EXCEPTION
                    'cannot downgrade assessment contract while tables contain rows';
            END IF;
        END;
        $$
        """
    )
    op.drop_index(
        "ix_assessment_attempts_assessment_started",
        table_name="assessment_attempts",
    )
    op.drop_table("assessment_attempts")
    op.execute("DROP TRIGGER tr_assessment_versions_append_only ON assessment_versions")
    op.drop_table("assessment_versions")
    op.drop_index("ix_assessments_scope_updated", table_name="assessments")
    op.drop_table("assessments")
