"""Add append-only Grade Review events.

Revision ID: 0007_grade_reviews
Revises: 0006_practice_workflow
Create Date: 2026-08-13
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0007_grade_reviews"
down_revision: Union[str, Sequence[str], None] = "0006_practice_workflow"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "grade_review_events",
        sa.Column("review_event_id", sa.Text(), nullable=False),
        sa.Column("review_chain_id", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("exam_id", sa.Text(), nullable=False),
        sa.Column("subject_id", sa.Text(), nullable=False),
        sa.Column("practice_session_id", sa.Text(), nullable=False),
        sa.Column("checkpoint_key", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("review_event_id", name="pk_grade_review_events"),
        sa.UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uq_grade_review_events_user_idem",
        ),
        sa.CheckConstraint(
            "action IN ('dispute', 'uphold', 'overturn')",
            name="ck_grade_review_events_action",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object'",
            name="ck_grade_review_events_payload_object",
        ),
    )
    op.create_index(
        "ix_grade_review_events_scope_created",
        "grade_review_events",
        ["user_id", "exam_id", "subject_id", "created_at"],
    )
    op.create_index(
        "ix_grade_review_events_chain_created",
        "grade_review_events",
        ["review_chain_id", "created_at"],
    )
    op.execute(
        """
        CREATE TRIGGER tr_grade_review_events_append_only
        BEFORE UPDATE OR DELETE ON grade_review_events
        FOR EACH ROW EXECUTE FUNCTION exam_mem_reject_append_only_mutation()
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM grade_review_events LIMIT 1) THEN
                RAISE EXCEPTION
                    'cannot downgrade grade review contract while table contains rows';
            END IF;
        END;
        $$
        """
    )
    op.execute("DROP TRIGGER tr_grade_review_events_append_only ON grade_review_events")
    op.drop_index(
        "ix_grade_review_events_chain_created",
        table_name="grade_review_events",
    )
    op.drop_index(
        "ix_grade_review_events_scope_created",
        table_name="grade_review_events",
    )
    op.drop_table("grade_review_events")
