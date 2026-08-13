"""Stage-three configuration contract for the ExamMem runtime surface."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from exam_mem.backends import BackendMode


class ExamMemCapabilitySettings(BaseModel):
    """ExamMem-owned capability switches; Host capabilities are not mirrored here."""

    model_config = ConfigDict(extra="forbid")

    exam_practice: bool = True


class ExamMemSettings(BaseModel):
    """Validated ExamMem settings before DeepTutor runtime integration."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    subject: str = "postgraduate_math_1"
    memory_backend: BackendMode = BackendMode.LIFECYCLE
    capabilities: ExamMemCapabilitySettings = Field(default_factory=ExamMemCapabilitySettings)


__all__ = ["ExamMemCapabilitySettings", "ExamMemSettings"]
