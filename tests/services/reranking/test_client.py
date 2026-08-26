from __future__ import annotations

import json
import math

import pytest

from deeptutor.services.reranking import (
    HostRerankingClient,
    LocalCrossEncoderRerankingClient,
    get_reranking_client,
)
from deeptutor.services.reranking import client as reranking_module

pytestmark = pytest.mark.asyncio


async def test_host_default_is_the_calibrated_local_reranker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reranking_module, "_client", None)

    client = get_reranking_client()

    assert client.version == "sentence_transformers:Qwen/Qwen3-Reranker-4B:nf4"
    assert get_reranking_client() is client


async def test_host_reranker_uses_one_strict_ordered_batch() -> None:
    calls: list[dict[str, object]] = []

    async def completion(**kwargs):  # noqa: ANN003, ANN202
        calls.append(kwargs)
        return '{"scores":[0.125,0.875]}'

    scores = await HostRerankingClient(completion).score(
        query="which memory matches?",
        documents=["first document", "second document"],
        instruction="judge direct relevance",
    )

    assert scores == [0.125, 0.875]
    assert len(calls) == 1
    assert calls[0]["temperature"] == 0.0
    payload = json.loads(str(calls[0]["prompt"]))
    assert payload["query"] == "which memory matches?"
    assert payload["documents"] == [
        {"document_number": 1, "text": "first document"},
        {"document_number": 2, "text": "second document"},
    ]
    response_format = calls[0]["response_format"]
    schema = response_format["json_schema"]["schema"]  # type: ignore[index]
    assert schema["properties"]["scores"]["minItems"] == 2
    assert schema["properties"]["scores"]["maxItems"] == 2


async def test_host_reranker_retries_only_the_bounded_invalid_response() -> None:
    responses = iter(['{"scores":[0.5]}', '{"scores":[0.2,0.8]}'])

    async def completion(**kwargs):  # noqa: ANN003, ANN202
        del kwargs
        return next(responses)

    scores = await HostRerankingClient(completion).score(
        query="query",
        documents=["one", "two"],
        instruction="instruction",
    )

    assert scores == [0.2, 0.8]


@pytest.mark.parametrize(
    ("query", "documents", "instruction"),
    [
        ("", ["document"], "instruction"),
        ("query", [], "instruction"),
        ("query", ["  "], "instruction"),
        ("query", ["document"], "  "),
    ],
)
async def test_host_reranker_rejects_blank_inputs(
    query: str,
    documents: list[str],
    instruction: str,
) -> None:
    async def completion(**kwargs):  # noqa: ANN003, ANN202
        raise AssertionError(kwargs)

    with pytest.raises(ValueError):
        await HostRerankingClient(completion).score(
            query=query,
            documents=documents,
            instruction=instruction,
        )


async def test_local_cross_encoder_passes_instruction_and_normalizes_logits() -> None:
    factories: list[dict[str, object]] = []
    predictions: list[dict[str, object]] = []

    class _Model:
        def predict(self, pairs, **kwargs):  # noqa: ANN001, ANN202
            predictions.append({"pairs": pairs, **kwargs})
            return [2.0, -2.0]

    def factory(model_name, **kwargs):  # noqa: ANN001, ANN202
        factories.append({"model_name": model_name, **kwargs})
        return _Model()

    client = LocalCrossEncoderRerankingClient(
        "local-reranker",
        device="cpu",
        batch_size=4,
        local_files_only=True,
        model_factory=factory,
    )
    scores = await client.score(
        query="query",
        documents=["first", "second"],
        instruction="instruction",
    )

    assert factories == [
        {
            "model_name": "local-reranker",
            "device": "cpu",
            "trust_remote_code": True,
            "local_files_only": True,
        }
    ]
    assert predictions == [
        {
            "pairs": [("query", "first"), ("query", "second")],
            "batch_size": 4,
            "show_progress_bar": False,
            "prompt": "instruction",
        }
    ]
    assert scores == pytest.approx([1.0 / (1.0 + math.exp(-2.0)), 1.0 / (1.0 + math.exp(2.0))])
