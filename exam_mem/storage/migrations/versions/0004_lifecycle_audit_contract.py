"""Complete the Stage 06 lifecycle audit contract.

Revision ID: 0004_lifecycle_audit_contract
Revises: 0003_defer_superseded_by_fk
Create Date: 2026-08-11
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0004_lifecycle_audit_contract"
down_revision: Union[str, Sequence[str], None] = "0003_defer_superseded_by_fk"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _require_empty_audit_tables(action: str) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM lifecycle_decisions LIMIT 1)
               OR EXISTS (SELECT 1 FROM memory_change_log LIMIT 1) THEN
                RAISE EXCEPTION
                    'cannot {action} lifecycle audit contract while audit tables contain rows';
            END IF;
        END;
        $$
        """
    )


def upgrade() -> None:
    _require_empty_audit_tables("upgrade")

    op.add_column(
        "lifecycle_decisions",
        sa.Column("trace_id", sa.Text(), nullable=False),
    )
    op.add_column(
        "lifecycle_decisions",
        sa.Column("event_id", sa.Text(), nullable=False),
    )
    op.add_column(
        "lifecycle_decisions",
        sa.Column("confidence", sa.Float(), nullable=False),
    )
    op.create_foreign_key(
        "fk_lifecycle_decisions_event_id",
        "lifecycle_decisions",
        "learning_events",
        ["event_id"],
        ["event_id"],
    )
    op.create_check_constraint(
        "ck_lifecycle_decisions_trace_nonempty",
        "lifecycle_decisions",
        "btrim(trace_id) <> ''",
    )
    op.create_check_constraint(
        "ck_lifecycle_decisions_input_object",
        "lifecycle_decisions",
        "jsonb_typeof(input_summary) = 'object'",
    )
    op.create_check_constraint(
        "ck_lifecycle_decisions_candidates_array",
        "lifecycle_decisions",
        "jsonb_typeof(candidate_memory_ids) = 'array'",
    )
    op.create_check_constraint(
        "ck_lifecycle_decisions_operation",
        "lifecycle_decisions",
        "operation IN ('ADD', 'NO_OP', 'MERGE', 'SUPERSEDE', 'INVALIDATE', 'CONTESTED')",
    )
    op.create_check_constraint(
        "ck_lifecycle_decisions_confidence_range",
        "lifecycle_decisions",
        "confidence >= 0.0 AND confidence <= 1.0",
    )
    op.create_index(
        "ix_lifecycle_decisions_trace_created",
        "lifecycle_decisions",
        ["trace_id", "created_at", "decision_id"],
    )

    op.add_column(
        "memory_change_log",
        sa.Column("decision_id", sa.Text(), nullable=False),
    )
    op.add_column(
        "memory_change_log",
        sa.Column("memory_id", sa.Text(), nullable=True),
    )
    op.add_column(
        "memory_change_log",
        sa.Column("expected_row_version", sa.Integer(), nullable=True),
    )
    op.add_column(
        "memory_change_log",
        sa.Column("actual_row_version", sa.Integer(), nullable=True),
    )
    op.add_column(
        "memory_change_log",
        sa.Column("error_code", sa.Text(), nullable=True),
    )
    op.create_foreign_key(
        "fk_memory_change_log_decision_id",
        "memory_change_log",
        "lifecycle_decisions",
        ["decision_id"],
        ["decision_id"],
    )
    op.create_foreign_key(
        "fk_memory_change_log_memory_id",
        "memory_change_log",
        "learning_memories",
        ["memory_id"],
        ["memory_id"],
    )
    op.create_check_constraint(
        "ck_memory_change_log_trace_nonempty",
        "memory_change_log",
        "btrim(trace_id) <> ''",
    )
    op.create_check_constraint(
        "ck_memory_change_log_before_object",
        "memory_change_log",
        "before_state IS NULL OR jsonb_typeof(before_state) = 'object'",
    )
    op.create_check_constraint(
        "ck_memory_change_log_after_object",
        "memory_change_log",
        "after_state IS NULL OR jsonb_typeof(after_state) = 'object'",
    )
    op.create_check_constraint(
        "ck_memory_change_log_apply_state",
        "memory_change_log",
        "apply_state IN ('PLANNED', 'APPLIED', 'IDEMPOTENT', 'CONTESTED', 'STALE', 'FAILED')",
    )
    op.create_check_constraint(
        "ck_memory_change_log_expected_row_version",
        "memory_change_log",
        "expected_row_version IS NULL OR expected_row_version >= 1",
    )
    op.create_check_constraint(
        "ck_memory_change_log_actual_row_version",
        "memory_change_log",
        "actual_row_version IS NULL OR actual_row_version >= 1",
    )
    op.create_check_constraint(
        "ck_memory_change_log_failed_error",
        "memory_change_log",
        "apply_state <> 'FAILED' OR error_code IS NOT NULL",
    )
    op.create_check_constraint(
        "ck_memory_change_log_error_only_on_failure",
        "memory_change_log",
        "apply_state = 'FAILED' OR error_code IS NULL",
    )
    op.create_check_constraint(
        "ck_memory_change_log_success_after",
        "memory_change_log",
        "apply_state NOT IN ('APPLIED', 'CONTESTED') "
        "OR (memory_id IS NOT NULL AND after_state IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_memory_change_log_stale_versions",
        "memory_change_log",
        "apply_state <> 'STALE' "
        "OR (expected_row_version IS NOT NULL AND actual_row_version IS NOT NULL)",
    )
    op.create_index(
        "ix_memory_change_log_decision_created",
        "memory_change_log",
        ["decision_id", "created_at", "change_id"],
    )
    op.create_index(
        "ix_memory_change_log_trace_created",
        "memory_change_log",
        ["trace_id", "created_at", "change_id"],
    )


def downgrade() -> None:
    _require_empty_audit_tables("downgrade")

    op.drop_index("ix_memory_change_log_trace_created", table_name="memory_change_log")
    op.drop_index("ix_memory_change_log_decision_created", table_name="memory_change_log")
    for constraint_name in (
        "ck_memory_change_log_stale_versions",
        "ck_memory_change_log_success_after",
        "ck_memory_change_log_error_only_on_failure",
        "ck_memory_change_log_failed_error",
        "ck_memory_change_log_actual_row_version",
        "ck_memory_change_log_expected_row_version",
        "ck_memory_change_log_apply_state",
        "ck_memory_change_log_after_object",
        "ck_memory_change_log_before_object",
        "ck_memory_change_log_trace_nonempty",
    ):
        op.drop_constraint(constraint_name, "memory_change_log", type_="check")
    op.drop_constraint(
        "fk_memory_change_log_memory_id",
        "memory_change_log",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_memory_change_log_decision_id",
        "memory_change_log",
        type_="foreignkey",
    )
    for column_name in (
        "error_code",
        "actual_row_version",
        "expected_row_version",
        "memory_id",
        "decision_id",
    ):
        op.drop_column("memory_change_log", column_name)

    op.drop_index("ix_lifecycle_decisions_trace_created", table_name="lifecycle_decisions")
    for constraint_name in (
        "ck_lifecycle_decisions_confidence_range",
        "ck_lifecycle_decisions_operation",
        "ck_lifecycle_decisions_candidates_array",
        "ck_lifecycle_decisions_input_object",
        "ck_lifecycle_decisions_trace_nonempty",
    ):
        op.drop_constraint(constraint_name, "lifecycle_decisions", type_="check")
    op.drop_constraint(
        "fk_lifecycle_decisions_event_id",
        "lifecycle_decisions",
        type_="foreignkey",
    )
    for column_name in ("confidence", "event_id", "trace_id"):
        op.drop_column("lifecycle_decisions", column_name)
