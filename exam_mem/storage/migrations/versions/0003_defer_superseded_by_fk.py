"""Defer the self-reference used by atomic L2 replacement.

Revision ID: 0003_defer_superseded_by_fk
Revises: 0002_append_only_records
Create Date: 2026-08-11
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_defer_superseded_by_fk"
down_revision: Union[str, Sequence[str], None] = "0002_append_only_records"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CONSTRAINT_NAME = "learning_memories_superseded_by_fkey"


def upgrade() -> None:
    op.drop_constraint(
        CONSTRAINT_NAME,
        "learning_memories",
        type_="foreignkey",
    )
    op.create_foreign_key(
        CONSTRAINT_NAME,
        "learning_memories",
        "learning_memories",
        ["superseded_by"],
        ["memory_id"],
        deferrable=True,
        initially="DEFERRED",
    )


def downgrade() -> None:
    op.drop_constraint(
        CONSTRAINT_NAME,
        "learning_memories",
        type_="foreignkey",
    )
    op.create_foreign_key(
        CONSTRAINT_NAME,
        "learning_memories",
        "learning_memories",
        ["superseded_by"],
        ["memory_id"],
    )
