"""Add versioned textbook bindings, mappings, and source snapshots.

Revision ID: 0014_textbook_grounding
Revises: 0013_textbook_library
Create Date: 2026-08-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0014_textbook_grounding"
down_revision: Union[str, Sequence[str], None] = "0013_textbook_library"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "study_plan_textbook_bindings",
        sa.Column("binding_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("plan_id", sa.Text(), nullable=False),
        sa.Column("plan_version", sa.Integer(), nullable=False),
        sa.Column(
            "textbook_version_id",
            sa.Text(),
            sa.ForeignKey("textbook_versions.version_id"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("confirmed_by", sa.Text(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("binding_id", name="pk_study_plan_textbook_bindings"),
        sa.ForeignKeyConstraint(
            ["plan_id", "plan_version"],
            ["study_plan_versions.plan_id", "study_plan_versions.version"],
            name="fk_textbook_bindings_plan_version",
        ),
        sa.UniqueConstraint(
            "plan_id",
            "plan_version",
            "textbook_version_id",
            "revision",
            name="uq_textbook_bindings_revision",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_textbook_bindings_revision"),
        sa.CheckConstraint("priority >= 0", name="ck_textbook_bindings_priority"),
        sa.CheckConstraint(
            "role IN ('primary','supplement','reference')", name="ck_textbook_bindings_role"
        ),
        sa.CheckConstraint(
            "status IN ('candidate','confirmed','inactive')", name="ck_textbook_bindings_status"
        ),
    )
    op.create_table(
        "objective_textbook_section_mappings",
        sa.Column("mapping_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("plan_id", sa.Text(), nullable=False),
        sa.Column("plan_version", sa.Integer(), nullable=False),
        sa.Column("objective_id", sa.Text(), nullable=False),
        sa.Column(
            "textbook_section_id",
            sa.Text(),
            sa.ForeignKey("textbook_sections.section_id"),
            nullable=False,
        ),
        sa.Column("mapping_version", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("created_via", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("confirmed_by", sa.Text(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("mapping_id", name="pk_objective_textbook_section_mappings"),
        sa.ForeignKeyConstraint(
            ["plan_id", "plan_version"],
            ["study_plan_versions.plan_id", "study_plan_versions.version"],
            name="fk_textbook_mappings_plan_version",
        ),
        sa.UniqueConstraint(
            "plan_id",
            "plan_version",
            "objective_id",
            "textbook_section_id",
            "mapping_version",
            name="uq_textbook_mappings_version",
        ),
        sa.CheckConstraint("mapping_version >= 1", name="ck_textbook_mappings_version"),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_textbook_mappings_confidence"
        ),
        sa.CheckConstraint(
            "created_via IN ('manual','recommended')", name="ck_textbook_mappings_method"
        ),
        sa.CheckConstraint(
            "status IN ('candidate','confirmed','rejected')", name="ck_textbook_mappings_status"
        ),
    )
    op.create_table(
        "learning_source_snapshots",
        sa.Column("snapshot_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("host_session_id", sa.Text(), nullable=False),
        sa.Column("plan_id", sa.Text(), nullable=False),
        sa.Column("plan_version", sa.Integer(), nullable=False),
        sa.Column("objective_id", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("sources", postgresql.JSONB(), nullable=False),
        sa.Column("index_versions", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("snapshot_id", name="pk_learning_source_snapshots"),
        sa.ForeignKeyConstraint(
            ["plan_id", "plan_version"],
            ["study_plan_versions.plan_id", "study_plan_versions.version"],
            name="fk_learning_snapshots_plan_version",
        ),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_learning_snapshots_idempotency"),
        sa.UniqueConstraint("user_id", "host_session_id", name="uq_learning_snapshots_session"),
        sa.CheckConstraint(
            "mode IN ('unbound','primary','compare')", name="ck_learning_snapshots_mode"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(sources) = 'array'", name="ck_learning_snapshots_sources_array"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(index_versions) = 'object'", name="ck_learning_snapshots_indexes_object"
        ),
    )
    op.create_table(
        "assessment_source_snapshots",
        sa.Column("snapshot_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("assessment_id", sa.Text(), nullable=False),
        sa.Column("assessment_version", sa.Integer(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("index_versions", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("snapshot_id", name="pk_assessment_source_snapshots"),
        sa.ForeignKeyConstraint(
            ["assessment_id", "assessment_version"],
            ["assessment_versions.assessment_id", "assessment_versions.version"],
            name="fk_assessment_snapshots_version",
        ),
        sa.UniqueConstraint(
            "user_id", "idempotency_key", name="uq_assessment_snapshots_idempotency"
        ),
        sa.UniqueConstraint(
            "user_id", "assessment_id", "assessment_version", name="uq_assessment_snapshots_version"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence) = 'array'", name="ck_assessment_snapshots_evidence_array"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(index_versions) = 'object'", name="ck_assessment_snapshots_indexes_object"
        ),
    )
    for table in (
        "study_plan_textbook_bindings",
        "objective_textbook_section_mappings",
        "learning_source_snapshots",
        "assessment_source_snapshots",
    ):
        op.execute(
            f"CREATE TRIGGER tr_{table}_append_only BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION exam_mem_reject_append_only_mutation()"
        )


def downgrade() -> None:
    op.execute(
        """DO $$ BEGIN IF EXISTS (SELECT 1 FROM study_plan_textbook_bindings LIMIT 1) OR EXISTS (SELECT 1 FROM objective_textbook_section_mappings LIMIT 1) OR EXISTS (SELECT 1 FROM learning_source_snapshots LIMIT 1) OR EXISTS (SELECT 1 FROM assessment_source_snapshots LIMIT 1) THEN RAISE EXCEPTION 'cannot downgrade textbook grounding while rows exist'; END IF; END; $$"""
    )
    for table in (
        "assessment_source_snapshots",
        "learning_source_snapshots",
        "objective_textbook_section_mappings",
        "study_plan_textbook_bindings",
    ):
        op.execute(f"DROP TRIGGER tr_{table}_append_only ON {table}")
        op.drop_table(table)
