from pathlib import Path

import pytest

from deeptutor.services.rag.pipelines.llamaindex import storage


def test_structured_metadata_filters_are_exact_and_multi_value() -> None:
    metadata = {"source_ref": "source-a", "section_key": "chapter-2"}
    assert storage._metadata_matches(metadata, {"section_key": ("chapter-1", "chapter-2")})
    assert not storage._metadata_matches(metadata, {"section_key": ("chapter-3",)})
    assert not storage._metadata_matches(metadata, {"source_ref": "source-b"})


@pytest.fixture(autouse=True)
def _clear_cache():
    storage.clear_index_cache()
    yield
    storage.clear_index_cache()


def test_loaded_index_cache_uses_small_lru(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(storage, "_load_validated_index", lambda path: object())
    paths = [tmp_path / f"kb-{index}" for index in range(4)]
    for path in paths:
        path.mkdir()
        storage._cached_index(path)

    assert len(storage._INDEX_CACHE) == storage._INDEX_CACHE_MAXSIZE
    retained_paths = {key[0] for key in storage._INDEX_CACHE}
    assert str(paths[-1].resolve()) in retained_paths
    assert str(paths[-2].resolve()) in retained_paths


def test_loaded_index_cache_expires_after_idle_ttl(monkeypatch, tmp_path: Path) -> None:
    now = [100.0]
    monkeypatch.setattr(storage.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(storage, "_INDEX_CACHE_IDLE_SECONDS", 10)
    monkeypatch.setattr(storage, "_load_validated_index", lambda path: object())
    path = tmp_path / "kb"
    path.mkdir()
    storage._cached_index(path)

    now[0] = 111.0

    assert storage.prune_index_cache() == 1
    assert not storage._INDEX_CACHE
