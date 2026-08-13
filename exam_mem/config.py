"""Stage-three configuration contract for the ExamMem runtime surface."""

from __future__ import annotations

import hashlib
import json

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


_BACKEND_SIDE_EFFECTS: dict[BackendMode, tuple[str, ...]] = {
    BackendMode.NONE: ("checkpoint", "practice_trace"),
    BackendMode.NATIVE: ("checkpoint", "practice_trace", "deep_tutor_native_memory"),
    BackendMode.APPEND_ONLY: ("checkpoint", "practice_trace", "learning_event_l1"),
    BackendMode.VECTOR: (
        "checkpoint",
        "practice_trace",
        "learning_event_l1",
        "vector_baseline_fact",
    ),
    BackendMode.LIFECYCLE: (
        "checkpoint",
        "practice_trace",
        "learning_event_l1",
        "learning_memory_l2",
        "decision_journal",
        "change_log",
        "student_model_l3",
    ),
}


def settings_revision(settings: ExamMemSettings) -> str:
    payload = json.dumps(
        settings.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def backend_side_effects(mode: BackendMode) -> tuple[str, ...]:
    return _BACKEND_SIDE_EFFECTS[mode]


__all__ = [
    "ExamMemCapabilitySettings",
    "ExamMemSettings",
    "backend_side_effects",
    "settings_revision",
]
