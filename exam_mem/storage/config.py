"""Secret-safe PostgreSQL connection settings for Learning Memory."""

from __future__ import annotations

from typing import Any

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

ASYNC_POSTGRESQL_DRIVER = "postgresql+asyncpg"
DEFAULT_POSTGRESQL_PORT = 5432


class DatabaseSettings(BaseSettings):
    """Load the Learning Memory DSN from process environment only."""

    model_config = SettingsConfigDict(
        env_prefix="EXAM_MEM_",
        extra="ignore",
        frozen=True,
    )

    database_url: SecretStr

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr) -> SecretStr:
        raw_url = value.get_secret_value().strip()
        if not raw_url:
            raise ValueError("EXAM_MEM_DATABASE_URL must not be empty")
        try:
            parsed = make_url(raw_url)
        except ArgumentError as exc:
            raise ValueError("EXAM_MEM_DATABASE_URL must be a valid SQLAlchemy URL") from exc
        if parsed.drivername != ASYNC_POSTGRESQL_DRIVER:
            raise ValueError("EXAM_MEM_DATABASE_URL must use the postgresql+asyncpg driver")
        missing_parts = [
            name
            for name, part in (
                ("username", parsed.username),
                ("password", parsed.password),
                ("host", parsed.host),
                ("database", parsed.database),
            )
            if not part
        ]
        if missing_parts:
            raise ValueError(
                "EXAM_MEM_DATABASE_URL is missing required parts: " + ", ".join(missing_parts)
            )
        return SecretStr(raw_url)

    def sqlalchemy_url(self) -> str:
        """Return the DSN only at the engine-construction boundary."""
        return self.database_url.get_secret_value()

    def safe_summary(self) -> dict[str, Any]:
        """Return connection metadata that cannot reveal the password."""
        parsed = make_url(self.sqlalchemy_url())
        return {
            "driver": parsed.drivername,
            "host": parsed.host or "",
            "port": parsed.port or DEFAULT_POSTGRESQL_PORT,
            "database": parsed.database or "",
        }


def load_database_settings() -> DatabaseSettings:
    """Resolve the required database settings without fallback or persistence."""
    return DatabaseSettings()  # type: ignore[call-arg]


__all__ = [
    "ASYNC_POSTGRESQL_DRIVER",
    "DEFAULT_POSTGRESQL_PORT",
    "DatabaseSettings",
    "load_database_settings",
]
