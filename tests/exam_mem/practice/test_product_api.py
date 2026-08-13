from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from deeptutor.multi_user.context import reset_current_user, set_current_user
from deeptutor.multi_user.models import CurrentUser, UserScope
from deeptutor.plugins import SettingsContribution
from deeptutor_plugins.exam_mem.api import build_router
from exam_mem.config import ExamMemSettings


@contextmanager
def _regular_user():
    user = CurrentUser(
        id="regular-user",
        username="regular",
        role="user",
        scope=UserScope(kind="user", user_id="regular-user", root=Path("/tmp/regular")),  # noqa: S108
    )
    token = set_current_user(user)
    try:
        yield
    finally:
        reset_current_user(token)


@pytest.mark.asyncio
async def test_non_admin_cannot_save_plugin_configuration(monkeypatch) -> None:
    saved = False

    def fail_if_saved(*_args, **_kwargs):  # noqa: ANN202
        nonlocal saved
        saved = True
        raise AssertionError("non-admin request reached settings persistence")

    monkeypatch.setattr(
        "deeptutor_plugins.exam_mem.api.save_plugin_settings", fail_if_saved
    )
    contribution = SettingsContribution(
        namespace="exam_mem",
        defaults=ExamMemSettings().model_dump(mode="json"),
        normalize=lambda value: ExamMemSettings.model_validate(value).model_dump(mode="json"),
    )
    api = FastAPI()
    api.include_router(
        build_router(
            object(),  # type: ignore[arg-type]
            settings_contribution=contribution,
            effective_settings=ExamMemSettings(),
        ),
        prefix="/api/v1/exam-mem",
    )

    with _regular_user():
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url="http://test"
        ) as client:
            response = await client.put(
                "/api/v1/exam-mem/configuration",
                json=ExamMemSettings(memory_backend="none").model_dump(mode="json"),
            )

    assert response.status_code == 403
    assert saved is False
