from __future__ import annotations

from pydantic import ValidationError
import pytest

from exam_mem.backends import BackendMode
from exam_mem.config import ExamMemSettings


@pytest.mark.schema
def test_exam_mem_defaults_define_the_stage_three_runtime_surface() -> None:
    settings = ExamMemSettings()

    assert settings.enabled is True
    assert settings.subject == "postgraduate_math_1"
    assert settings.memory_backend is BackendMode.LIFECYCLE
    assert settings.capabilities.model_dump() == {"exam_practice": True}


@pytest.mark.schema
@pytest.mark.parametrize("mode", list(BackendMode))
def test_exam_mem_accepts_every_frozen_backend_mode(mode: BackendMode) -> None:
    settings = ExamMemSettings.model_validate({"memory_backend": mode.value})

    assert settings.memory_backend is mode


@pytest.mark.schema
def test_exam_mem_accepts_the_documented_lifecycle_configuration() -> None:
    settings = ExamMemSettings.model_validate(
        {
            "enabled": True,
            "subject": "postgraduate_math_1",
            "memory_backend": "lifecycle",
            "capabilities": {"exam_practice": True},
        }
    )

    assert settings.memory_backend is BackendMode.LIFECYCLE


@pytest.mark.schema
def test_exam_mem_rejects_an_unknown_backend_mode() -> None:
    with pytest.raises(ValidationError, match="memory_backend"):
        ExamMemSettings.model_validate({"memory_backend": "fallback"})


@pytest.mark.schema
@pytest.mark.parametrize(
    "payload",
    [
        {"unexpected": True},
        {"capabilities": {"unexpected": True}},
    ],
)
def test_exam_mem_rejects_undocumented_fields(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ExamMemSettings.model_validate(payload)
