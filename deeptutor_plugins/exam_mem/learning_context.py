"""Scoped Learning Memory grounding for ExamMem-bound study conversations."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Protocol

from deeptutor.plugins import SessionContextBlock
from deeptutor.plugins.host_services import current_user_id
from exam_mem.contracts import LearningContext
from exam_mem.study import StudyPlanTree


class ProductRuntimeProvider(Protocol):
    def open_product(self) -> Any: ...


class ExamMemLearningContextContributor:
    """Read live, objective-scoped memory only for explicitly bound sessions."""

    name = "exam_mem_learning"

    def __init__(self, runtime_provider: ProductRuntimeProvider) -> None:
        self._runtime_provider = runtime_provider

    async def resolve(self, *, session_id: str, language: str) -> SessionContextBlock | None:
        user_id = current_user_id()
        async with self._runtime_provider.open_product() as runtime:
            link = await runtime.study_plans.find_objective_session_by_host(
                user_id=user_id,
                host_session_id=session_id,
            )
            if link is None:
                return None
            plan = await runtime.study_plans.get(user_id=user_id, plan_id=link["plan_id"])
            if plan["archived_at"] is not None:
                return None
            version = await runtime.study_plans.get_version(
                user_id=user_id,
                plan_id=link["plan_id"],
                version=int(link["plan_version"]),
            )
            tree = StudyPlanTree.model_validate(version["tree"])
            resolved = tree.objective(link["objective_id"])
            if resolved is None:
                return None
            subject, module, objective = resolved
            taxonomy_version = str(version["taxonomy_versions"][subject.id])
            taxonomy = tree.taxonomy(subject.id, taxonomy_version)
            context = LearningContext(
                user_id=user_id,
                exam_id=f"plan:{link['plan_id']}",
                subject_id=subject.id,
            )
            profile = await runtime.learning_profiles.get(
                context=context,
                taxonomy=taxonomy,
                evaluated_at=datetime.now(timezone.utc),
            )
            observations = await runtime.observations.list(
                user_id=user_id,
                exam_id=context.exam_id,
                subject_id=context.subject_id,
                taxonomy_version=taxonomy_version,
                channel="learning_path",
                knowledge_point_ids=(objective.id,),
                status="confirmed",
            )

        point = next(
            item for item in profile.knowledge_points if item.knowledge_point_id == objective.id
        )
        review = next(
            item for item in profile.review_queue if item.knowledge_point_id == objective.id
        )
        return SessionContextBlock(
            name=self.name,
            content=_render_context(
                language=language,
                plan_name=tree.name,
                subject_name=subject.name,
                module_name=module.name,
                point=point,
                review=review,
                observations=observations[:3],
            ),
        )


def _render_context(
    *,
    language: str,
    plan_name: str,
    subject_name: str,
    module_name: str,
    point: Any,
    review: Any,
    observations: list[dict[str, Any]],
) -> str:
    zh = language.lower().startswith("zh")
    accuracy = "—" if point.accuracy is None else f"{point.accuracy * 100:.1f}%"
    mastery = "—" if point.mastery_score is None else f"{point.mastery_score * 100:.1f}%"
    memory_ids = ", ".join(point.source_memory_ids) or "none"
    note_lines = [f"- {item['summary']}" for item in observations]
    if zh:
        notes = "\n".join(note_lines) or "- 暂无已确认的学习路径记录。"
        return "\n".join(
            (
                "[ExamMem 学习上下文｜只读]",
                f"当前目标：{plan_name} / {subject_name} / {module_name} / {point.name}",
                "",
                "正式评测记忆（强证据，只能由独立作答与生命周期规则更新）：",
                f"- 状态：{point.status}；掌握度：{mastery}；正式作答：{point.attempts}；正确率：{accuracy}",
                f"- 稳定错因：{', '.join(point.error_types) or '无'}",
                f"- 复习状态：{review.status}；间隔：{review.interval_days} 天；原因：{', '.join(review.reason_codes)}",
                f"- 来源记忆：{memory_ids}",
                "",
                "已确认的学习路径记录（弱证据，仅帮助延续讲解，不等同于掌握）：",
                notes,
                "",
                "使用规则：结合正式记忆调整讲解深度并优先处理薄弱点；不得把聊天自述或学习记录当作已掌握证据。",
            )
        )
    notes = "\n".join(note_lines) or "- No confirmed learning-path notes yet."
    return "\n".join(
        (
            "[ExamMem learning context | read only]",
            f"Current objective: {plan_name} / {subject_name} / {module_name} / {point.name}",
            "",
            "Formal assessment memory (strong evidence; updated only by independent assessment and lifecycle rules):",
            f"- Status: {point.status}; mastery: {mastery}; formal attempts: {point.attempts}; accuracy: {accuracy}",
            f"- Stable errors: {', '.join(point.error_types) or 'none'}",
            f"- Review: {review.status}; interval: {review.interval_days} days; reasons: {', '.join(review.reason_codes)}",
            f"- Source memories: {memory_ids}",
            "",
            "Confirmed learning-path notes (weak evidence; useful for continuity, never proof of mastery):",
            notes,
            "",
            "Use rule: adapt depth from formal memory and address weaknesses first. Never treat chat claims or learning notes as mastery evidence.",
        )
    )


__all__ = ["ExamMemLearningContextContributor"]
