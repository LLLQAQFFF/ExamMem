from __future__ import annotations

from pathlib import Path
import subprocess
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_server_requirements_include_exam_mem_database_runtime() -> None:
    requirements = (PROJECT_ROOT / "requirements" / "server.txt").read_text(encoding="utf-8")

    for dependency in ("SQLAlchemy", "alembic", "asyncpg", "pgvector"):
        assert dependency in requirements


def test_production_image_copies_exam_mem_plugin_and_migrations() -> None:
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY deeptutor_plugins/ ./deeptutor_plugins/" in dockerfile
    assert "COPY exam_mem/ ./exam_mem/" in dockerfile
    assert "COPY alembic.ini ./" in dockerfile


def test_packaged_migration_entrypoint_resolves_the_single_head() -> None:
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    packaged_config = PROJECT_ROOT / "exam_mem" / "storage" / "alembic.ini"

    assert packaged_config.is_file()
    assert '"storage/alembic.ini"' in pyproject
    result = subprocess.run(
        [sys.executable, "-m", "exam_mem.storage.migrations", "heads"],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0011_assessment_archival (head)"


def test_public_ci_runs_exam_mem_changes_against_postgresql() -> None:
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")

    assert '"deeptutor_plugins/**"' in workflow
    assert '"exam_mem/**"' in workflow
    assert "pgvector/pgvector:0.8.2-pg16-bookworm" in workflow
    assert "EXAM_MEM_DATABASE_URL:" in workflow
    assert "python -m alembic -c alembic.ini upgrade head" in workflow
