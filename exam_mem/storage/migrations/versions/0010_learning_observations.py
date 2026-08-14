"""Add isolated Agent learning observations.

Revision ID: 0010_learning_observations
Revises: 0009_assessments
Create Date: 2026-08-14
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0010_learning_observations"
down_revision: Union[str, Sequence[str], None] = "0009_assessments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "learning_observations",
        sa.Column("observation_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("exam_id", sa.Text(), nullable=False),
        sa.Column("subject_id", sa.Text(), nullable=False),
        sa.Column("taxonomy_version", sa.Text(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("source_session_id", sa.Text(), nullable=False),
        sa.Column("source_turn_ids", postgresql.JSONB(), nullable=False),
        sa.Column("knowledge_point_ids", postgresql.JSONB(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("agent_contract_version", sa.Text(), nullable=False),
        sa.Column("source_fingerprint", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("observation_id", name="pk_learning_observations"),
        sa.UniqueConstraint(
            "user_id",
            "source_fingerprint",
            name="uq_learning_observations_user_source",
        ),
        sa.CheckConstraint(
            "channel IN ('chat', 'learning_path')",
            name="ck_learning_observations_channel",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(source_turn_ids) = 'array'",
            name="ck_learning_observations_turns_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(knowledge_point_ids) = 'array' "
            "AND jsonb_array_length(knowledge_point_ids) > 0",
            name="ck_learning_observations_knowledge_points",
        ),
        sa.CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0",
            name="ck_learning_observations_confidence",
        ),
        sa.CheckConstraint(
            "length(source_fingerprint) = 64",
            name="ck_learning_observations_fingerprint",
        ),
    )
    op.create_index(
        "ix_learning_observations_scope_created",
        "learning_observations",
        [
            "user_id",
            "exam_id",
            "subject_id",
            "taxonomy_version",
            "created_at",
            "observation_id",
        ],
    )
    op.execute(
        """
        CREATE TRIGGER tr_learning_observations_append_only
        BEFORE UPDATE OR DELETE ON learning_observations
        FOR EACH ROW EXECUTE FUNCTION exam_mem_reject_append_only_mutation()
        """
    )
    op.create_table(
        "learning_observation_actions",
        sa.Column("action_id", sa.Text(), nullable=False),
        sa.Column(
            "observation_id",
            sa.Text(),
            sa.ForeignKey("learning_observations.observation_id"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint(
            "action_id",
            name="pk_learning_observation_actions",
        ),
        sa.UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uq_learning_observation_actions_user_idempotency",
        ),
        sa.CheckConstraint(
            "action IN ('confirm', 'dismiss')",
            name="ck_learning_observation_actions_action",
        ),
    )
    op.create_index(
        "ix_learning_observation_actions_observation_created",
        "learning_observation_actions",
        ["observation_id", "created_at", "action_id"],
    )
    op.execute(
        """
        CREATE TRIGGER tr_learning_observation_actions_append_only
        BEFORE UPDATE OR DELETE ON learning_observation_actions
        FOR EACH ROW EXECUTE FUNCTION exam_mem_reject_append_only_mutation()
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM learning_observations LIMIT 1)
               OR EXISTS (SELECT 1 FROM learning_observation_actions LIMIT 1) THEN
                RAISE EXCEPTION
                    'cannot downgrade learning observation contract while tables contain rows';
            END IF;
        END;
        $$
        """
    )
    op.execute(
        "DROP TRIGGER tr_learning_observation_actions_append_only ON learning_observation_actions"
    )
    op.drop_index(
        "ix_learning_observation_actions_observation_created",
        table_name="learning_observation_actions",
    )
    op.drop_table("learning_observation_actions")
    op.execute("DROP TRIGGER tr_learning_observations_append_only ON learning_observations")
    op.drop_index(
        "ix_learning_observations_scope_created",
        table_name="learning_observations",
    )
    op.drop_table("learning_observations")
