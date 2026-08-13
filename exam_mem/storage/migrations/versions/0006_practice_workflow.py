"""Persist Stage 07 workflow checkpoints and append-only Trace spans.

Revision ID: 0006_practice_workflow
Revises: 0005_practice_backend_facts
Create Date: 2026-08-12
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0006_practice_workflow"
down_revision: Union[str, Sequence[str], None] = "0005_practice_backend_facts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _require_empty_practice_runtime_tables(action: str) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM practice_workflow_checkpoints LIMIT 1)
               OR EXISTS (SELECT 1 FROM practice_trace_spans LIMIT 1) THEN
                RAISE EXCEPTION
                    'cannot {action} practice workflow contract while tables contain rows';
            END IF;
        END;
        $$
        """
    )


def upgrade() -> None:
    op.create_table(
        "practice_workflow_checkpoints",
        sa.Column("practice_session_id", sa.Text(), nullable=False),
        sa.Column("checkpoint_key", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("exam_id", sa.Text(), nullable=False),
        sa.Column("subject_id", sa.Text(), nullable=False),
        sa.Column("trace_id", sa.Text(), nullable=False),
        sa.Column("step_state", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "step_state IN "
            "('IDLE', 'QUESTION_READY', 'ANSWER_RECEIVED', 'GRADED', "
            "'DIAGNOSED', 'MEMORY_UPDATED', 'RECOMMENDED')",
            name="ck_practice_workflow_checkpoints_step_state",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object'",
            name="ck_practice_workflow_checkpoints_payload_object",
        ),
        sa.CheckConstraint(
            "payload ->> 'checkpoint_key' = checkpoint_key",
            name="ck_practice_workflow_checkpoints_payload_key",
        ),
        sa.CheckConstraint(
            "payload #>> '{context,practice_session_id}' = practice_session_id",
            name="ck_practice_workflow_checkpoints_payload_session",
        ),
        sa.CheckConstraint(
            "payload #>> '{context,trace_id}' = trace_id",
            name="ck_practice_workflow_checkpoints_payload_trace",
        ),
        sa.CheckConstraint(
            "payload #>> '{context,step_state}' = step_state",
            name="ck_practice_workflow_checkpoints_payload_state",
        ),
        sa.CheckConstraint(
            "row_version >= 1",
            name="ck_practice_workflow_checkpoints_row_version",
        ),
        sa.PrimaryKeyConstraint(
            "practice_session_id",
            "checkpoint_key",
            name="pk_practice_workflow_checkpoints",
        ),
    )
    op.create_index(
        "ix_practice_workflow_checkpoints_scope_updated",
        "practice_workflow_checkpoints",
        [
            "user_id",
            "exam_id",
            "subject_id",
            "updated_at",
            "practice_session_id",
        ],
    )
    op.create_index(
        "ix_practice_workflow_checkpoints_trace",
        "practice_workflow_checkpoints",
        ["trace_id"],
    )

    op.create_table(
        "practice_trace_spans",
        sa.Column("trace_id", sa.Text(), nullable=False),
        sa.Column("step_id", sa.Integer(), nullable=False),
        sa.Column("span_name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("input_summary", postgresql.JSONB(), nullable=False),
        sa.Column("output_summary", postgresql.JSONB(), nullable=False),
        sa.Column("versions", postgresql.JSONB(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("llm_calls", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("related_record_ids", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("step_id >= 1", name="ck_practice_trace_spans_step_id"),
        sa.CheckConstraint(
            "span_name IN "
            "('request_received', 'question_selected', 'answer_graded', "
            "'knowledge_mapped', 'error_diagnosed', 'event_appended', "
            "'lifecycle_decided', 'lifecycle_applied', 'student_model_projected', "
            "'question_recommended', 'plan_transition_appended', "
            "'plan_transition_applied', 'correction_target_resolved', "
            "'correction_event_appended', 'correction_lifecycle_applied', "
            "'recommendation_refreshed', 'response_sent')",
            name="ck_practice_trace_spans_name",
        ),
        sa.CheckConstraint(
            "status IN ('completed', 'failed')",
            name="ck_practice_trace_spans_status",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(input_summary) = 'object' "
            "AND jsonb_typeof(output_summary) = 'object' "
            "AND jsonb_typeof(versions) = 'object'",
            name="ck_practice_trace_spans_summary_objects",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(related_record_ids) = 'array'",
            name="ck_practice_trace_spans_related_ids_array",
        ),
        sa.CheckConstraint(
            "completed_at >= started_at AND duration_ms >= 0.0",
            name="ck_practice_trace_spans_duration",
        ),
        sa.CheckConstraint(
            "retry_count >= 0 AND llm_calls >= 0 AND input_tokens >= 0 AND output_tokens >= 0",
            name="ck_practice_trace_spans_counts",
        ),
        sa.CheckConstraint(
            "(status = 'failed' AND error_code IS NOT NULL) "
            "OR (status = 'completed' AND error_code IS NULL)",
            name="ck_practice_trace_spans_error_status",
        ),
        sa.PrimaryKeyConstraint(
            "trace_id",
            "step_id",
            name="pk_practice_trace_spans",
        ),
    )
    op.create_index(
        "ix_practice_trace_spans_name_created",
        "practice_trace_spans",
        ["span_name", "created_at", "trace_id"],
    )
    op.execute(
        """
        CREATE TRIGGER tr_practice_trace_spans_append_only
        BEFORE UPDATE OR DELETE ON practice_trace_spans
        FOR EACH ROW EXECUTE FUNCTION exam_mem_reject_append_only_mutation()
        """
    )


def downgrade() -> None:
    _require_empty_practice_runtime_tables("downgrade")

    op.execute("DROP TRIGGER tr_practice_trace_spans_append_only ON practice_trace_spans")
    op.drop_index(
        "ix_practice_trace_spans_name_created",
        table_name="practice_trace_spans",
    )
    op.drop_table("practice_trace_spans")

    op.drop_index(
        "ix_practice_workflow_checkpoints_trace",
        table_name="practice_workflow_checkpoints",
    )
    op.drop_index(
        "ix_practice_workflow_checkpoints_scope_updated",
        table_name="practice_workflow_checkpoints",
    )
    op.drop_table("practice_workflow_checkpoints")
