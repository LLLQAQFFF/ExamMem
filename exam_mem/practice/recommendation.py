"""Deterministic and explainable recommendation policy for Stage 07."""

from __future__ import annotations

from typing import Annotated, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from exam_mem.contracts import LearningContext, LearningMemory, LifecycleState
from exam_mem.domain import KnowledgePointStatus, Taxonomy, load_taxonomy

from .contracts import Question, Recommendation

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Probability = Annotated[float, Field(ge=0.0, le=1.0)]


class StrictRecommendationModel(BaseModel):
    """Reject silent drift at the deterministic recommendation boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class RecommendationPolicyV1Config(StrictRecommendationModel):
    """The exact engineering weights frozen by the Stage 07 specification."""

    policy_version: Literal["recommendation_policy_v1"] = "recommendation_policy_v1"
    weakness_weight: Literal[0.40] = 0.40
    stable_error_weight: Literal[0.25] = 0.25
    forgetting_risk_weight: Literal[0.15] = 0.15
    active_plan_priority_weight: Literal[0.10] = 0.10
    coverage_gap_weight: Literal[0.10] = 0.10
    tie_break_rule: Literal["priority_desc_syllabus_order_asc_knowledge_point_id_asc"] = (
        "priority_desc_syllabus_order_asc_knowledge_point_id_asc"
    )


class RecommendationFeatures(StrictRecommendationModel):
    """The five normalized policy features recorded for one knowledge point."""

    weakness: Probability
    stable_error: Probability
    forgetting_risk: Probability
    active_plan_priority: Probability
    coverage_gap: Probability


class RecommendationCandidate(StrictRecommendationModel):
    """Auditable policy input assembled from Student Model and current evidence."""

    target_knowledge_point_id: NonEmptyString
    target_difficulty: Probability
    features: RecommendationFeatures
    source_memories: tuple[LearningMemory, ...] = ()
    source_evidence_weight: Probability = 1.0
    deduplication_weight: Probability = 1.0

    @model_validator(mode="after")
    def validate_source_evidence(self) -> RecommendationCandidate:
        memory_ids = [memory.memory_id for memory in self.source_memories]
        if len(memory_ids) != len(set(memory_ids)):
            raise ValueError("recommendation source memory IDs must be unique")

        terminal_states = {
            memory.lifecycle_state
            for memory in self.source_memories
            if memory.lifecycle_state in {LifecycleState.ARCHIVED, LifecycleState.INVALIDATED}
        }
        if terminal_states:
            states = ", ".join(sorted(state.value for state in terminal_states))
            raise ValueError(f"terminal memory must not enter recommendation context: {states}")

        contains_contested = any(
            memory.lifecycle_state is LifecycleState.CONTESTED for memory in self.source_memories
        )
        if contains_contested and self.source_evidence_weight >= 1.0:
            raise ValueError("contested recommendation evidence must use a weight below 1")
        if not contains_contested and self.source_evidence_weight != 1.0:
            raise ValueError("active-only recommendation evidence must use weight 1")
        return self


class RecommendationScore(StrictRecommendationModel):
    """One complete scoring record suitable for a future Trace span."""

    candidate: RecommendationCandidate
    base_priority: Probability
    final_priority: Probability
    reason_codes: tuple[NonEmptyString, ...]
    syllabus_order: Annotated[int, Field(ge=0)]
    tie_break_rule: NonEmptyString


class RecommendationPolicyV1:
    """Rank normalized inputs without LLM choice or persistence side effects."""

    def __init__(
        self,
        taxonomy_version: str = "math1_v1",
        config: RecommendationPolicyV1Config | None = None,
        *,
        taxonomy: Taxonomy | None = None,
    ) -> None:
        self._taxonomy = taxonomy or load_taxonomy(taxonomy_version)
        self._config = config or RecommendationPolicyV1Config()
        self._syllabus_order = _active_leaf_order(self._taxonomy)

    @property
    def config(self) -> RecommendationPolicyV1Config:
        return self._config

    def rank(
        self,
        *,
        context: LearningContext,
        candidates: Sequence[RecommendationCandidate],
    ) -> tuple[RecommendationScore, ...]:
        candidate_ids = [candidate.target_knowledge_point_id for candidate in candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("recommendation candidate knowledge point IDs must be unique")

        scores = [self._score(context=context, candidate=candidate) for candidate in candidates]
        return tuple(
            sorted(
                scores,
                key=lambda score: (
                    -score.final_priority,
                    score.syllabus_order,
                    score.candidate.target_knowledge_point_id,
                ),
            )
        )

    def build_recommendation(
        self,
        score: RecommendationScore,
        question: Question,
    ) -> Recommendation:
        target_id = score.candidate.target_knowledge_point_id
        if target_id not in question.knowledge_point_ids:
            raise ValueError("recommended question must cover the target knowledge point")
        return Recommendation(
            question_id=question.question_id,
            target_knowledge_point_id=target_id,
            target_difficulty=score.candidate.target_difficulty,
            reason_codes=list(score.reason_codes),
            source_memory_ids=sorted(
                memory.memory_id for memory in score.candidate.source_memories
            ),
            policy_version=self._config.policy_version,
        )

    def build_fallback_recommendation(
        self,
        question: Question,
        *,
        target_knowledge_point_id: str,
    ) -> Recommendation:
        if target_knowledge_point_id not in question.knowledge_point_ids:
            raise ValueError("fallback question must cover the target knowledge point")
        if target_knowledge_point_id not in self._syllabus_order:
            raise ValueError("fallback target must be an active taxonomy leaf")
        return Recommendation(
            question_id=question.question_id,
            target_knowledge_point_id=target_knowledge_point_id,
            target_difficulty=question.difficulty,
            reason_codes=["syllabus_fallback"],
            source_memory_ids=[],
            policy_version=self._config.policy_version,
        )

    def _score(
        self,
        *,
        context: LearningContext,
        candidate: RecommendationCandidate,
    ) -> RecommendationScore:
        syllabus_order = self._syllabus_order.get(candidate.target_knowledge_point_id)
        if syllabus_order is None:
            raise ValueError("recommendation target must be an active taxonomy leaf")

        for memory in candidate.source_memories:
            memory_context = LearningContext(
                user_id=memory.scope.user_id,
                exam_id=memory.scope.exam_id,
                subject_id=memory.scope.subject_id,
            )
            if memory_context != context:
                raise ValueError("recommendation source memory is outside the requested context")

        features = candidate.features
        config = self._config
        base_priority = (
            config.weakness_weight * features.weakness
            + config.stable_error_weight * features.stable_error
            + config.forgetting_risk_weight * features.forgetting_risk
            + config.active_plan_priority_weight * features.active_plan_priority
            + config.coverage_gap_weight * features.coverage_gap
        )
        final_priority = (
            base_priority * candidate.source_evidence_weight * candidate.deduplication_weight
        )
        return RecommendationScore(
            candidate=candidate,
            base_priority=base_priority,
            final_priority=final_priority,
            reason_codes=_reason_codes(candidate),
            syllabus_order=syllabus_order,
            tie_break_rule=config.tie_break_rule,
        )


def _active_leaf_order(taxonomy: Taxonomy) -> dict[str, int]:
    active_leaves = [
        node.id
        for node in taxonomy.nodes
        if node.status is KnowledgePointStatus.ACTIVE and not taxonomy.children_of(node.id)
    ]
    return {knowledge_point_id: index for index, knowledge_point_id in enumerate(active_leaves)}


def _reason_codes(candidate: RecommendationCandidate) -> tuple[str, ...]:
    feature_reasons = (
        ("weakness", candidate.features.weakness),
        ("stable_error", candidate.features.stable_error),
        ("forgetting_risk", candidate.features.forgetting_risk),
        ("active_plan_priority", candidate.features.active_plan_priority),
        ("coverage_gap", candidate.features.coverage_gap),
    )
    reasons = [reason for reason, value in feature_reasons if value > 0]
    if candidate.source_evidence_weight < 1.0:
        reasons.append("contested_evidence_downweighted")
    if candidate.deduplication_weight < 1.0:
        reasons.append("recent_practice_downweighted")
    if not reasons:
        reasons.append("no_positive_signal")
    return tuple(reasons)


__all__ = [
    "RecommendationCandidate",
    "RecommendationFeatures",
    "RecommendationPolicyV1",
    "RecommendationPolicyV1Config",
    "RecommendationScore",
]
