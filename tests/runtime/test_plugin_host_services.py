from __future__ import annotations

import pytest

from deeptutor.plugins import host_services


def test_plugin_host_services_expose_json_and_embedding_validation() -> None:
    assert host_services.extract_json_object('prefix {"ok": true} suffix') == {"ok": True}
    assert host_services.validate_embedding_batch([[1, 2.5]], expected_count=1) == [
        [1.0, 2.5]
    ]


@pytest.mark.asyncio
async def test_plugin_completion_delegates_to_the_configured_host(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_complete(**kwargs):
        captured.update(kwargs)
        return "result"

    monkeypatch.setattr("deeptutor.services.llm.complete", fake_complete)

    result = await host_services.complete(
        prompt="prompt",
        system_prompt="system",
        response_format={"type": "json_object"},
        temperature=0.0,
    )

    assert result == "result"
    assert captured == {
        "prompt": "prompt",
        "system_prompt": "system",
        "response_format": {"type": "json_object"},
        "temperature": 0.0,
    }
