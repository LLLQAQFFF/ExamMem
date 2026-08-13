from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError
import pytest
import yaml

from exam_mem.storage import DatabaseSettings, load_database_settings

pytestmark = pytest.mark.database

PROJECT_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_PATH = PROJECT_ROOT / "compose.exam-mem.yaml"
ENV_EXAMPLE_PATH = PROJECT_ROOT / "artifacts" / "stage05" / "database.env.example"


def test_database_settings_fail_when_the_secret_url_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EXAM_MEM_DATABASE_URL", raising=False)

    with pytest.raises(ValidationError, match="database_url"):
        load_database_settings()


@pytest.mark.parametrize(
    "database_url",
    [
        "sqlite+aiosqlite:///exammem.db",
        "postgresql://user:password@127.0.0.1/exammem",
        "postgresql+asyncpg://user@127.0.0.1/exammem",
    ],
)
def test_database_settings_reject_non_postgresql_or_incomplete_urls(
    database_url: str,
) -> None:
    with pytest.raises(ValidationError, match="EXAM_MEM_DATABASE_URL"):
        DatabaseSettings(database_url=database_url)


def test_database_settings_keep_the_password_out_of_safe_outputs() -> None:
    password = "stage05-local-secret"
    database_url = f"postgresql+asyncpg://exammem:{password}@127.0.0.1:55432/exammem"

    settings = DatabaseSettings(database_url=database_url)

    assert settings.sqlalchemy_url() == database_url
    assert settings.safe_summary() == {
        "driver": "postgresql+asyncpg",
        "host": "127.0.0.1",
        "port": 55432,
        "database": "exammem",
    }
    assert password not in repr(settings)
    assert password not in settings.model_dump_json()


def test_compose_uses_a_pinned_pgvector_image_and_required_secret_variables() -> None:
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    postgres = compose["services"]["postgres"]

    assert postgres["image"] == "pgvector/pgvector:0.8.2-pg16-bookworm"
    assert postgres["ports"] == ["127.0.0.1:${EXAM_MEM_POSTGRES_PORT:-55432}:5432"]
    assert postgres["environment"] == {
        "POSTGRES_USER": "${EXAM_MEM_POSTGRES_USER:?Set EXAM_MEM_POSTGRES_USER}",
        "POSTGRES_PASSWORD": ("${EXAM_MEM_POSTGRES_PASSWORD:?Set EXAM_MEM_POSTGRES_PASSWORD}"),
        "POSTGRES_DB": "${EXAM_MEM_POSTGRES_DB:?Set EXAM_MEM_POSTGRES_DB}",
    }
    assert postgres["volumes"] == ["exammem-postgres-data:/var/lib/postgresql/data"]


def test_sanitized_environment_example_contains_no_values_or_connection_string() -> None:
    lines = [
        line.strip()
        for line in ENV_EXAMPLE_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]

    assert lines == [
        "EXAM_MEM_POSTGRES_USER=",
        "EXAM_MEM_POSTGRES_PASSWORD=",
        "EXAM_MEM_POSTGRES_DB=",
        "EXAM_MEM_POSTGRES_PORT=55432",
        "EXAM_MEM_DATABASE_URL=",
    ]
    assert "://" not in ENV_EXAMPLE_PATH.read_text(encoding="utf-8")
