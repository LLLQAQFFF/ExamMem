from __future__ import annotations

from typing import Any

import pytest

from exam_mem.backends import (
    BackendConfigurationError,
    BackendMode,
    MemoryBackend,
    NoMemoryBackend,
    build_memory_backend,
    validate_runtime_backend_mode,
)

pytestmark = pytest.mark.backend_mode


class FakeBackend:
    def __init__(self, mode: BackendMode) -> None:
        self.mode = mode

    async def record_event(self, event: object) -> None:
        return None

    async def update(self, event: object, candidates: list[object]) -> list[object]:
        return []

    async def query_state(self, context: object) -> None:
        return None

    async def retrieve(self, scope: object, query: str, top_k: int) -> list[object]:
        return []

    async def snapshot(self, context: object) -> dict[str, Any]:
        return {"mode": self.mode.value}


@pytest.mark.parametrize("mode", list(BackendMode))
def test_factory_builds_every_frozen_mode(mode: BackendMode) -> None:
    calls: list[BackendMode] = []

    def provider() -> MemoryBackend:
        calls.append(mode)
        return FakeBackend(mode)

    backend = build_memory_backend(mode, {mode: provider})

    assert isinstance(backend, MemoryBackend)
    if mode is BackendMode.NONE:
        assert isinstance(backend, NoMemoryBackend)
        assert calls == []
    else:
        assert isinstance(backend, FakeBackend)
        assert backend.mode is mode
        assert calls == [mode]


def test_factory_only_initializes_the_selected_provider() -> None:
    calls: list[BackendMode] = []

    def provider(mode: BackendMode):
        def build() -> MemoryBackend:
            calls.append(mode)
            return FakeBackend(mode)

        return build

    providers = {mode: provider(mode) for mode in BackendMode if mode is not BackendMode.NONE}

    backend = build_memory_backend(BackendMode.VECTOR, providers)

    assert isinstance(backend, FakeBackend)
    assert backend.mode is BackendMode.VECTOR
    assert calls == [BackendMode.VECTOR]


def test_unknown_mode_fails_with_the_complete_legal_value_list() -> None:
    with pytest.raises(BackendConfigurationError) as exc_info:
        build_memory_backend("legacy")

    message = str(exc_info.value)
    assert "legacy" in message
    for mode in BackendMode:
        assert mode.value in message


def test_missing_lifecycle_dependencies_fail_without_native_fallback() -> None:
    native_calls = 0

    def native_provider() -> MemoryBackend:
        nonlocal native_calls
        native_calls += 1
        return FakeBackend(BackendMode.NATIVE)

    with pytest.raises(
        BackendConfigurationError,
        match="lifecycle.*not configured.*refusing to fall back",
    ):
        build_memory_backend(
            BackendMode.LIFECYCLE,
            {BackendMode.NATIVE: native_provider},
        )

    assert native_calls == 0


@pytest.mark.asyncio
async def test_none_backend_has_no_state_or_updates() -> None:
    backend = NoMemoryBackend()

    await backend.record_event(None)  # type: ignore[arg-type]

    assert await backend.update(None, []) == []  # type: ignore[arg-type]
    assert await backend.query_state(None) is None  # type: ignore[arg-type]
    assert await backend.retrieve(None, "query", 5) == []  # type: ignore[arg-type]
    assert await backend.snapshot(None) == {}  # type: ignore[arg-type]


def test_factory_rejects_a_provider_that_breaks_the_protocol() -> None:
    with pytest.raises(BackendConfigurationError, match="does not implement MemoryBackend"):
        build_memory_backend(BackendMode.NATIVE, {BackendMode.NATIVE: object})


@pytest.mark.parametrize("mode", list(BackendMode))
def test_runtime_accepts_all_modes_with_real_entry_providers(mode: BackendMode) -> None:
    assert validate_runtime_backend_mode(mode) is mode
