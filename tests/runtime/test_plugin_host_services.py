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


@pytest.mark.asyncio
async def test_plugin_turn_host_delegates_to_the_public_facade(monkeypatch) -> None:
    requests: list[dict[str, object]] = []

    class FakeApp:
        async def start_turn(self, request):
            requests.append(request)
            return {"id": "session-1"}, {"id": "turn-1"}

        async def stream_turn(self, turn_id):
            assert turn_id == "turn-1"
            yield {"type": "done"}

    monkeypatch.setattr("deeptutor.app.DeepTutorApp", FakeApp)
    host = host_services.PluginTurnHost()
    session, turn = await host.start_turn(
        host_services.PluginTurnRequest(
            content="practice",
            capability="domain_capability",
            config={"domain_context": {"id": "one"}},
        )
    )
    events = [event async for event in host.stream_turn(turn["id"])]

    assert session == {"id": "session-1"}
    assert requests == [
        {
            "content": "practice",
            "capability": "domain_capability",
            "session_id": None,
            "language": "en",
            "config": {"domain_context": {"id": "one"}},
        }
    ]
    assert events == [{"type": "done"}]


@pytest.mark.asyncio
async def test_plugin_turn_host_forwards_explicit_attachments(monkeypatch) -> None:
    requests: list[dict[str, object]] = []

    class FakeApp:
        async def start_turn(self, request):
            requests.append(request)
            return {"id": "session-1"}, {"id": "turn-1"}

    monkeypatch.setattr("deeptutor.app.DeepTutorApp", FakeApp)
    host = host_services.PluginTurnHost()
    await host.start_turn(
        host_services.PluginTurnRequest(
            content="generate",
            capability="neutral_capability",
            attachments=(
                {
                    "type": "file",
                    "filename": "lesson.txt",
                    "mime_type": "text/plain",
                    "base64": "bGVzc29u",
                },
            ),
        )
    )

    assert requests[0]["attachments"] == [
        {
            "type": "file",
            "filename": "lesson.txt",
            "mime_type": "text/plain",
            "base64": "bGVzc29u",
        }
    ]


@pytest.mark.asyncio
async def test_plugin_turn_host_deletes_transient_session_and_attachments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deleted: list[tuple[str, str]] = []

    class FakeApp:
        pass

    class SessionStore:
        async def delete_session(self, session_id):
            deleted.append(("session", session_id))
            return True

    class AttachmentStore:
        async def delete_session(self, session_id):
            deleted.append(("attachments", session_id))

    monkeypatch.setattr("deeptutor.app.DeepTutorApp", FakeApp)
    monkeypatch.setattr(
        "deeptutor.services.session.get_session_store", lambda: SessionStore()
    )
    monkeypatch.setattr(
        "deeptutor.services.storage.attachment_store.get_attachment_store",
        lambda: AttachmentStore(),
    )

    assert await host_services.PluginTurnHost().delete_session("transient-1") is True
    assert deleted == [
        ("session", "transient-1"),
        ("attachments", "transient-1"),
    ]
