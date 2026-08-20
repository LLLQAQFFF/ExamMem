"""Add the versioned textbook library and resumable ingestion state.

Revision ID: 0013_textbook_library
Revises: 0012_study_plan_archival
Create Date: 2026-08-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0013_textbook_library"
down_revision: Union[str, Sequence[str], None] = "0012_study_plan_archival"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "textbooks",
        sa.Column("textbook_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("textbook_id", name="pk_textbooks"),
        sa.CheckConstraint("btrim(title) <> ''", name="ck_textbooks_title_nonempty"),
        sa.CheckConstraint(
            "jsonb_typeof(metadata) = 'object'", name="ck_textbooks_metadata_object"
        ),
    )
    op.create_index("ix_textbooks_user_updated", "textbooks", ["user_id", "updated_at"])
    op.create_table(
        "textbook_versions",
        sa.Column("version_id", sa.Text(), nullable=False),
        sa.Column("textbook_id", sa.Text(), sa.ForeignKey("textbooks.textbook_id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("host_source_ref", sa.Text(), nullable=False),
        sa.Column("parser_signature", sa.Text(), nullable=True),
        sa.Column("host_index_ref", sa.Text(), nullable=True),
        sa.Column("index_version", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("warnings", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("version_id", name="pk_textbook_versions"),
        sa.UniqueConstraint("textbook_id", "version", name="uq_textbook_versions_number"),
        sa.UniqueConstraint("textbook_id", "content_hash", name="uq_textbook_versions_content"),
        sa.CheckConstraint("version >= 1", name="ck_textbook_versions_version"),
        sa.CheckConstraint("length(content_hash) = 64", name="ck_textbook_versions_hash"),
        sa.CheckConstraint("size_bytes > 0", name="ck_textbook_versions_size"),
        sa.CheckConstraint(
            "status IN ('queued','processing','completed','failed')",
            name="ck_textbook_versions_status",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(warnings) = 'array'", name="ck_textbook_versions_warnings_array"
        ),
    )
    op.create_table(
        "textbook_sections",
        sa.Column("section_id", sa.Text(), nullable=False),
        sa.Column(
            "version_id", sa.Text(), sa.ForeignKey("textbook_versions.version_id"), nullable=False
        ),
        sa.Column("section_key", sa.Text(), nullable=False),
        sa.Column(
            "parent_section_id",
            sa.Text(),
            sa.ForeignKey("textbook_sections.section_id"),
            nullable=True,
        ),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("path", postgresql.JSONB(), nullable=False),
        sa.Column("start_page", sa.Integer(), nullable=True),
        sa.Column("end_page", sa.Integer(), nullable=True),
        sa.Column("host_content_ref", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("inferred", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("section_id", name="pk_textbook_sections"),
        sa.UniqueConstraint("version_id", "section_key", name="uq_textbook_sections_key"),
        sa.UniqueConstraint("version_id", "position", name="uq_textbook_sections_position"),
        sa.CheckConstraint("level >= 1", name="ck_textbook_sections_level"),
        sa.CheckConstraint("position >= 0", name="ck_textbook_sections_position"),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_textbook_sections_confidence"
        ),
        sa.CheckConstraint("jsonb_typeof(path) = 'array'", name="ck_textbook_sections_path_array"),
    )
    op.create_table(
        "textbook_ingestion_jobs",
        sa.Column("job_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("textbook_id", sa.Text(), sa.ForeignKey("textbooks.textbook_id"), nullable=False),
        sa.Column(
            "version_id", sa.Text(), sa.ForeignKey("textbook_versions.version_id"), nullable=False
        ),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("checkpoint", postgresql.JSONB(), nullable=False),
        sa.Column("input_hash", sa.Text(), nullable=False),
        sa.Column("output_refs", postgresql.JSONB(), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("job_id", name="pk_textbook_ingestion_jobs"),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_textbook_jobs_idempotency"),
        sa.CheckConstraint(
            "stage IN ('saved','parsing','structuring','chunking','indexing','completed','failed')",
            name="ck_textbook_jobs_stage",
        ),
        sa.CheckConstraint("progress >= 0 AND progress <= 100", name="ck_textbook_jobs_progress"),
        sa.CheckConstraint("retry_count >= 0", name="ck_textbook_jobs_retries"),
        sa.CheckConstraint("length(input_hash) = 64", name="ck_textbook_jobs_hash"),
        sa.CheckConstraint(
            "jsonb_typeof(checkpoint) = 'object'", name="ck_textbook_jobs_checkpoint_object"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(output_refs) = 'object'", name="ck_textbook_jobs_refs_object"
        ),
    )
    op.create_index(
        "ix_textbook_jobs_user_updated", "textbook_ingestion_jobs", ["user_id", "updated_at"]
    )
    op.execute("""
        CREATE FUNCTION exam_mem_reject_completed_textbook_version_mutation() RETURNS trigger AS $$
        BEGIN
            IF OLD.status = 'completed' THEN
                RAISE EXCEPTION 'completed textbook versions are immutable';
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER tr_textbook_versions_completed_immutable
        BEFORE UPDATE OR DELETE ON textbook_versions
        FOR EACH ROW EXECUTE FUNCTION exam_mem_reject_completed_textbook_version_mutation()
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM textbooks LIMIT 1) THEN
                RAISE EXCEPTION 'cannot downgrade textbook library while rows exist';
            END IF;
        END; $$
    """)
    op.execute("DROP TRIGGER tr_textbook_versions_completed_immutable ON textbook_versions")
    op.execute("DROP FUNCTION exam_mem_reject_completed_textbook_version_mutation")
    op.drop_index("ix_textbook_jobs_user_updated", table_name="textbook_ingestion_jobs")
    op.drop_table("textbook_ingestion_jobs")
    op.drop_table("textbook_sections")
    op.drop_table("textbook_versions")
    op.drop_index("ix_textbooks_user_updated", table_name="textbooks")
    op.drop_table("textbooks")
