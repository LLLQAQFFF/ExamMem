"""Stage-three configuration contract for the ExamMem runtime surface."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from exam_mem.backends import BackendMode


class ExamMemCapabilitySettings(BaseModel):
    """Feature flags frozen by the stage-three design document."""

    model_config = ConfigDict(extra="forbid")

    exam_practice: bool = True
    native_chat: bool = True
    knowledge_base: bool = True
    native_quiz: bool = True
    deep_research: bool = False
    book: bool = False
    cowriter: bool = False
    visualize: bool = False
    partners: bool = False


class ExamMemSettings(BaseModel):
    """Validated ExamMem settings before DeepTutor runtime integration."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    subject: str = "postgraduate_math_1"
    memory_backend: BackendMode = BackendMode.NATIVE
    capabilities: ExamMemCapabilitySettings = Field(default_factory=ExamMemCapabilitySettings)


__all__ = ["ExamMemCapabilitySettings", "ExamMemSettings"]
