from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest

pytestmark = [pytest.mark.database, pytest.mark.migration]

PROJECT_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"


def _script_directory() -> ScriptDirectory:
    return ScriptDirectory.from_config(Config(str(ALEMBIC_INI)))


def test_alembic_configuration_does_not_store_a_database_url() -> None:
    configuration = ALEMBIC_INI.read_text(encoding="utf-8")

    assert "sqlalchemy.url" not in configuration
    assert "postgresql+asyncpg://" not in configuration


def test_migration_chain_has_one_linear_head() -> None:
    scripts = _script_directory()

    assert scripts.get_heads() == ["0010_learning_observations"]
    assert scripts.get_revision("0001_learning_memory_schema").down_revision is None
    assert (
        scripts.get_revision("0002_append_only_records").down_revision
        == "0001_learning_memory_schema"
    )
    assert (
        scripts.get_revision("0003_defer_superseded_by_fk").down_revision
        == "0002_append_only_records"
    )
    assert (
        scripts.get_revision("0004_lifecycle_audit_contract").down_revision
        == "0003_defer_superseded_by_fk"
    )
    assert (
        scripts.get_revision("0005_practice_backend_facts").down_revision
        == "0004_lifecycle_audit_contract"
    )
    assert (
        scripts.get_revision("0006_practice_workflow").down_revision
        == "0005_practice_backend_facts"
    )
    assert scripts.get_revision("0007_grade_reviews").down_revision == "0006_practice_workflow"
    assert scripts.get_revision("0008_study_plans").down_revision == "0007_grade_reviews"
    assert scripts.get_revision("0009_assessments").down_revision == "0008_study_plans"
    assert scripts.get_revision("0010_learning_observations").down_revision == "0009_assessments"


def test_revision_ids_fit_the_alembic_version_column() -> None:
    scripts = _script_directory()

    assert all(len(revision.revision) <= 32 for revision in scripts.walk_revisions())


def test_initial_migration_renders_the_frozen_schema_offline() -> None:
    password = "offline-migration-contract-password"
    environment = os.environ.copy()
    environment["EXAM_MEM_DATABASE_URL"] = (
        f"postgresql+asyncpg://exammem:{password}@127.0.0.1:55432/exammem"
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(ALEMBIC_INI),
            "upgrade",
            "head",
            "--sql",
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    rendered = result.stdout.lower()

    assert result.returncode == 0, result.stderr
    assert "create extension if not exists vector" in rendered
    assert rendered.count("create table ") == 22
    for table_name in (
        "alembic_version",
        "learning_events",
        "learning_memories",
        "event_correction_targets",
        "event_plan_transition_targets",
        "memory_provenance",
        "student_model_snapshots",
        "lifecycle_decisions",
        "memory_change_log",
        "baseline_memory_facts",
        "practice_workflow_checkpoints",
        "practice_trace_spans",
        "grade_review_events",
        "study_plans",
        "study_plan_drafts",
        "study_plan_versions",
        "study_objective_sessions",
        "assessments",
        "assessment_versions",
        "assessment_attempts",
        "learning_observations",
        "learning_observation_actions",
    ):
        assert f"create table {table_name}" in rendered
    assert "vector(1024)" in rendered
    assert "using hnsw (content_embedding vector_cosine_ops)" in rendered
    assert "create function exam_mem_reject_append_only_mutation" in rendered
    assert rendered.count("create trigger tr_") == 10
    assert "deferrable initially deferred" in rendered
    assert "add column trace_id text not null" in rendered
    assert "add column decision_id text not null" in rendered
    assert "ck_memory_change_log_apply_state" in rendered
    assert "cannot upgrade lifecycle audit contract" in rendered
    assert "pk_baseline_memory_facts" in rendered
    assert "ck_baseline_memory_facts_embedding_mode" in rendered
    assert "tr_baseline_memory_facts_append_only" in rendered
    assert "pk_practice_workflow_checkpoints" in rendered
    assert "tr_practice_trace_spans_append_only" in rendered
    assert "tr_grade_review_events_append_only" in rendered
    assert "tr_study_plan_versions_append_only" in rendered
    assert "tr_assessment_versions_append_only" in rendered
    assert "tr_learning_observations_append_only" in rendered
    assert "tr_learning_observation_actions_append_only" in rendered
    assert password not in result.stdout
    assert password not in result.stderr


def test_audit_downgrade_refuses_to_discard_existing_rows() -> None:
    revision = _script_directory().get_revision("0004_lifecycle_audit_contract")
    migration_source = Path(revision.path).read_text(encoding="utf-8")
    downgrade_source = migration_source.split("def downgrade() -> None:", maxsplit=1)[1]

    assert '_require_empty_audit_tables("downgrade")' in downgrade_source
    assert "drop_column" in downgrade_source


def test_baseline_fact_downgrade_refuses_to_discard_existing_rows() -> None:
    revision = _script_directory().get_revision("0005_practice_backend_facts")
    migration_source = Path(revision.path).read_text(encoding="utf-8")
    downgrade_source = migration_source.split("def downgrade() -> None:", maxsplit=1)[1]

    assert '_require_empty_baseline_facts("downgrade")' in downgrade_source
    assert 'op.drop_table("baseline_memory_facts")' in downgrade_source


def test_practice_runtime_downgrade_refuses_to_discard_existing_rows() -> None:
    revision = _script_directory().get_revision("0006_practice_workflow")
    migration_source = Path(revision.path).read_text(encoding="utf-8")
    downgrade_source = migration_source.split("def downgrade() -> None:", maxsplit=1)[1]

    assert '_require_empty_practice_runtime_tables("downgrade")' in downgrade_source
    assert 'op.drop_table("practice_trace_spans")' in downgrade_source
    assert 'op.drop_table("practice_workflow_checkpoints")' in downgrade_source


def test_grade_review_downgrade_refuses_to_discard_existing_rows() -> None:
    revision = _script_directory().get_revision("0007_grade_reviews")
    migration_source = Path(revision.path).read_text(encoding="utf-8")
    downgrade_source = migration_source.split("def downgrade() -> None:", maxsplit=1)[1]

    assert "SELECT 1 FROM grade_review_events" in downgrade_source
    assert 'op.drop_table("grade_review_events")' in downgrade_source


@pytest.mark.parametrize(
    ("revision_id", "guard", "table_name"),
    [
        ("0008_study_plans", "cannot downgrade study plan contract", "study_plans"),
        ("0009_assessments", "cannot downgrade assessment contract", "assessments"),
        (
            "0010_learning_observations",
            "cannot downgrade learning observation contract",
            "learning_observations",
        ),
    ],
)
def test_product_downgrades_refuse_to_discard_rows(
    revision_id: str, guard: str, table_name: str
) -> None:
    revision = _script_directory().get_revision(revision_id)
    migration_source = Path(revision.path).read_text(encoding="utf-8")
    downgrade_source = migration_source.split("def downgrade() -> None:", maxsplit=1)[1]

    assert guard in downgrade_source
    assert f'op.drop_table("{table_name}")' in downgrade_source


def test_downgrade_keeps_the_shared_vector_extension() -> None:
    revision = _script_directory().get_revision("0001_learning_memory_schema")
    migration_source = Path(revision.path).read_text(encoding="utf-8")
    downgrade_source = migration_source.split("def downgrade() -> None:", maxsplit=1)[1]

    assert "drop_table" in downgrade_source
    assert "DROP EXTENSION" not in downgrade_source.upper()
