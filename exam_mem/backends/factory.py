"""Single construction boundary for the five frozen memory experiment arms."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from pydantic import JsonValue

from exam_mem.contracts import (
    LearningContext,
    LearningEvent,
    LearningMemory,
    LifecycleDecision,
    MemoryScope,
    MemoryUpdateCandidate,
    StudentModel,
)

from .protocol import BackendMode, MemoryBackend

BackendProvider = Callable[[], MemoryBackend]


class BackendConfigurationError(RuntimeError):
    """Raised when the selected experiment arm cannot be built exactly."""


class NoMemoryBackend:
    """The ``none`` baseline: accept calls without retaining or returning state."""

    async def record_event(self, event: LearningEvent) -> None:
        return None

    async def update(
        self,
        event: LearningEvent,
        candidates: list[MemoryUpdateCandidate],
    ) -> list[LifecycleDecision]:
        return []

    async def query_state(self, context: LearningContext) -> StudentModel | None:
        return None

    async def retrieve(
        self,
        scope: MemoryScope,
        query: str,
        top_k: int,
    ) -> list[LearningMemory]:
        return []

    async def snapshot(self, context: LearningContext) -> dict[str, JsonValue]:
        return {}


def _resolve_mode(mode: BackendMode | str) -> BackendMode:
    if isinstance(mode, BackendMode):
        return mode
    try:
        return BackendMode(mode)
    except ValueError as exc:
        legal_values = ", ".join(item.value for item in BackendMode)
        raise BackendConfigurationError(
            f"Unknown memory backend {mode!r}; expected one of: {legal_values}"
        ) from exc


def build_memory_backend(
    mode: BackendMode | str,
    providers: Mapping[BackendMode, BackendProvider] | None = None,
) -> MemoryBackend:
    """Build exactly one backend and never fall back to another experiment arm."""
    resolved_mode = _resolve_mode(mode)
    if resolved_mode is BackendMode.NONE:
        return NoMemoryBackend()

    provider = (providers or {}).get(resolved_mode)
    if provider is None:
        raise BackendConfigurationError(
            f"Memory backend {resolved_mode.value!r} is not configured; "
            "refusing to fall back to another mode"
        )

    backend = provider()
    if not isinstance(backend, MemoryBackend):
        raise BackendConfigurationError(
            f"Provider for memory backend {resolved_mode.value!r} returned an "
            "object that does not implement MemoryBackend"
        )
    return backend


def validate_runtime_backend_mode(mode: BackendMode | str) -> BackendMode:
    """Validate one of the five modes now wired by the Practice provider."""
    return _resolve_mode(mode)


__all__ = [
    "BackendConfigurationError",
    "BackendProvider",
    "NoMemoryBackend",
    "build_memory_backend",
    "validate_runtime_backend_mode",
]
