from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from deeptutor.multi_user.context import reset_current_user, set_current_user
from deeptutor.multi_user.models import CurrentUser, UserScope
from deeptutor_plugins.exam_mem.api import StudyPlanImportBody, build_router
from deeptutor_plugins.exam_mem.learning_context import ExamMemLearningContextContributor
from deeptutor_plugins.exam_mem.sdk import ExamMemTextbookLearningSDK
from deeptutor_plugins.exam_mem.study_plan import ImportedStudyPlan
from exam_mem.practice.learning_observation import LearningObservationDraft
from exam_mem.practice.learning_profile import build_learning_profile
from exam_mem.storage import StudyPlanConflict
from exam_mem.study import ImportedOutline, StudyPlanTree, materialize_outline

pytestmark = pytest.mark.asyncio
NOW = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)


@contextmanager
def _regular_user():
    user = CurrentUser(
        id="study-user",
        username="study-user",
        role="user",
        scope=UserScope(kind="user", user_id="study-user", root=Path("/tmp/study-user")),  # noqa: S108
    )
    token = set_current_user(user)
    try:
        yield
    finally:
        reset_current_user(token)


def _tree(plan_id: str) -> StudyPlanTree:
    return materialize_outline(
        plan_id,
        ImportedOutline.model_validate(
            {
                "name": "2027 考研",
                "subjects": [
                    {
                        "name": "数学一",
                        "modules": [
                            {
                                "name": "高等数学",
                                "knowledge_points": [{"name": "函数极限", "type": "concept"}],
                            }
                        ],
                    }
                ],
            }
        ),
    )


class FakeImporter:
    async def generated(self, *, plan_id, plan_name, request):  # noqa: ANN001, ANN201
        assert plan_name == "2027 考研"
        assert request == "考研数学一完整大纲"
        return ImportedStudyPlan(
            tree=_tree(plan_id),
            source_kind="generated",
            source_metadata={"sha256": "0" * 64},
        )


class FakeStudyPlans:
    def __init__(self) -> None:
        self.plan_id = ""
        self.tree: StudyPlanTree | None = None
        self.published = False
        self.archived_at = None
        self.link = None

    async def create_draft(self, *, user_id, plan_id, tree, source_kind, source_metadata):  # noqa: ANN001, ANN201
        assert user_id == "study-user"
        self.plan_id = plan_id
        self.tree = tree
        return self._payload()

    async def list(self, *, user_id, archived=False):  # noqa: ANN001, ANN201, FBT002
        assert user_id == "study-user"
        matches = archived is None or bool(self.archived_at) is archived
        return [self._payload()] if matches else []

    async def archive(self, *, user_id, plan_id):  # noqa: ANN001, ANN201
        assert user_id == "study-user" and plan_id == self.plan_id
        self.archived_at = NOW.isoformat()
        return {"plan_id": plan_id, "archived_at": self.archived_at}

    async def restore(self, *, user_id, plan_id):  # noqa: ANN001, ANN201
        assert user_id == "study-user" and plan_id == self.plan_id
        self.archived_at = None
        return {"plan_id": plan_id, "archived_at": None}

    async def require_active(self, *, user_id, plan_id):  # noqa: ANN001, ANN201
        assert user_id == "study-user" and plan_id == self.plan_id
        if self.archived_at:
            raise StudyPlanConflict("archived study plan cannot be changed or opened")

    async def get(self, *, user_id, plan_id):  # noqa: ANN001, ANN201
        assert user_id == "study-user" and plan_id == self.plan_id
        return self._payload()

    async def publish(self, *, user_id, plan_id):  # noqa: ANN001, ANN201
        assert user_id == "study-user" and plan_id == self.plan_id
        self.published = True
        return self._payload()

    async def get_version(self, *, user_id, plan_id, version):  # noqa: ANN001, ANN201
        assert user_id == "study-user" and plan_id == self.plan_id
        assert version in (None, 1)
        return self._version()

    async def get_version_for_taxonomy(self, *, user_id, plan_id, subject_id, taxonomy_version):  # noqa: ANN001, ANN201
        assert user_id == "study-user" and plan_id == self.plan_id
        version = self._version()
        assert version["taxonomy_versions"][subject_id] == taxonomy_version
        return version

    async def taxonomy(self, *, user_id, exam_id, subject_id, taxonomy_version):  # noqa: ANN001, ANN201
        assert user_id == "study-user"
        assert exam_id == f"plan:{self.plan_id}"
        assert self.tree is not None
        return self.tree.taxonomy(subject_id, taxonomy_version)

    async def lock_objective_session(self, **_kwargs):  # noqa: ANN003
        return None

    async def find_objective_session(self, **_kwargs):  # noqa: ANN003, ANN201
        return self.link

    async def find_objective_session_by_host(self, *, user_id, host_session_id):  # noqa: ANN001, ANN201
        assert user_id == "study-user"
        if self.link is None or self.link["host_session_id"] != host_session_id:
            return None
        return self.link

    async def bind_objective_session(self, **kwargs):  # noqa: ANN003, ANN201
        self.link = {
            **kwargs,
            "created_at": NOW,
            "updated_at": NOW,
        }
        return self.link, True

    async def list_objective_sessions(self, **_kwargs):  # noqa: ANN003, ANN201
        return [] if self.link is None else [self.link]

    def _version(self):  # noqa: ANN202
        assert self.tree is not None
        subject = self.tree.subjects[0]
        return {
            "version": 1,
            "tree": self.tree.model_dump(mode="json"),
            "taxonomy_versions": {subject.id: "ptest_s001_v1"},
            "source_kind": "generated",
            "source_metadata": {"sha256": "0" * 64},
            "content_hash": "1" * 64,
            "published_at": NOW.isoformat(),
        }

    def _payload(self):  # noqa: ANN202
        assert self.tree is not None
        source = {
            "tree": self.tree.model_dump(mode="json"),
            "source_kind": "generated",
            "source_metadata": {"sha256": "0" * 64},
            "content_hash": "1" * 64,
        }
        return {
            "plan_id": self.plan_id,
            "name": self.tree.name,
            "active_version": 1 if self.published else None,
            "archived_at": self.archived_at,
            "created_at": NOW.isoformat(),
            "updated_at": NOW.isoformat(),
            "draft": None if self.published else {**source, "updated_at": NOW.isoformat()},
            "published": self._version() if self.published else None,
        }


class FakeConnection:
    commits = 0

    async def commit(self):
        self.commits += 1


class FakeRuntime:
    def __init__(self) -> None:
        self.study_plans = FakeStudyPlans()
        self.observations = FakeObservations()
        self.learning_profiles = FakeLearningProfiles()
        self.grounded_learning = FakeGroundedLearning()
        self.connection = FakeConnection()


class FakeProvider:
    def __init__(self) -> None:
        self.runtime = FakeRuntime()

    @asynccontextmanager
    async def open_product(self):
        yield self.runtime


class FakeGroundedLearning:
    def __init__(self) -> None:
        self.snapshot = None
        self.bindings = []
        self.mappings = []

    async def list_bindings(self, **_kwargs):  # noqa: ANN003, ANN201
        return self.bindings

    async def set_binding(self, **kwargs):  # noqa: ANN003, ANN201
        binding = {**kwargs, "revision": len(self.bindings) + 1}
        self.bindings.append(binding)
        return binding

    async def list_mappings(self, **_kwargs):  # noqa: ANN003, ANN201
        return self.mappings

    async def set_mapping(self, **kwargs):  # noqa: ANN003, ANN201
        mapping = {**kwargs, "mapping_version": len(self.mappings) + 1}
        self.mappings.append(mapping)
        return mapping

    async def grounding_scope(self, **_kwargs):  # noqa: ANN003, ANN201
        return []

    async def find_learning_snapshot(self, *, user_id, host_session_id):  # noqa: ANN001, ANN201
        assert user_id == "study-user"
        if self.snapshot and self.snapshot["host_session_id"] == host_session_id:
            return self.snapshot
        return None

    async def create_learning_snapshot(self, **kwargs):  # noqa: ANN003, ANN201
        self.snapshot = {
            "snapshot_id": kwargs["snapshot_id"],
            "host_session_id": kwargs["host_session_id"],
            "plan_id": kwargs["plan_id"],
            "plan_version": kwargs["plan_version"],
            "objective_id": kwargs["objective_id"],
            "mode": kwargs["mode"],
            "sources": kwargs["sources"],
            "index_versions": kwargs["index_versions"],
            "created_at": NOW.isoformat(),
        }
        return self.snapshot, True


class FakeGroundingService:
    async def evidence_package(self, **_kwargs):  # noqa: ANN003, ANN201
        return {
            "mode": "compare",
            "sources": [
                {
                    "textbook_title": "高等数学",
                    "textbook_version": 2,
                    "role": "primary",
                    "priority": 0,
                    "index_ref": "opaque-index",
                    "section_keys": ["limits"],
                    "evidence": [
                        {
                            "section_path": ["第一章", "极限"],
                            "section_key": "limits",
                            "start_page": 12,
                            "content": "函数极限的教材定义。",
                        }
                    ],
                }
            ],
            "knowledge_bases": ["opaque-index"],
            "filters": {"opaque-index": {"section_key": ("limits",)}},
            "index_versions": {"opaque-index": "index-v2"},
            "conflict_state": "none",
        }

    def validate_snapshot(self, snapshot):  # noqa: ANN001, ANN201
        assert snapshot["index_versions"] == {"opaque-index": "index-v2"}
        return ("opaque-index",), {"opaque-index": {"section_key": ("limits",)}}


class FakeLearningProfiles:
    async def get(self, *, context, taxonomy, evaluated_at):  # noqa: ANN001, ANN201
        return build_learning_profile(
            context=context,
            taxonomy=taxonomy,
            events=[],
            memories=[],
            model=None,
            evaluated_at=evaluated_at,
        )


class FakeTurnHost:
    def __init__(self) -> None:
        self.requests = []
        self.context_bindings = []
        self.knowledge_bindings = []

    async def start_turn(self, request):  # noqa: ANN001, ANN201
        self.requests.append(request)
        return {"id": "host-session"}, {"id": "host-turn"}

    async def session_exists(self, session_id):  # noqa: ANN001, ANN201
        return session_id == "host-session"

    async def bind_session_context_sources(self, session_id, source_names):  # noqa: ANN001, ANN201
        self.context_bindings.append((session_id, source_names))
        return True

    async def bind_session_knowledge_sources(self, session_id, source_names, *, filters=None):  # noqa: ANN001, ANN201
        self.knowledge_bindings.append((session_id, source_names, filters))
        return True

    async def delete_session(self, session_id):  # noqa: ANN001, ANN201
        raise AssertionError(f"unexpected cleanup: {session_id}")

    async def read_conversation(self, session_id):  # noqa: ANN001, ANN201
        assert session_id == "host-session"
        return SimpleNamespace(
            session_id=session_id,
            messages=(
                {"id": "user-1", "role": "user", "content": "请解释函数极限"},
                {"id": "assistant-1", "role": "assistant", "content": "先看定义。"},
            ),
        )


class FakeObservations:
    def __init__(self) -> None:
        self.observation = None

    async def append(self, **kwargs):  # noqa: ANN003, ANN201
        self.observation = {
            **kwargs,
            "status": "pending",
            "created_at": NOW.isoformat(),
        }
        return self.observation

    async def append_action(self, **kwargs):  # noqa: ANN003, ANN201
        assert self.observation is not None
        assert kwargs["observation_id"] == self.observation["observation_id"]
        self.observation = {**self.observation, "status": "confirmed"}
        return self.observation

    async def list(self, **kwargs):  # noqa: ANN003, ANN201
        assert kwargs["user_id"] == "study-user"
        assert kwargs["channel"] == "learning_path"
        assert kwargs["status"] == "confirmed"
        return [] if self.observation is None else [self.observation]


class FakeObservationAgent:
    async def analyze(self, **kwargs):  # noqa: ANN003, ANN201
        assert kwargs["channel"] == "learning_path"
        assert kwargs["language"] == "zh"
        assert kwargs["fixed_knowledge_point_id"]
        return LearningObservationDraft(
            related_to_study=True,
            knowledge_point_ids=[kwargs["fixed_knowledge_point_id"]],
            summary="复习了函数极限的定义。",
            rationale="对话包含定义讲解。",
            confidence=0.9,
        )


class FakeLearningHost:
    def __init__(self) -> None:
        self.paths = []

    def ensure_single_objective_path(self, *, path_id, objective):  # noqa: ANN001, ANN201
        self.paths.append((path_id, objective))

    def objective_progress(self, *, path_id, objective_id):  # noqa: ANN001, ANN201
        assert path_id and objective_id
        return {"status": "learning", "mastery": 0.25}


async def test_import_publish_and_open_objective_restores_one_host_session() -> None:
    provider = FakeProvider()
    turns = FakeTurnHost()
    learning = FakeLearningHost()
    api = FastAPI()
    api.include_router(
        build_router(
            provider,  # type: ignore[arg-type]
            turn_host=turns,  # type: ignore[arg-type]
            outline_importer=FakeImporter(),  # type: ignore[arg-type]
            learning_host=learning,  # type: ignore[arg-type]
            observation_agent=FakeObservationAgent(),  # type: ignore[arg-type]
        ),
        prefix="/api/v1/exam-mem",
    )

    with _regular_user():
        async with AsyncClient(transport=ASGITransport(app=api), base_url="http://test") as client:
            imported = await client.post(
                "/api/v1/exam-mem/study-plans/import",
                json={
                    "name": "2027 考研",
                    "source_kind": "generated",
                    "request": "考研数学一完整大纲",
                },
            )
            assert imported.status_code == 200
            plan_id = imported.json()["plan_id"]
            published = await client.post(f"/api/v1/exam-mem/study-plans/{plan_id}/publish")
            objective_id = published.json()["published"]["tree"]["subjects"][0]["modules"][0][
                "knowledge_points"
            ][0]["id"]
            binding = await client.post(
                f"/api/v1/exam-mem/study-plans/{plan_id}/versions/1/textbooks",
                json={
                    "textbook_version_id": "version-1",
                    "role": "primary",
                    "priority": 0,
                    "status": "confirmed",
                    "idempotency_key": "binding-1",
                },
            )
            mapping = await client.post(
                f"/api/v1/exam-mem/study-plans/{plan_id}/versions/1/textbook-mappings",
                json={
                    "objective_id": objective_id,
                    "textbook_section_id": "section-1",
                    "confidence": 1.0,
                    "created_via": "manual",
                    "status": "confirmed",
                    "idempotency_key": "mapping-1",
                },
            )
            first = await client.post(
                f"/api/v1/exam-mem/study-plans/{plan_id}/objectives/{objective_id}/open",
                json={"version": 1, "language": "zh"},
            )
            second = await client.post(
                f"/api/v1/exam-mem/study-plans/{plan_id}/objectives/{objective_id}/open",
                json={"version": 1, "language": "zh"},
            )
            snapshot = await client.get(
                f"/api/v1/exam-mem/learning-source-snapshots/{first.json()['session_id']}"
            )
            summarized = await client.post(
                f"/api/v1/exam-mem/study-plans/{plan_id}/objectives/{objective_id}/summarize",
                json={"version": 1, "language": "zh"},
            )
            subject = published.json()["published"]["tree"]["subjects"][0]
            taxonomy_version = published.json()["published"]["taxonomy_versions"][subject["id"]]
            profile = await client.get(
                "/api/v1/exam-mem/learning-profile",
                params={
                    "exam_id": f"plan:{plan_id}",
                    "subject_id": subject["id"],
                    "taxonomy_version": taxonomy_version,
                },
            )
            learning_context = await ExamMemLearningContextContributor(provider).resolve(
                session_id="host-session", language="zh"
            )
            archived = await client.post(f"/api/v1/exam-mem/study-plans/{plan_id}/archive")
            active_list = await client.get("/api/v1/exam-mem/study-plans")
            archived_list = await client.get("/api/v1/exam-mem/study-plans?archival=archived")
            blocked = await client.post(
                f"/api/v1/exam-mem/study-plans/{plan_id}/objectives/{objective_id}/open",
                json={"version": 1, "language": "zh"},
            )
            restored = await client.post(f"/api/v1/exam-mem/study-plans/{plan_id}/restore")
            sdk = ExamMemTextbookLearningSDK(client)
            sdk_binding = await sdk.bind_textbook(
                plan_id=plan_id,
                plan_version=1,
                textbook_version_id="version-2",
                role="supplement",
                priority=1,
                status="candidate",
                idempotency_key="sdk-binding-1",
            )
            sdk_bindings = await sdk.list_textbook_bindings(plan_id=plan_id, plan_version=1)
            sdk_session = await sdk.open_study_objective(
                plan_id=plan_id,
                objective_id=objective_id,
                plan_version=1,
                language="zh",
            )

    assert first.status_code == 200
    assert first.json()["created"] is True
    assert first.json()["source_status"] == "unbound"
    assert first.json()["chat_url"] == "/home/host-session"
    assert second.status_code == 200
    assert second.json()["created"] is False
    assert second.json()["session_id"] == first.json()["session_id"]
    assert len(turns.requests) == 1
    assert binding.status_code == 200
    assert mapping.status_code == 200
    assert snapshot.status_code == 200
    assert snapshot.json()["sources"] == []
    assert turns.requests[0].capability == "mastery_path"
    assert turns.requests[0].context_sources == ("exam_mem_learning",)
    assert turns.context_bindings == [
        ("host-session", ("exam_mem_learning",)),
        ("host-session", ("exam_mem_learning",)),
    ]
    assert "函数极限" in turns.requests[0].content
    assert len(learning.paths) == 1
    assert summarized.status_code == 200
    assert summarized.json()["observation"]["status"] == "confirmed"
    assert summarized.json()["observation"]["knowledge_point_ids"] == [objective_id]
    assert profile.status_code == 200
    assert profile.json()["context"] == {
        "exam_id": f"plan:{plan_id}",
        "subject_id": subject["id"],
    }
    assert profile.json()["summary"]["due_count"] == 0
    assert learning_context is not None
    assert "正式评测记忆（强证据" in learning_context.content
    assert "函数极限" in learning_context.content
    assert "复习了函数极限的定义" in learning_context.content
    assert "弱证据" in learning_context.content
    assert archived.status_code == 200
    assert active_list.json()["plans"] == []
    assert archived_list.json()["plans"][0]["plan_id"] == plan_id
    assert blocked.status_code == 409
    assert restored.json()["plan"]["archived_at"] is None
    assert sdk_binding["textbook_version_id"] == "version-2"
    assert len(sdk_bindings) == 2
    assert sdk_session["created"] is False
    assert sdk_session["session_id"] == first.json()["session_id"]


async def test_open_objective_pins_grounded_sources_and_rebinds_on_resume() -> None:
    provider = FakeProvider()
    turns = FakeTurnHost()
    api = FastAPI()
    api.include_router(
        build_router(
            provider,  # type: ignore[arg-type]
            turn_host=turns,  # type: ignore[arg-type]
            outline_importer=FakeImporter(),  # type: ignore[arg-type]
            learning_host=FakeLearningHost(),  # type: ignore[arg-type]
            observation_agent=FakeObservationAgent(),  # type: ignore[arg-type]
            grounded_learning_service=FakeGroundingService(),  # type: ignore[arg-type]
        ),
        prefix="/api/v1/exam-mem",
    )

    with _regular_user():
        async with AsyncClient(transport=ASGITransport(app=api), base_url="http://test") as client:
            imported = await client.post(
                "/api/v1/exam-mem/study-plans/import",
                json={
                    "name": "2027 考研",
                    "source_kind": "generated",
                    "request": "考研数学一完整大纲",
                },
            )
            plan_id = imported.json()["plan_id"]
            published = await client.post(f"/api/v1/exam-mem/study-plans/{plan_id}/publish")
            objective_id = published.json()["published"]["tree"]["subjects"][0]["modules"][0][
                "knowledge_points"
            ][0]["id"]
            first = await client.post(
                f"/api/v1/exam-mem/study-plans/{plan_id}/objectives/{objective_id}/open",
                json={"version": 1, "language": "zh", "source_mode": "compare"},
            )
            second = await client.post(
                f"/api/v1/exam-mem/study-plans/{plan_id}/objectives/{objective_id}/open",
                json={"version": 1, "language": "zh", "source_mode": "primary"},
            )

    assert first.status_code == 200
    assert first.json()["source_status"] == "grounded"
    assert first.json()["source_snapshot"]["mode"] == "compare"
    assert "高等数学 v2" in turns.requests[0].content
    assert turns.requests[0].knowledge_bases == ("opaque-index",)
    assert turns.requests[0].knowledge_source_filters == {
        "opaque-index": {"section_key": ("limits",)}
    }
    assert second.status_code == 200
    assert second.json()["source_snapshot"]["mode"] == "compare"
    assert turns.knowledge_bindings == [
        (
            "host-session",
            ("opaque-index",),
            {"opaque-index": {"section_key": ("limits",)}},
        )
    ]


async def test_study_plan_file_contract_rejects_unplanned_ingestion_formats() -> None:
    with pytest.raises(ValueError, match="PDF, TXT and Markdown"):
        StudyPlanImportBody(
            name="计算机统考",
            source_kind="file",
            filename="textbook.pptx",
            mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            base64="c2xpZGVz",
        )
