"""Protect append-only Learning Memory records.

Revision ID: 0002_append_only_records
Revises: 0001_learning_memory_schema
Create Date: 2026-08-11
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_append_only_records"
down_revision: Union[str, Sequence[str], None] = "0001_learning_memory_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

APPEND_ONLY_TABLES = (
    "learning_events",
    "lifecycle_decisions",
    "memory_change_log",
)
TRIGGER_FUNCTION = "exam_mem_reject_append_only_mutation"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE FUNCTION {TRIGGER_FUNCTION}()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'table % is append-only; % is forbidden',
                TG_TABLE_NAME,
                TG_OP
                USING ERRCODE = '55000';
        END;
        $$
        """
    )
    for table_name in APPEND_ONLY_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER tr_{table_name}_append_only
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW
            EXECUTE FUNCTION {TRIGGER_FUNCTION}()
            """
        )


def downgrade() -> None:
    for table_name in reversed(APPEND_ONLY_TABLES):
        op.execute(f"DROP TRIGGER tr_{table_name}_append_only ON {table_name}")
    op.execute(f"DROP FUNCTION {TRIGGER_FUNCTION}()")
