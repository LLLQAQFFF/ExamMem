"""ExamMem-owned learning-domain models and versioned taxonomy access."""

from .candidate_query import (
    CANDIDATE_LIFECYCLE_STATES,
    CandidateMatchReason,
    CandidateQuery,
    CandidateQueryPredicate,
    CandidateQueryPredicates,
    build_candidate_query,
    build_candidate_query_predicates,
)
from .normalization_policy import (
    NormalizationPolicy,
    NormalizationPolicyName,
    load_normalization_policy,
)
from .normalizer import (
    UNKNOWN_KNOWLEDGE_POINT_ID,
    KnowledgePointCandidate,
    KnowledgePointNormalizationResult,
    NormalizedKnowledgePoint,
    RuleBasedKnowledgePointNormalizer,
)
from .scope import (
    MVP_EXAM_ID,
    MVP_SUBJECT_ID,
    ScopeFieldName,
    ScopeQueryParameters,
    build_memory_scope,
    build_mvp_memory_scope,
    build_scope_query_parameters,
    scope_fingerprint,
)
from .slot_key import (
    SlotKey,
    build_error_pattern_slot_key,
    build_mastery_slot_key,
    build_plan_slot_key,
    build_preference_slot_key,
    build_profile_slot_key,
    validate_slot_key,
)
from .taxonomy import (
    KnowledgePointStatus,
    Taxonomy,
    TaxonomyNode,
    load_taxonomy,
)

__all__ = [
    "CANDIDATE_LIFECYCLE_STATES",
    "CandidateMatchReason",
    "CandidateQuery",
    "CandidateQueryPredicate",
    "CandidateQueryPredicates",
    "KnowledgePointCandidate",
    "KnowledgePointNormalizationResult",
    "KnowledgePointStatus",
    "MVP_EXAM_ID",
    "MVP_SUBJECT_ID",
    "NormalizedKnowledgePoint",
    "NormalizationPolicy",
    "NormalizationPolicyName",
    "RuleBasedKnowledgePointNormalizer",
    "ScopeFieldName",
    "ScopeQueryParameters",
    "SlotKey",
    "Taxonomy",
    "TaxonomyNode",
    "UNKNOWN_KNOWLEDGE_POINT_ID",
    "build_candidate_query",
    "build_candidate_query_predicates",
    "build_error_pattern_slot_key",
    "build_mastery_slot_key",
    "build_memory_scope",
    "build_mvp_memory_scope",
    "build_plan_slot_key",
    "build_preference_slot_key",
    "build_profile_slot_key",
    "build_scope_query_parameters",
    "load_taxonomy",
    "load_normalization_policy",
    "scope_fingerprint",
    "validate_slot_key",
]
