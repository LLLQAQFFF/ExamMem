from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from exam_mem.practice import Question
from exam_mem.storage import (
    GroundedLearningConflict,
    PostgresAssessmentRepository,
    PostgresGroundedLearningRepository,
    PostgresStudyPlanRepository,
    PostgresTextbookRepository,
    load_database_settings,
)
from exam_mem.storage.models import study_plan_textbook_bindings
from exam_mem.study import ImportedOutline, materialize_outline

pytestmark = [pytest.mark.asyncio, pytest.mark.database, pytest.mark.repository]


def _database_url_or_skip() -> str:
    if not os.environ.get("EXAM_MEM_DATABASE_URL"):
        pytest.skip("EXAM_MEM_DATABASE_URL is required")
    return load_database_settings().sqlalchemy_url()


def _tree(plan_id: str):
    return materialize_outline(
        plan_id,
        ImportedOutline.model_validate(
            {
                "name": "考试",
                "subjects": [
                    {
                        "name": "数学",
                        "modules": [
                            {
                                "name": "微积分",
                                "knowledge_points": [{"name": "极限", "type": "concept"}],
                            }
                        ],
                    }
                ],
            }
        ),
    )


async def _completed_textbook(
    repository: PostgresTextbookRepository, *, user_id: str, token: str, suffix: str
) -> tuple[dict, str]:
    version_id = f"version-{suffix}-{token}"
    job_id = f"job-{suffix}-{token}"
    version, _ = await repository.create_ingestion(
        user_id=user_id,
        textbook_id=f"book-{suffix}-{token}",
        version_id=version_id,
        job_id=job_id,
        idempotency_key=f"upload-{suffix}-{token}",
        title=f"教材{suffix}",
        metadata={},
        filename=f"book-{suffix}.md",
        mime_type="text/markdown",
        size_bytes=100,
        content_hash=("a" if suffix == "a" else "b") * 64,
        host_source_ref="source:" + ("a" if suffix == "a" else "b") * 64,
    )
    section_id = f"section-{suffix}-{token}"
    await repository.replace_sections(
        user_id=user_id,
        version_id=version_id,
        sections=(
            {
                "section_id": section_id,
                "section_key": f"section-{suffix}",
                "parent_section_id": None,
                "level": 1,
                "order": 0,
                "title": f"章节{suffix}",
                "path": [f"章节{suffix}"],
                "start_page": 3,
                "end_page": 8,
                "content_hash": "c" * 64,
                "confidence": 1.0,
                "inferred": False,
            },
        ),
    )
    await repository.advance_job(
        user_id=user_id,
        job_id=job_id,
        stage="completed",
        progress=100,
        checkpoint={"safe_stage": "completed"},
        host_index_ref="structured-" + ("d" if suffix == "a" else "e") * 32,
        index_version=f"index-{suffix}",
    )
    return version, section_id


async def test_versioned_bindings_mappings_snapshots_and_isolation() -> None:
    engine = create_async_engine(_database_url_or_skip())
    token = uuid.uuid4().hex
    user_id = f"user-{token}"
    plan_id = f"plan-{token}"
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                plans = PostgresStudyPlanRepository(connection)
                await plans.create_draft(
                    user_id=user_id,
                    plan_id=plan_id,
                    tree=_tree(plan_id),
                    source_kind="generated",
                    source_metadata={},
                )
                published = await plans.publish(user_id=user_id, plan_id=plan_id)
                subject_id = published["published"]["tree"]["subjects"][0]["id"]
                taxonomy_version = published["published"]["taxonomy_versions"][subject_id]
                objective_id = published["published"]["tree"]["subjects"][0]["modules"][0][
                    "knowledge_points"
                ][0]["id"]
                await plans.replace_draft(
                    user_id=user_id,
                    plan_id=plan_id,
                    tree=_tree(plan_id),
                    source_kind="generated",
                    source_metadata={},
                )
                await plans.publish(user_id=user_id, plan_id=plan_id)
                pinned_plan_version = await plans.get_version_for_taxonomy(
                    user_id=user_id,
                    plan_id=plan_id,
                    subject_id=subject_id,
                    taxonomy_version=taxonomy_version,
                )
                assert pinned_plan_version["version"] == 1
                textbooks = PostgresTextbookRepository(connection)
                first, first_section = await _completed_textbook(
                    textbooks, user_id=user_id, token=token, suffix="a"
                )
                second, second_section = await _completed_textbook(
                    textbooks, user_id=user_id, token=token, suffix="b"
                )
                grounded = PostgresGroundedLearningRepository(connection)
                assert (
                    await grounded.list_bindings(user_id=user_id, plan_id=plan_id, plan_version=1)
                    == []
                )
                candidate = await grounded.set_binding(
                    binding_id=f"binding-candidate-{token}",
                    user_id=user_id,
                    plan_id=plan_id,
                    plan_version=1,
                    textbook_version_id=first["version_id"],
                    role="primary",
                    priority=0,
                    status="candidate",
                )
                with pytest.raises(GroundedLearningConflict, match="confirmed textbook binding"):
                    await grounded.set_mapping(
                        mapping_id=f"mapping-invalid-{token}",
                        user_id=user_id,
                        plan_id=plan_id,
                        plan_version=1,
                        objective_id=objective_id,
                        textbook_section_id=first_section,
                        confidence=0.9,
                        created_via="recommended",
                        status="confirmed",
                    )
                confirmed = await grounded.set_binding(
                    binding_id=f"binding-confirmed-{token}",
                    user_id=user_id,
                    plan_id=plan_id,
                    plan_version=1,
                    textbook_version_id=first["version_id"],
                    role="primary",
                    priority=0,
                    status="confirmed",
                )
                assert candidate["revision"] == 1 and confirmed["revision"] == 2
                with pytest.raises(GroundedLearningConflict, match="only one confirmed primary"):
                    await grounded.set_binding(
                        binding_id=f"binding-conflict-{token}",
                        user_id=user_id,
                        plan_id=plan_id,
                        plan_version=1,
                        textbook_version_id=second["version_id"],
                        role="primary",
                        priority=0,
                        status="confirmed",
                    )
                await grounded.set_binding(
                    binding_id=f"binding-second-{token}",
                    user_id=user_id,
                    plan_id=plan_id,
                    plan_version=1,
                    textbook_version_id=second["version_id"],
                    role="supplement",
                    priority=1,
                    status="confirmed",
                )
                first_mapping = await grounded.set_mapping(
                    mapping_id=f"mapping-first-{token}",
                    user_id=user_id,
                    plan_id=plan_id,
                    plan_version=1,
                    objective_id=objective_id,
                    textbook_section_id=first_section,
                    confidence=1.0,
                    created_via="manual",
                    status="confirmed",
                )
                replay = await grounded.set_mapping(
                    mapping_id=f"mapping-first-{token}",
                    user_id=user_id,
                    plan_id=plan_id,
                    plan_version=1,
                    objective_id=objective_id,
                    textbook_section_id=first_section,
                    confidence=1.0,
                    created_via="manual",
                    status="confirmed",
                )
                assert replay == first_mapping
                await grounded.set_mapping(
                    mapping_id=f"mapping-second-{token}",
                    user_id=user_id,
                    plan_id=plan_id,
                    plan_version=1,
                    objective_id=objective_id,
                    textbook_section_id=second_section,
                    confidence=1.0,
                    created_via="manual",
                    status="confirmed",
                )
                scope = await grounded.grounding_scope(
                    user_id=user_id, plan_id=plan_id, plan_version=1, objective_id=objective_id
                )
                assert [(item["role"], item["priority"]) for item in scope] == [
                    ("primary", 0),
                    ("supplement", 1),
                ]
                sources = [
                    {
                        "index_ref": item["index_ref"],
                        "section_keys": [section["section_key"] for section in item["sections"]],
                        "evidence": [],
                    }
                    for item in scope
                ]
                snapshot, created = await grounded.create_learning_snapshot(
                    snapshot_id=f"snapshot-{token}",
                    user_id=user_id,
                    idempotency_key=f"open-{token}",
                    host_session_id=f"session-{token}",
                    plan_id=plan_id,
                    plan_version=1,
                    objective_id=objective_id,
                    mode="compare",
                    sources=sources,
                    index_versions={item["index_ref"]: item["index_version"] for item in scope},
                )
                duplicate, duplicate_created = await grounded.create_learning_snapshot(
                    snapshot_id="ignored",
                    user_id=user_id,
                    idempotency_key=f"open-{token}",
                    host_session_id=f"session-{token}",
                    plan_id=plan_id,
                    plan_version=1,
                    objective_id=objective_id,
                    mode="compare",
                    sources=sources,
                    index_versions={item["index_ref"]: item["index_version"] for item in scope},
                )
                assert created is True and duplicate_created is False and duplicate == snapshot
                with pytest.raises(GroundedLearningConflict, match="idempotency key conflicts"):
                    await grounded.create_learning_snapshot(
                        snapshot_id="ignored-conflict",
                        user_id=user_id,
                        idempotency_key=f"open-{token}",
                        host_session_id=f"session-{token}",
                        plan_id=plan_id,
                        plan_version=1,
                        objective_id=objective_id,
                        mode="primary",
                        sources=sources,
                        index_versions={item["index_ref"]: item["index_version"] for item in scope},
                    )
                assert (
                    await grounded.find_learning_snapshot(
                        user_id="other-user", host_session_id=f"session-{token}"
                    )
                    is None
                )
                assessments = PostgresAssessmentRepository(connection)
                assessment = await assessments.create_version(
                    assessment_id=f"assessment-{token}",
                    user_id=user_id,
                    exam_id=f"plan:{plan_id}",
                    subject_id=subject_id,
                    taxonomy_version=taxonomy_version,
                    title="固定教材检测",
                    knowledge_point_ids=[objective_id],
                    questions=(
                        Question(
                            question_id="q1",
                            stem="定义极限",
                            knowledge_point_ids=[objective_id],
                            difficulty=0.5,
                            reference_answer="定义",
                            grading_rubric={"required_steps": ["definition"]},
                        ),
                    ),
                    generation={"grounded": True},
                )
                assessment_snapshot = await grounded.create_assessment_snapshot(
                    snapshot_id=f"assessment-snapshot-{token}",
                    user_id=user_id,
                    idempotency_key=f"assessment-source-{token}",
                    assessment_id=f"assessment-{token}",
                    assessment_version=assessment["version"],
                    evidence=sources,
                    index_versions={item["index_ref"]: item["index_version"] for item in scope},
                )
                restored_assessment_snapshot = await grounded.find_assessment_snapshot(
                    user_id=user_id,
                    assessment_id=f"assessment-{token}",
                    assessment_version=1,
                )
                assert restored_assessment_snapshot == assessment_snapshot
                with pytest.raises(GroundedLearningConflict, match="idempotency key conflicts"):
                    await grounded.create_assessment_snapshot(
                        snapshot_id="ignored-assessment-conflict",
                        user_id=user_id,
                        idempotency_key=f"assessment-source-{token}",
                        assessment_id=f"assessment-{token}",
                        assessment_version=assessment["version"],
                        evidence=[],
                        index_versions={},
                    )
                await textbooks.archive(user_id=user_id, textbook_id=first["textbook_id"])
                scope_after_archive = await grounded.grounding_scope(
                    user_id=user_id,
                    plan_id=plan_id,
                    plan_version=1,
                    objective_id=objective_id,
                )
                assert [item["textbook_version_id"] for item in scope_after_archive] == [
                    second["version_id"]
                ]
                assert (
                    await grounded.find_learning_snapshot(
                        user_id=user_id, host_session_id=f"session-{token}"
                    )
                    == snapshot
                )
                with pytest.raises(GroundedLearningConflict, match="archived textbook"):
                    await grounded.set_binding(
                        binding_id=f"binding-archived-{token}",
                        user_id=user_id,
                        plan_id=plan_id,
                        plan_version=1,
                        textbook_version_id=first["version_id"],
                        role="reference",
                        priority=2,
                        status="confirmed",
                    )

                other_plan_id = f"other-plan-{token}"
                await plans.create_draft(
                    user_id=user_id,
                    plan_id=other_plan_id,
                    tree=_tree(other_plan_id),
                    source_kind="generated",
                    source_metadata={},
                )
                other_published = await plans.publish(user_id=user_id, plan_id=other_plan_id)
                other_objective_id = other_published["published"]["tree"]["subjects"][0]["modules"][
                    0
                ]["knowledge_points"][0]["id"]
                assert (
                    await grounded.grounding_scope(
                        user_id=user_id,
                        plan_id=other_plan_id,
                        plan_version=1,
                        objective_id=other_objective_id,
                    )
                    == []
                )
                with pytest.raises(DBAPIError):
                    async with connection.begin_nested():
                        await connection.execute(
                            update(study_plan_textbook_bindings)
                            .where(
                                study_plan_textbook_bindings.c.binding_id == confirmed["binding_id"]
                            )
                            .values(priority=99)
                        )
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
