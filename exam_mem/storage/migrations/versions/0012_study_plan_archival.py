"""Add reversible study-plan archival.

Revision ID: 0012_study_plan_archival
Revises: 0011_assessment_archival
Create Date: 2026-08-18
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0012_study_plan_archival"
down_revision: Union[str, Sequence[str], None] = "0011_assessment_archival"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "study_plans",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM study_plans WHERE archived_at IS NOT NULL LIMIT 1) THEN
                RAISE EXCEPTION
                    'cannot downgrade study plan archival while archived plans exist';
            END IF;
        END;
        $$
        """
    )
    op.drop_column("study_plans", "archived_at")
