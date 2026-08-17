"""Add reversible assessment archival.

Revision ID: 0011_assessment_archival
Revises: 0010_learning_observations
Create Date: 2026-08-17
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0011_assessment_archival"
down_revision: Union[str, Sequence[str], None] = "0010_learning_observations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "assessments",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM assessments WHERE archived_at IS NOT NULL LIMIT 1) THEN
                RAISE EXCEPTION
                    'cannot downgrade assessment archival while archived assessments exist';
            END IF;
        END;
        $$
        """
    )
    op.drop_column("assessments", "archived_at")
