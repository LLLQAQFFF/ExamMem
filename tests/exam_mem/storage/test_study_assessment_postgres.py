from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from exam_mem.practice import Question
from exam_mem.storage import (
    AssessmentConflict,
    AssessmentNotFound,
    PostgresAssessmentRepository,
    PostgresStudyPlanRepository,
    load_database_settings,
)
from exam_mem.storage.models import assessment_versions, study_plan_versions
from exam_mem.study import ImportedOutline, materialize_outline

pytestmark = [pytest.mark.asyncio, pytest.mark.database, pytest.mark.repository]


def _database_url_or_skip() -> str:
    if not os.environ.get("EXAM_MEM_DATABASE_URL"):
        pytest.skip("EXAM_MEM_DATABASE_URL is required for PostgreSQL integration tests")
    return load_database_settings().sqlalchemy_url()


def _tree(plan_id: str, *, objective_name: str = "函数极限"):
    outline = ImportedOutline.model_validate(
        {
            "name": "2027 考研",
            "subjects": [
                {
                    "name": "数学一",
                    "modules": [
                        {
                            "name": "高等数学",
                            "knowledge_points": [
                                {"name": objective_name, "type": "concept"},
                                {"name": "洛必达法则", "type": "procedure"},
                            ],
                        }
                    ],
                }
            ],
        }
    )
    return materialize_outline(plan_id, outline)


def _questions(knowledge_point_id: str) -> tuple[Question, ...]:
    return tuple(
        Question(
            question_id=f"generated:q{index}",
            stem=f"Question {index}",
            knowledge_point_ids=[knowledge_point_id],
            difficulty=0.5,
            reference_answer=f"Answer {index}",
            grading_rubric={"required_steps": [f"step-{index}"]},
        )
        for index in (1, 2)
    )


async def test_study_plan_publish_taxonomy_and_session_link_are_transactional() -> None:
    engine = create_async_engine(_database_url_or_skip())
    plan_id = f"test-{uuid.uuid4().hex}"
    user_id = f"user-{uuid.uuid4().hex}"
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            repository = PostgresStudyPlanRepository(connection)
            try:
                draft = await repository.create_draft(
                    user_id=user_id,
                    plan_id=plan_id,
                    tree=_tree(plan_id),
                    source_kind="file",
                    source_metadata={"filename": "outline.txt"},
                )
                assert draft["active_version"] is None

                published = await repository.publish(user_id=user_id, plan_id=plan_id)
                version = published["published"]
                assert version["version"] == 1
                assert published["draft"] is None
                subject = _tree(plan_id).subjects[0]
                taxonomy_version = version["taxonomy_versions"][subject.id]
                taxonomy = await repository.taxonomy(
                    user_id=user_id,
                    exam_id=f"plan:{plan_id}",
                    subject_id=subject.id,
                    taxonomy_version=taxonomy_version,
                )
                objective = subject.modules[0].knowledge_points[0]
                assert taxonomy.get(objective.id).name_zh == objective.name

                await repository.lock_objective_session(
                    user_id=user_id,
                    plan_id=plan_id,
                    plan_version=1,
                    objective_id=objective.id,
                )
                link, inserted = await repository.bind_objective_session(
                    link_id=f"link-{uuid.uuid4().hex}",
                    user_id=user_id,
                    plan_id=plan_id,
                    plan_version=1,
                    objective_id=objective.id,
                    host_path_id="host-path",
                    host_session_id="host-session",
                    initial_turn_id="host-turn",
                )
                replay, replay_inserted = await repository.bind_objective_session(
                    link_id=f"link-{uuid.uuid4().hex}",
                    user_id=user_id,
                    plan_id=plan_id,
                    plan_version=1,
                    objective_id=objective.id,
                    host_path_id="ignored-path",
                    host_session_id="ignored-session",
                    initial_turn_id="ignored-turn",
                )
                assert inserted is True
                assert replay_inserted is False
                assert replay["link_id"] == link["link_id"]

                with pytest.raises(DBAPIError):
                    async with connection.begin_nested():
                        await connection.execute(
                            update(study_plan_versions)
                            .where(study_plan_versions.c.plan_id == plan_id)
                            .values(content_hash="0" * 64)
                        )
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


async def test_assessment_versions_and_repeated_attempts_keep_one_blueprint() -> None:
    engine = create_async_engine(_database_url_or_skip())
    user_id = f"user-{uuid.uuid4().hex}"
    assessment_id = f"assessment-{uuid.uuid4().hex}"
    knowledge_point_id = "ptest.s001.m001.k001"
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            repository = PostgresAssessmentRepository(connection)
            try:
                first = await repository.create_version(
                    assessment_id=assessment_id,
                    user_id=user_id,
                    exam_id="plan:test",
                    subject_id="ptest.s001",
                    taxonomy_version="ptest_s001_v1",
                    title="函数极限检测",
                    knowledge_point_ids=[knowledge_point_id],
                    questions=_questions(knowledge_point_id),
                    generation={"difficulty": "auto"},
                )
                with pytest.raises(AssessmentConflict, match="identity is unavailable"):
                    await repository.create_version(
                        assessment_id=assessment_id,
                        user_id="another-user",
                        exam_id="plan:test",
                        subject_id="ptest.s001",
                        taxonomy_version="ptest_s001_v1",
                        title="foreign identity",
                        knowledge_point_ids=[knowledge_point_id],
                        questions=_questions(knowledge_point_id),
                        generation={},
                    )
                second = await repository.create_version(
                    assessment_id=assessment_id,
                    user_id=user_id,
                    exam_id="plan:test",
                    subject_id="ptest.s001",
                    taxonomy_version="ptest_s001_v1",
                    title="函数极限检测（二）",
                    knowledge_point_ids=[knowledge_point_id],
                    questions=_questions(knowledge_point_id),
                    generation={"difficulty": "medium"},
                )
                assert (first["version"], second["version"]) == (1, 2)

                practice_session_ids = []
                for number, version in enumerate((1, 1, 2), start=1):
                    practice_session_id = f"practice-{number}-{uuid.uuid4().hex}"
                    practice_session_ids.append(practice_session_id)
                    await repository.start_attempt(
                        attempt_id=f"attempt-{number}-{uuid.uuid4().hex}",
                        user_id=user_id,
                        assessment_id=assessment_id,
                        version=version,
                        practice_session_id=practice_session_id,
                        trace_id=f"trace-{number}-{uuid.uuid4().hex}",
                    )
                listed = await repository.list(user_id=user_id)
                assert listed[0]["latest_version"] == 2
                assert listed[0]["archived_at"] is None
                assert len(listed[0]["attempts"]) == 3

                archived = await repository.archive(
                    user_id=user_id,
                    assessment_id=assessment_id,
                )
                replayed_archive = await repository.archive(
                    user_id=user_id,
                    assessment_id=assessment_id,
                )
                assert replayed_archive == archived
                assert await repository.list(user_id=user_id) == []
                [archived_item] = await repository.list(user_id=user_id, archived=True)
                assert archived_item["archived_at"] == archived["archived_at"]
                assert {attempt["status"] for attempt in archived_item["attempts"]} == {"failed"}
                with pytest.raises(AssessmentConflict, match="cannot receive new versions"):
                    await repository.create_version(
                        assessment_id=assessment_id,
                        user_id=user_id,
                        exam_id="plan:test",
                        subject_id="ptest.s001",
                        taxonomy_version="ptest_s001_v1",
                        title="archived",
                        knowledge_point_ids=[knowledge_point_id],
                        questions=_questions(knowledge_point_id),
                        generation={},
                    )
                with pytest.raises(AssessmentConflict, match="cannot be attempted"):
                    await repository.get_version(
                        user_id=user_id,
                        assessment_id=assessment_id,
                        version=1,
                    )
                with pytest.raises(AssessmentConflict, match="cannot be continued"):
                    await repository.require_practice_active(
                        user_id=user_id,
                        practice_session_id=practice_session_ids[0],
                    )
                with pytest.raises(AssessmentNotFound):
                    await repository.restore(
                        user_id="another-user",
                        assessment_id=assessment_id,
                    )

                restored = await repository.restore(
                    user_id=user_id,
                    assessment_id=assessment_id,
                )
                assert restored["archived_at"] is None
                [restored_item] = await repository.list(user_id=user_id)
                assert restored_item["archived_at"] is None
                await repository.require_practice_active(
                    user_id=user_id,
                    practice_session_id=practice_session_ids[0],
                )

                with pytest.raises(AssessmentConflict):
                    await repository.create_version(
                        assessment_id=assessment_id,
                        user_id=user_id,
                        exam_id="another-plan",
                        subject_id="ptest.s001",
                        taxonomy_version="ptest_s001_v1",
                        title="wrong scope",
                        knowledge_point_ids=[knowledge_point_id],
                        questions=_questions(knowledge_point_id),
                        generation={},
                    )

                with pytest.raises(DBAPIError):
                    async with connection.begin_nested():
                        await connection.execute(
                            update(assessment_versions)
                            .where(
                                assessment_versions.c.assessment_id == assessment_id,
                                assessment_versions.c.version == 1,
                            )
                            .values(content_hash="0" * 64)
                        )
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
