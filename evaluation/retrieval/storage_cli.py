"""Command-line entry point for the serial retrieval-v2 storage evaluation."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from evaluation.backend_adapters import ConfiguredHostEmbeddingClient
from evaluation.retrieval.contracts import RetrievalDataset
from evaluation.retrieval.dataset_builder import build_scale_corpus
from evaluation.retrieval.metrics import compute_storage_metrics
from evaluation.retrieval.storage_runner import (
    ingest_dataset,
    ingest_scale_corpus,
    run_invariant_rejection_trials,
)
from exam_mem.storage.models import learning_events, learning_memories


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("evaluation/datasets/semantic_retrieval_v2/test.json"),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scale-size", type=int, default=10_000)
    parser.add_argument("--skip-scale", action="store_true")
    return parser


async def _run(args: argparse.Namespace) -> dict[str, object]:
    database_url = os.environ.get("EXAM_MEM_DATABASE_URL")
    if not database_url:
        raise ValueError("EXAM_MEM_DATABASE_URL is required")
    dataset = RetrievalDataset.model_validate_json(args.dataset.read_text(encoding="utf-8"))
    embedding_client = ConfiguredHostEmbeddingClient()
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            database_name = str(await connection.scalar(text("select current_database()")))
            if not database_name.startswith("exammem_retrieval_v2"):
                raise ValueError("storage evaluation requires an exammem_retrieval_v2 database")
            if await connection.scalar(select(func.count()).select_from(learning_memories)):
                raise ValueError("storage evaluation requires an empty learning_memories table")
            await connection.commit()

            def semantic_progress(done: int, total: int) -> None:
                if done == total or done % 100 == 0:
                    print(f"semantic storage: {done}/{total}", flush=True)

            semantic = await ingest_dataset(
                connection,
                dataset,
                embedding_client,
                batch_size=32,
                progress=semantic_progress,
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
            storage_metrics = compute_storage_metrics([*semantic.observations, *invalid])

            scale = None
            if not args.skip_scale:
                scale_records = build_scale_corpus(args.scale_size)

                def scale_progress(done: int, total: int) -> None:
                    if done == total or done % 1000 == 0:
                        print(f"scale storage: {done}/{total}", flush=True)

                scale = await ingest_scale_corpus(
                    connection,
                    scale_records,
                    embedding_client,
                    batch_size=64,
                    transaction_size=240,
                    progress=scale_progress,
                )
            memory_count = int(
                await connection.scalar(select(func.count()).select_from(learning_memories)) or 0
            )
            event_count = int(
                await connection.scalar(select(func.count()).select_from(learning_events)) or 0
            )
            migration_head = str(
                await connection.scalar(text("select version_num from alembic_version"))
            )
            table_bytes = int(
                await connection.scalar(text("select pg_total_relation_size('learning_memories')"))
                or 0
            )
            index_rows = (
                await connection.execute(
                    text(
                        "select indexrelname, pg_relation_size(indexrelid) "
                        "from pg_stat_user_indexes where relname='learning_memories' "
                        "order by indexrelname"
                    )
                )
            ).all()
    finally:
        await engine.dispose()

    return {
        "protocol_version": dataset.protocol_version,
        "dataset_sha256": dataset.canonical_hash(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "database_name": database_name,
        "migration_head": migration_head,
        "embedding_provider": embedding_client.version,
        "embedding_call_count": embedding_client.call_count,
        "semantic": {
            "memory_count": semantic.memory_count,
            "event_count": semantic.event_count,
            "embedding_dimension": semantic.embedding_dimension,
            "elapsed_ms": semantic.elapsed_ms,
            "observations": [item.model_dump(mode="json") for item in semantic.observations],
        },
        "invalid_observations": [item.model_dump(mode="json") for item in invalid],
        "storage_metrics": [score.model_dump(mode="json") for score in storage_metrics],
        "scale": (
            {
                "memory_count": scale.memory_count,
                "event_count": scale.event_count,
                "embedding_dimension": scale.embedding_dimension,
                "elapsed_ms": scale.elapsed_ms,
            }
            if scale is not None
            else None
        ),
        "database_counts": {"learning_memories": memory_count, "learning_events": event_count},
        "learning_memories_total_bytes": table_bytes,
        "learning_memories_indexes": {
            str(index_name): int(size_bytes) for index_name, size_bytes in index_rows
        },
    }


def main() -> None:
    args = _parser().parse_args()
    report = asyncio.run(_run(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"storage report: {args.output}", flush=True)


if __name__ == "__main__":
    main()
