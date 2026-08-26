"""Allow study plans generated from immutable textbook scopes.

Revision ID: 0015_textbook_plan_source
Revises: 0014_textbook_grounding
Create Date: 2026-08-26
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0015_textbook_plan_source"
down_revision: Union[str, Sequence[str], None] = "0014_textbook_grounding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table, constraint in (
        ("study_plan_drafts", "ck_study_plan_drafts_source_kind"),
        ("study_plan_versions", "ck_study_plan_versions_source_kind"),
    ):
        op.drop_constraint(constraint, table, type_="check")
        op.create_check_constraint(
            constraint,
            table,
            "source_kind IN ('file', 'url', 'generated', 'textbook')",
        )


def downgrade() -> None:
    op.execute(
        """DO $$ BEGIN IF EXISTS (SELECT 1 FROM study_plan_drafts WHERE source_kind = 'textbook' LIMIT 1) OR EXISTS (SELECT 1 FROM study_plan_versions WHERE source_kind = 'textbook' LIMIT 1) THEN RAISE EXCEPTION 'cannot downgrade textbook plan source while textbook plans exist'; END IF; END; $$"""
    )
    for table, constraint in (
        ("study_plan_drafts", "ck_study_plan_drafts_source_kind"),
        ("study_plan_versions", "ck_study_plan_versions_source_kind"),
    ):
        op.drop_constraint(constraint, table, type_="check")
        op.create_check_constraint(
            constraint,
            table,
            "source_kind IN ('file', 'url', 'generated')",
        )
