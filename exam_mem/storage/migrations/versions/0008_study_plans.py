"""Add versioned study plans and objective-to-session links.

Revision ID: 0008_study_plans
Revises: 0007_grade_reviews
Create Date: 2026-08-14
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0008_study_plans"
down_revision: Union[str, Sequence[str], None] = "0007_grade_reviews"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "study_plans",
        sa.Column("plan_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("active_version", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("plan_id", name="pk_study_plans"),
        sa.UniqueConstraint("user_id", "plan_id", name="uq_study_plans_user_plan"),
        sa.CheckConstraint("btrim(name) <> ''", name="ck_study_plans_name_nonempty"),
        sa.CheckConstraint(
            "active_version IS NULL OR active_version >= 1",
            name="ck_study_plans_active_version",
        ),
    )
    op.create_index(
        "ix_study_plans_user_updated",
        "study_plans",
        ["user_id", "updated_at", "plan_id"],
    )
    op.create_table(
        "study_plan_drafts",
        sa.Column("plan_id", sa.Text(), sa.ForeignKey("study_plans.plan_id"), nullable=False),
        sa.Column("tree", postgresql.JSONB(), nullable=False),
        sa.Column("source_kind", sa.Text(), nullable=False),
        sa.Column("source_metadata", postgresql.JSONB(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("plan_id", name="pk_study_plan_drafts"),
        sa.CheckConstraint(
            "source_kind IN ('file', 'url', 'generated')",
            name="ck_study_plan_drafts_source_kind",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(tree) = 'object'", name="ck_study_plan_drafts_tree_object"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(source_metadata) = 'object'",
            name="ck_study_plan_drafts_source_metadata_object",
        ),
        sa.CheckConstraint("length(content_hash) = 64", name="ck_study_plan_drafts_hash"),
    )
    op.create_table(
        "study_plan_versions",
        sa.Column("plan_id", sa.Text(), sa.ForeignKey("study_plans.plan_id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("tree", postgresql.JSONB(), nullable=False),
        sa.Column("taxonomy_versions", postgresql.JSONB(), nullable=False),
        sa.Column("source_kind", sa.Text(), nullable=False),
        sa.Column("source_metadata", postgresql.JSONB(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column(
            "published_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("plan_id", "version", name="pk_study_plan_versions"),
        sa.CheckConstraint("version >= 1", name="ck_study_plan_versions_version"),
        sa.CheckConstraint(
            "source_kind IN ('file', 'url', 'generated')",
            name="ck_study_plan_versions_source_kind",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(tree) = 'object'", name="ck_study_plan_versions_tree_object"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(taxonomy_versions) = 'object'",
            name="ck_study_plan_versions_taxonomies_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(source_metadata) = 'object'",
            name="ck_study_plan_versions_source_metadata_object",
        ),
        sa.CheckConstraint("length(content_hash) = 64", name="ck_study_plan_versions_hash"),
    )
    op.execute(
        """
        CREATE TRIGGER tr_study_plan_versions_append_only
        BEFORE UPDATE OR DELETE ON study_plan_versions
        FOR EACH ROW EXECUTE FUNCTION exam_mem_reject_append_only_mutation()
        """
    )
    op.create_table(
        "study_objective_sessions",
        sa.Column("link_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("plan_id", sa.Text(), nullable=False),
        sa.Column("plan_version", sa.Integer(), nullable=False),
        sa.Column("objective_id", sa.Text(), nullable=False),
        sa.Column("host_path_id", sa.Text(), nullable=False),
        sa.Column("host_session_id", sa.Text(), nullable=False),
        sa.Column("initial_turn_id", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("link_id", name="pk_study_objective_sessions"),
        sa.ForeignKeyConstraint(
            ["plan_id", "plan_version"],
            ["study_plan_versions.plan_id", "study_plan_versions.version"],
            name="fk_study_objective_sessions_plan_version",
        ),
        sa.UniqueConstraint(
            "user_id",
            "plan_id",
            "plan_version",
            "objective_id",
            name="uq_study_objective_sessions_objective",
        ),
        sa.UniqueConstraint(
            "user_id",
            "host_session_id",
            name="uq_study_objective_sessions_host_session",
        ),
        sa.CheckConstraint(
            "btrim(objective_id) <> ''", name="ck_study_objective_sessions_objective"
        ),
        sa.CheckConstraint("btrim(host_path_id) <> ''", name="ck_study_objective_sessions_path"),
        sa.CheckConstraint(
            "btrim(host_session_id) <> ''", name="ck_study_objective_sessions_session"
        ),
        sa.CheckConstraint("btrim(initial_turn_id) <> ''", name="ck_study_objective_sessions_turn"),
    )
    op.create_index(
        "ix_study_objective_sessions_user_updated",
        "study_objective_sessions",
        ["user_id", "updated_at", "link_id"],
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM study_plans LIMIT 1)
               OR EXISTS (SELECT 1 FROM study_plan_versions LIMIT 1)
               OR EXISTS (SELECT 1 FROM study_objective_sessions LIMIT 1) THEN
                RAISE EXCEPTION
                    'cannot downgrade study plan contract while tables contain rows';
            END IF;
        END;
        $$
        """
    )
    op.drop_index(
        "ix_study_objective_sessions_user_updated",
        table_name="study_objective_sessions",
    )
    op.drop_table("study_objective_sessions")
    op.execute("DROP TRIGGER tr_study_plan_versions_append_only ON study_plan_versions")
    op.drop_table("study_plan_versions")
    op.drop_table("study_plan_drafts")
    op.drop_index("ix_study_plans_user_updated", table_name="study_plans")
    op.drop_table("study_plans")
