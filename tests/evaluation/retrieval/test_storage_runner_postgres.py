from __future__ import annotations

import os

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from evaluation.backend_adapters import DeterministicHashEmbeddingClient
from evaluation.retrieval.contracts import RetrievalSplit
from evaluation.retrieval.dataset_builder import build_merged_dataset
from evaluation.retrieval.metrics import compute_storage_metrics
from evaluation.retrieval.storage_runner import (
    ingest_dataset,
    run_invariant_rejection_trials,
)
from exam_mem.storage.models import learning_events, learning_memories


def _database_url_or_skip() -> str:
    database_url = os.environ.get("EXAM_MEM_DATABASE_URL")
    if not database_url:
        pytest.skip("EXAM_MEM_DATABASE_URL is required for PostgreSQL integration tests")
    return database_url


@pytest.mark.asyncio
async def test_serial_ingestion_replay_round_trip_and_invalid_writes() -> None:
    engine = create_async_engine(_database_url_or_skip())
    try:
        async with engine.connect() as connection:
            database_name = await connection.scalar(text("select current_database()"))
            assert str(database_name).startswith("exammem_retrieval_v2_tests")
            assert await connection.scalar(select(func.count()).select_from(learning_memories)) == 0
            await connection.commit()

            dataset = build_merged_dataset(RetrievalSplit.TEST)
            embedding_client = DeterministicHashEmbeddingClient()
            result = await ingest_dataset(
                connection,
                dataset,
                embedding_client,
                batch_size=64,
            )
            valid_vector = (
                await embedding_client.embed(
                    [dataset.corpus[0].embedding_text],
                    input_type="search_document",
                )
            )[0]
            invalid = await run_invariant_rejection_trials(
                connection,
                dataset,
                valid_vector,
                trial_count=32,
            )
            scores = {
                score.metric_id: score
                for score in compute_storage_metrics([*result.observations, *invalid])
            }

            assert result.memory_count == len(dataset.corpus) == 396
            assert (
                await connection.scalar(select(func.count()).select_from(learning_memories)) == 396
            )
            assert await connection.scalar(select(func.count()).select_from(learning_events)) == (
                len(
                    {
                        event.event_id
                        for record in dataset.corpus
                        for event in record.provenance_events
                    }
                )
            )
            assert all(score.value == 1.0 for score in scores.values())
            assert scores["storage.acceptance_rate"].sample_count == 396
            assert scores["storage.idempotency_rate"].sample_count == 396
            assert scores["storage.invariant_rejection_rate"].sample_count == 32
    finally:
        await engine.dispose()
