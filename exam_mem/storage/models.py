"""SQLAlchemy Core schema for the three Learning Memory layers.

The tables in this module are ExamMem-owned PostgreSQL structures.  They do
not replace or share persistence with DeepTutor's file-based Native Memory.
"""

from __future__ import annotations

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

LEARNING_MEMORY_EMBEDDING_DIMENSION = 1024

metadata = MetaData()

study_plans = Table(
    "study_plans",
    metadata,
    Column("plan_id", Text, nullable=False),
    Column("user_id", Text, nullable=False),
    Column("name", Text, nullable=False),
    Column("active_version", Integer, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("plan_id", name="pk_study_plans"),
    UniqueConstraint("user_id", "plan_id", name="uq_study_plans_user_plan"),
    CheckConstraint("btrim(name) <> ''", name="ck_study_plans_name_nonempty"),
    CheckConstraint(
        "active_version IS NULL OR active_version >= 1",
        name="ck_study_plans_active_version",
    ),
)

Index(
    "ix_study_plans_user_updated",
    study_plans.c.user_id,
    study_plans.c.updated_at,
    study_plans.c.plan_id,
)

study_plan_drafts = Table(
    "study_plan_drafts",
    metadata,
    Column("plan_id", Text, ForeignKey("study_plans.plan_id"), nullable=False),
    Column("tree", JSONB, nullable=False),
    Column("source_kind", Text, nullable=False),
    Column("source_metadata", JSONB, nullable=False),
    Column("content_hash", Text, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("plan_id", name="pk_study_plan_drafts"),
    CheckConstraint(
        "source_kind IN ('file', 'url', 'generated')",
        name="ck_study_plan_drafts_source_kind",
    ),
    CheckConstraint("jsonb_typeof(tree) = 'object'", name="ck_study_plan_drafts_tree_object"),
    CheckConstraint(
        "jsonb_typeof(source_metadata) = 'object'",
        name="ck_study_plan_drafts_source_metadata_object",
    ),
    CheckConstraint("length(content_hash) = 64", name="ck_study_plan_drafts_hash"),
)

study_plan_versions = Table(
    "study_plan_versions",
    metadata,
    Column("plan_id", Text, ForeignKey("study_plans.plan_id"), nullable=False),
    Column("version", Integer, nullable=False),
    Column("tree", JSONB, nullable=False),
    Column("taxonomy_versions", JSONB, nullable=False),
    Column("source_kind", Text, nullable=False),
    Column("source_metadata", JSONB, nullable=False),
    Column("content_hash", Text, nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("plan_id", "version", name="pk_study_plan_versions"),
    CheckConstraint("version >= 1", name="ck_study_plan_versions_version"),
    CheckConstraint(
        "source_kind IN ('file', 'url', 'generated')",
        name="ck_study_plan_versions_source_kind",
    ),
    CheckConstraint("jsonb_typeof(tree) = 'object'", name="ck_study_plan_versions_tree_object"),
    CheckConstraint(
        "jsonb_typeof(taxonomy_versions) = 'object'",
        name="ck_study_plan_versions_taxonomies_object",
    ),
    CheckConstraint(
        "jsonb_typeof(source_metadata) = 'object'",
        name="ck_study_plan_versions_source_metadata_object",
    ),
    CheckConstraint("length(content_hash) = 64", name="ck_study_plan_versions_hash"),
)

study_objective_sessions = Table(
    "study_objective_sessions",
    metadata,
    Column("link_id", Text, nullable=False),
    Column("user_id", Text, nullable=False),
    Column("plan_id", Text, nullable=False),
    Column("plan_version", Integer, nullable=False),
    Column("objective_id", Text, nullable=False),
    Column("host_path_id", Text, nullable=False),
    Column("host_session_id", Text, nullable=False),
    Column("initial_turn_id", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("link_id", name="pk_study_objective_sessions"),
    ForeignKeyConstraint(
        ["plan_id", "plan_version"],
        ["study_plan_versions.plan_id", "study_plan_versions.version"],
        name="fk_study_objective_sessions_plan_version",
    ),
    UniqueConstraint(
        "user_id",
        "plan_id",
        "plan_version",
        "objective_id",
        name="uq_study_objective_sessions_objective",
    ),
    UniqueConstraint(
        "user_id",
        "host_session_id",
        name="uq_study_objective_sessions_host_session",
    ),
    CheckConstraint("btrim(objective_id) <> ''", name="ck_study_objective_sessions_objective"),
    CheckConstraint("btrim(host_path_id) <> ''", name="ck_study_objective_sessions_path"),
    CheckConstraint("btrim(host_session_id) <> ''", name="ck_study_objective_sessions_session"),
    CheckConstraint("btrim(initial_turn_id) <> ''", name="ck_study_objective_sessions_turn"),
)

Index(
    "ix_study_objective_sessions_user_updated",
    study_objective_sessions.c.user_id,
    study_objective_sessions.c.updated_at,
    study_objective_sessions.c.link_id,
)

assessments = Table(
    "assessments",
    metadata,
    Column("assessment_id", Text, nullable=False),
    Column("user_id", Text, nullable=False),
    Column("exam_id", Text, nullable=False),
    Column("subject_id", Text, nullable=False),
    Column("taxonomy_version", Text, nullable=False),
    Column("title", Text, nullable=False),
    Column("knowledge_point_ids", JSONB, nullable=False),
    Column("latest_version", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("assessment_id", name="pk_assessments"),
    CheckConstraint("btrim(title) <> ''", name="ck_assessments_title_nonempty"),
    CheckConstraint(
        "jsonb_typeof(knowledge_point_ids) = 'array' "
        "AND jsonb_array_length(knowledge_point_ids) > 0",
        name="ck_assessments_knowledge_points",
    ),
    CheckConstraint("latest_version >= 1", name="ck_assessments_latest_version"),
)

Index(
    "ix_assessments_scope_updated",
    assessments.c.user_id,
    assessments.c.exam_id,
    assessments.c.subject_id,
    assessments.c.updated_at,
    assessments.c.assessment_id,
)

assessment_versions = Table(
    "assessment_versions",
    metadata,
    Column("assessment_id", Text, ForeignKey("assessments.assessment_id"), nullable=False),
    Column("version", Integer, nullable=False),
    Column("question_catalog", JSONB, nullable=False),
    Column("generation", JSONB, nullable=False),
    Column("content_hash", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("assessment_id", "version", name="pk_assessment_versions"),
    CheckConstraint("version >= 1", name="ck_assessment_versions_version"),
    CheckConstraint(
        "jsonb_typeof(question_catalog) = 'array' AND jsonb_array_length(question_catalog) > 0",
        name="ck_assessment_versions_catalog",
    ),
    CheckConstraint(
        "jsonb_typeof(generation) = 'object'",
        name="ck_assessment_versions_generation",
    ),
    CheckConstraint("length(content_hash) = 64", name="ck_assessment_versions_hash"),
)

assessment_attempts = Table(
    "assessment_attempts",
    metadata,
    Column("attempt_id", Text, nullable=False),
    Column("user_id", Text, nullable=False),
    Column("assessment_id", Text, nullable=False),
    Column("assessment_version", Integer, nullable=False),
    Column("practice_session_id", Text, nullable=False),
    Column("trace_id", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    PrimaryKeyConstraint("attempt_id", name="pk_assessment_attempts"),
    ForeignKeyConstraint(
        ["assessment_id", "assessment_version"],
        ["assessment_versions.assessment_id", "assessment_versions.version"],
        name="fk_assessment_attempts_version",
    ),
    UniqueConstraint(
        "user_id",
        "practice_session_id",
        name="uq_assessment_attempts_practice_session",
    ),
    UniqueConstraint("user_id", "trace_id", name="uq_assessment_attempts_trace"),
    CheckConstraint(
        "status IN ('in_progress', 'completed', 'failed')",
        name="ck_assessment_attempts_status",
    ),
    CheckConstraint(
        "(status = 'completed' AND completed_at IS NOT NULL) "
        "OR (status <> 'completed' AND completed_at IS NULL)",
        name="ck_assessment_attempts_completion",
    ),
)

Index(
    "ix_assessment_attempts_assessment_started",
    assessment_attempts.c.user_id,
    assessment_attempts.c.assessment_id,
    assessment_attempts.c.started_at,
    assessment_attempts.c.attempt_id,
)

learning_observations = Table(
    "learning_observations",
    metadata,
    Column("observation_id", Text, nullable=False),
    Column("user_id", Text, nullable=False),
    Column("exam_id", Text, nullable=False),
    Column("subject_id", Text, nullable=False),
    Column("taxonomy_version", Text, nullable=False),
    Column("channel", Text, nullable=False),
    Column("source_session_id", Text, nullable=False),
    Column("source_turn_ids", JSONB, nullable=False),
    Column("knowledge_point_ids", JSONB, nullable=False),
    Column("summary", Text, nullable=False),
    Column("rationale", Text, nullable=False),
    Column("confidence", Float, nullable=False),
    Column("agent_contract_version", Text, nullable=False),
    Column("source_fingerprint", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("observation_id", name="pk_learning_observations"),
    UniqueConstraint(
        "user_id",
        "source_fingerprint",
        name="uq_learning_observations_user_source",
    ),
    CheckConstraint(
        "channel IN ('chat', 'learning_path')",
        name="ck_learning_observations_channel",
    ),
    CheckConstraint(
        "jsonb_typeof(source_turn_ids) = 'array'",
        name="ck_learning_observations_turns_array",
    ),
    CheckConstraint(
        "jsonb_typeof(knowledge_point_ids) = 'array' "
        "AND jsonb_array_length(knowledge_point_ids) > 0",
        name="ck_learning_observations_knowledge_points",
    ),
    CheckConstraint(
        "confidence >= 0.0 AND confidence <= 1.0",
        name="ck_learning_observations_confidence",
    ),
    CheckConstraint(
        "length(source_fingerprint) = 64",
        name="ck_learning_observations_fingerprint",
    ),
)

Index(
    "ix_learning_observations_scope_created",
    learning_observations.c.user_id,
    learning_observations.c.exam_id,
    learning_observations.c.subject_id,
    learning_observations.c.taxonomy_version,
    learning_observations.c.created_at,
    learning_observations.c.observation_id,
)

learning_observation_actions = Table(
    "learning_observation_actions",
    metadata,
    Column("action_id", Text, nullable=False),
    Column(
        "observation_id",
        Text,
        ForeignKey("learning_observations.observation_id"),
        nullable=False,
    ),
    Column("user_id", Text, nullable=False),
    Column("action", Text, nullable=False),
    Column("idempotency_key", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("action_id", name="pk_learning_observation_actions"),
    UniqueConstraint(
        "user_id",
        "idempotency_key",
        name="uq_learning_observation_actions_user_idempotency",
    ),
    CheckConstraint(
        "action IN ('confirm', 'dismiss')",
        name="ck_learning_observation_actions_action",
    ),
)

Index(
    "ix_learning_observation_actions_observation_created",
    learning_observation_actions.c.observation_id,
    learning_observation_actions.c.created_at,
    learning_observation_actions.c.action_id,
)

learning_events = Table(
    "learning_events",
    metadata,
    Column("event_id", Text, primary_key=True),
    Column("idempotency_key", Text, nullable=False),
    Column("user_id", Text, nullable=False),
    Column("exam_id", Text, nullable=False),
    Column("subject_id", Text, nullable=False),
    Column("event_type", Text, nullable=False),
    Column("session_id", Text, nullable=True),
    Column("question_id", Text, nullable=True),
    Column("knowledge_point_ids", JSONB, nullable=False),
    Column("primary_knowledge_point_id", Text, nullable=True),
    Column("difficulty", Float, nullable=True),
    Column("answer_correct", Boolean, nullable=True),
    Column("error_type", Text, nullable=True),
    Column("error_detail", Text, nullable=True),
    Column("evidence_quality", JSONB, nullable=False),
    Column("correction_source", Text, nullable=True),
    Column("correction_statement", Text, nullable=True),
    Column("plan_transition_status", Text, nullable=True),
    Column("plan_transition_source", Text, nullable=True),
    Column("plan_transition_reason", Text, nullable=True),
    Column("raw_payload", JSONB, nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("trace_id", Text, nullable=False),
    Column("schema_version", Integer, nullable=False),
    UniqueConstraint(
        "user_id",
        "idempotency_key",
        name="uq_learning_events_user_idempotency",
    ),
    CheckConstraint(
        "event_type IN ('answer_attempt', 'explicit_correction', 'plan_transition')",
        name="ck_learning_events_event_type",
    ),
    CheckConstraint(
        "jsonb_typeof(knowledge_point_ids) = 'array' "
        "AND jsonb_array_length(knowledge_point_ids) > 0",
        name="ck_learning_events_knowledge_points_nonempty",
    ),
    CheckConstraint(
        "difficulty IS NULL OR (difficulty >= 0.0 AND difficulty <= 1.0)",
        name="ck_learning_events_difficulty_range",
    ),
    CheckConstraint(
        "(event_type = 'answer_attempt' "
        "AND question_id IS NOT NULL "
        "AND difficulty IS NOT NULL "
        "AND answer_correct IS NOT NULL "
        "AND correction_source IS NULL "
        "AND correction_statement IS NULL "
        "AND plan_transition_status IS NULL "
        "AND plan_transition_source IS NULL "
        "AND plan_transition_reason IS NULL) "
        "OR (event_type = 'explicit_correction' "
        "AND correction_source IS NOT NULL "
        "AND correction_statement IS NOT NULL "
        "AND question_id IS NULL "
        "AND difficulty IS NULL "
        "AND answer_correct IS NULL "
        "AND error_type IS NULL "
        "AND error_detail IS NULL "
        "AND plan_transition_status IS NULL "
        "AND plan_transition_source IS NULL "
        "AND plan_transition_reason IS NULL) "
        "OR (event_type = 'plan_transition' "
        "AND plan_transition_status IS NOT NULL "
        "AND plan_transition_source IS NOT NULL "
        "AND plan_transition_reason IS NOT NULL "
        "AND question_id IS NULL "
        "AND difficulty IS NULL "
        "AND answer_correct IS NULL "
        "AND error_type IS NULL "
        "AND error_detail IS NULL "
        "AND correction_source IS NULL "
        "AND correction_statement IS NULL)",
        name="ck_learning_events_payload_shape",
    ),
    CheckConstraint(
        "correction_source IS NULL OR correction_source IN ('user', 'teacher', 'grader_audit')",
        name="ck_learning_events_correction_source",
    ),
    CheckConstraint(
        "plan_transition_source IS NULL "
        "OR plan_transition_source IN ('user', 'system', 'practice_progress')",
        name="ck_learning_events_plan_transition_source",
    ),
    CheckConstraint(
        "plan_transition_status IS NULL "
        "OR plan_transition_status IN "
        "('planned', 'in_progress', 'completed', 'cancelled', 'expired')",
        name="ck_learning_events_plan_transition_status",
    ),
    CheckConstraint("schema_version >= 1", name="ck_learning_events_schema_version"),
)

baseline_memory_facts = Table(
    "baseline_memory_facts",
    metadata,
    Column("backend_mode", Text, nullable=False),
    Column(
        "event_id",
        Text,
        ForeignKey("learning_events.event_id"),
        nullable=False,
    ),
    Column("user_id", Text, nullable=False),
    Column("exam_id", Text, nullable=False),
    Column("subject_id", Text, nullable=False),
    Column("memory_namespace", Text, nullable=False),
    Column("slot_key", Text, nullable=False),
    Column("value", JSONB, nullable=False),
    Column("evidence", JSONB, nullable=False),
    Column(
        "content_embedding",
        Vector(LEARNING_MEMORY_EMBEDDING_DIMENSION),
        nullable=True,
    ),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    PrimaryKeyConstraint(
        "backend_mode",
        "event_id",
        "slot_key",
        name="pk_baseline_memory_facts",
    ),
    CheckConstraint(
        "backend_mode IN ('append_only', 'vector')",
        name="ck_baseline_memory_facts_backend_mode",
    ),
    CheckConstraint(
        "memory_namespace IN ('mastery', 'error_pattern', 'plan', 'profile', 'preference')",
        name="ck_baseline_memory_facts_namespace",
    ),
    CheckConstraint(
        "value ->> 'type' = memory_namespace",
        name="ck_baseline_memory_facts_value_namespace",
    ),
    CheckConstraint(
        "jsonb_typeof(evidence) = 'object'",
        name="ck_baseline_memory_facts_evidence_object",
    ),
    CheckConstraint(
        "(backend_mode = 'append_only' AND content_embedding IS NULL) "
        "OR (backend_mode = 'vector' AND content_embedding IS NOT NULL)",
        name="ck_baseline_memory_facts_embedding_mode",
    ),
)

Index(
    "ix_baseline_memory_facts_mode_scope_slot_created",
    baseline_memory_facts.c.backend_mode,
    baseline_memory_facts.c.user_id,
    baseline_memory_facts.c.exam_id,
    baseline_memory_facts.c.subject_id,
    baseline_memory_facts.c.memory_namespace,
    baseline_memory_facts.c.slot_key,
    baseline_memory_facts.c.created_at,
    baseline_memory_facts.c.event_id,
)
Index(
    "ix_baseline_memory_facts_content_embedding_hnsw",
    baseline_memory_facts.c.content_embedding,
    postgresql_using="hnsw",
    postgresql_ops={"content_embedding": "vector_cosine_ops"},
    postgresql_where=(baseline_memory_facts.c.backend_mode == "vector")
    & baseline_memory_facts.c.content_embedding.is_not(None),
)

practice_workflow_checkpoints = Table(
    "practice_workflow_checkpoints",
    metadata,
    Column("practice_session_id", Text, nullable=False),
    Column("checkpoint_key", Text, nullable=False),
    Column("user_id", Text, nullable=False),
    Column("exam_id", Text, nullable=False),
    Column("subject_id", Text, nullable=False),
    Column("trace_id", Text, nullable=False),
    Column("step_state", Text, nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("row_version", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    PrimaryKeyConstraint(
        "practice_session_id",
        "checkpoint_key",
        name="pk_practice_workflow_checkpoints",
    ),
    CheckConstraint(
        "step_state IN "
        "('IDLE', 'QUESTION_READY', 'ANSWER_RECEIVED', 'GRADED', "
        "'DIAGNOSED', 'MEMORY_UPDATED', 'RECOMMENDED')",
        name="ck_practice_workflow_checkpoints_step_state",
    ),
    CheckConstraint(
        "jsonb_typeof(payload) = 'object'",
        name="ck_practice_workflow_checkpoints_payload_object",
    ),
    CheckConstraint(
        "payload ->> 'checkpoint_key' = checkpoint_key",
        name="ck_practice_workflow_checkpoints_payload_key",
    ),
    CheckConstraint(
        "payload #>> '{context,practice_session_id}' = practice_session_id",
        name="ck_practice_workflow_checkpoints_payload_session",
    ),
    CheckConstraint(
        "payload #>> '{context,trace_id}' = trace_id",
        name="ck_practice_workflow_checkpoints_payload_trace",
    ),
    CheckConstraint(
        "payload #>> '{context,step_state}' = step_state",
        name="ck_practice_workflow_checkpoints_payload_state",
    ),
    CheckConstraint("row_version >= 1", name="ck_practice_workflow_checkpoints_row_version"),
)

Index(
    "ix_practice_workflow_checkpoints_scope_updated",
    practice_workflow_checkpoints.c.user_id,
    practice_workflow_checkpoints.c.exam_id,
    practice_workflow_checkpoints.c.subject_id,
    practice_workflow_checkpoints.c.updated_at,
    practice_workflow_checkpoints.c.practice_session_id,
)
Index(
    "ix_practice_workflow_checkpoints_trace",
    practice_workflow_checkpoints.c.trace_id,
)

practice_trace_spans = Table(
    "practice_trace_spans",
    metadata,
    Column("trace_id", Text, nullable=False),
    Column("step_id", Integer, nullable=False),
    Column("span_name", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("input_summary", JSONB, nullable=False),
    Column("output_summary", JSONB, nullable=False),
    Column("versions", JSONB, nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=False),
    Column("duration_ms", Float, nullable=False),
    Column("retry_count", Integer, nullable=False),
    Column("llm_calls", Integer, nullable=False),
    Column("input_tokens", Integer, nullable=False),
    Column("output_tokens", Integer, nullable=False),
    Column("error_code", Text, nullable=True),
    Column("related_record_ids", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("trace_id", "step_id", name="pk_practice_trace_spans"),
    CheckConstraint("step_id >= 1", name="ck_practice_trace_spans_step_id"),
    CheckConstraint(
        "span_name IN "
        "('request_received', 'question_selected', 'answer_graded', "
        "'knowledge_mapped', 'error_diagnosed', 'event_appended', "
        "'lifecycle_decided', 'lifecycle_applied', 'student_model_projected', "
        "'question_recommended', 'plan_transition_appended', "
        "'plan_transition_applied', 'correction_target_resolved', "
        "'correction_event_appended', 'correction_lifecycle_applied', "
        "'recommendation_refreshed', 'response_sent')",
        name="ck_practice_trace_spans_name",
    ),
    CheckConstraint(
        "status IN ('completed', 'failed')",
        name="ck_practice_trace_spans_status",
    ),
    CheckConstraint(
        "jsonb_typeof(input_summary) = 'object' "
        "AND jsonb_typeof(output_summary) = 'object' "
        "AND jsonb_typeof(versions) = 'object'",
        name="ck_practice_trace_spans_summary_objects",
    ),
    CheckConstraint(
        "jsonb_typeof(related_record_ids) = 'array'",
        name="ck_practice_trace_spans_related_ids_array",
    ),
    CheckConstraint(
        "completed_at >= started_at AND duration_ms >= 0.0",
        name="ck_practice_trace_spans_duration",
    ),
    CheckConstraint(
        "retry_count >= 0 AND llm_calls >= 0 AND input_tokens >= 0 AND output_tokens >= 0",
        name="ck_practice_trace_spans_counts",
    ),
    CheckConstraint(
        "(status = 'failed' AND error_code IS NOT NULL) "
        "OR (status = 'completed' AND error_code IS NULL)",
        name="ck_practice_trace_spans_error_status",
    ),
)

grade_review_events = Table(
    "grade_review_events",
    metadata,
    Column("review_event_id", Text, nullable=False),
    Column("review_chain_id", Text, nullable=False),
    Column("idempotency_key", Text, nullable=False),
    Column("action", Text, nullable=False),
    Column("user_id", Text, nullable=False),
    Column("exam_id", Text, nullable=False),
    Column("subject_id", Text, nullable=False),
    Column("practice_session_id", Text, nullable=False),
    Column("checkpoint_key", Text, nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("review_event_id", name="pk_grade_review_events"),
    UniqueConstraint("user_id", "idempotency_key", name="uq_grade_review_events_user_idem"),
    CheckConstraint(
        "action IN ('dispute', 'uphold', 'overturn')",
        name="ck_grade_review_events_action",
    ),
    CheckConstraint(
        "jsonb_typeof(payload) = 'object'",
        name="ck_grade_review_events_payload_object",
    ),
)

Index(
    "ix_grade_review_events_scope_created",
    grade_review_events.c.user_id,
    grade_review_events.c.exam_id,
    grade_review_events.c.subject_id,
    grade_review_events.c.created_at,
)
Index(
    "ix_grade_review_events_chain_created",
    grade_review_events.c.review_chain_id,
    grade_review_events.c.created_at,
)

Index(
    "ix_practice_trace_spans_name_created",
    practice_trace_spans.c.span_name,
    practice_trace_spans.c.created_at,
    practice_trace_spans.c.trace_id,
)

learning_memories = Table(
    "learning_memories",
    metadata,
    Column("memory_id", Text, primary_key=True),
    Column("user_id", Text, nullable=False),
    Column("exam_id", Text, nullable=False),
    Column("subject_id", Text, nullable=False),
    Column("memory_namespace", Text, nullable=False),
    Column("slot_key", Text, nullable=False),
    Column("value", JSONB, nullable=False),
    Column("confidence", Float, nullable=False),
    Column("evidence_count", Integer, nullable=False),
    Column("lifecycle_state", Text, nullable=False),
    Column("version", Integer, nullable=False),
    Column("row_version", Integer, nullable=False),
    Column("valid_from", DateTime(timezone=True), nullable=False),
    Column("valid_to", DateTime(timezone=True), nullable=True),
    Column(
        "superseded_by",
        Text,
        ForeignKey(
            "learning_memories.memory_id",
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=True,
    ),
    Column("contested_group_id", Text, nullable=True),
    Column(
        "content_embedding",
        Vector(LEARNING_MEMORY_EMBEDDING_DIMENSION),
        nullable=True,
    ),
    Column("policy_version", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
    UniqueConstraint(
        "user_id",
        "exam_id",
        "subject_id",
        "memory_namespace",
        "slot_key",
        "version",
        name="uq_learning_memories_scope_slot_version",
    ),
    CheckConstraint(
        "memory_namespace IN ('mastery', 'error_pattern', 'plan', 'profile', 'preference')",
        name="ck_learning_memories_namespace",
    ),
    CheckConstraint(
        "value ->> 'type' = memory_namespace",
        name="ck_learning_memories_value_namespace",
    ),
    CheckConstraint(
        "confidence >= 0.0 AND confidence <= 1.0",
        name="ck_learning_memories_confidence_range",
    ),
    CheckConstraint("evidence_count >= 1", name="ck_learning_memories_evidence_count"),
    CheckConstraint("version >= 1", name="ck_learning_memories_version"),
    CheckConstraint("row_version >= 1", name="ck_learning_memories_row_version"),
    CheckConstraint(
        "lifecycle_state IN ('active', 'archived', 'invalidated', 'contested')",
        name="ck_learning_memories_lifecycle_state",
    ),
    CheckConstraint(
        "valid_to IS NULL OR valid_to >= valid_from",
        name="ck_learning_memories_valid_interval",
    ),
    CheckConstraint(
        "lifecycle_state <> 'active' OR valid_to IS NULL",
        name="ck_learning_memories_active_open_interval",
    ),
    CheckConstraint(
        "lifecycle_state NOT IN ('archived', 'invalidated') OR valid_to IS NOT NULL",
        name="ck_learning_memories_terminal_closed_interval",
    ),
)

Index(
    "ix_learning_memories_scope_slot_state",
    learning_memories.c.user_id,
    learning_memories.c.exam_id,
    learning_memories.c.subject_id,
    learning_memories.c.memory_namespace,
    learning_memories.c.slot_key,
    learning_memories.c.lifecycle_state,
)
Index(
    "uq_learning_memories_scope_slot_active",
    learning_memories.c.user_id,
    learning_memories.c.exam_id,
    learning_memories.c.subject_id,
    learning_memories.c.memory_namespace,
    learning_memories.c.slot_key,
    unique=True,
    postgresql_where=learning_memories.c.lifecycle_state == "active",
)
Index(
    "ix_learning_memories_content_embedding_hnsw",
    learning_memories.c.content_embedding,
    postgresql_using="hnsw",
    postgresql_ops={"content_embedding": "vector_cosine_ops"},
    postgresql_where=learning_memories.c.content_embedding.is_not(None),
)

event_correction_targets = Table(
    "event_correction_targets",
    metadata,
    Column(
        "event_id",
        Text,
        ForeignKey("learning_events.event_id"),
        nullable=False,
    ),
    Column(
        "memory_id",
        Text,
        ForeignKey("learning_memories.memory_id"),
        nullable=False,
    ),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    PrimaryKeyConstraint(
        "event_id",
        "memory_id",
        name="pk_event_correction_targets",
    ),
)

event_plan_transition_targets = Table(
    "event_plan_transition_targets",
    metadata,
    Column(
        "event_id",
        Text,
        ForeignKey("learning_events.event_id"),
        nullable=False,
    ),
    Column(
        "memory_id",
        Text,
        ForeignKey("learning_memories.memory_id"),
        nullable=False,
    ),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("event_id", name="uq_event_plan_transition_targets_event"),
)

memory_provenance = Table(
    "memory_provenance",
    metadata,
    Column(
        "memory_id",
        Text,
        ForeignKey("learning_memories.memory_id"),
        nullable=False,
    ),
    Column(
        "event_id",
        Text,
        ForeignKey("learning_events.event_id"),
        nullable=False,
    ),
    Column("relation_type", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    PrimaryKeyConstraint(
        "memory_id",
        "event_id",
        "relation_type",
        name="pk_memory_provenance",
    ),
    CheckConstraint(
        "relation_type IN ('created_by', 'merged_from', 'contradicted_by', 'invalidated_by')",
        name="ck_memory_provenance_relation_type",
    ),
)

student_model_snapshots = Table(
    "student_model_snapshots",
    metadata,
    Column("snapshot_id", Text, primary_key=True),
    Column("user_id", Text, nullable=False),
    Column("exam_id", Text, nullable=False),
    Column("subject_id", Text, nullable=False),
    Column("model", JSONB, nullable=False),
    Column("projection_version", Integer, nullable=False),
    Column("source_event_watermark", Text, nullable=False),
    Column("source_memory_watermark", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "projection_version >= 1",
        name="ck_student_model_snapshots_projection_version",
    ),
)

lifecycle_decisions = Table(
    "lifecycle_decisions",
    metadata,
    Column("decision_id", Text, primary_key=True),
    Column("trace_id", Text, nullable=False),
    Column("event_id", Text, ForeignKey("learning_events.event_id"), nullable=False),
    Column("input_summary", JSONB, nullable=False),
    Column("candidate_memory_ids", JSONB, nullable=False),
    Column("operation", Text, nullable=False),
    Column("reason", Text, nullable=False),
    Column("confidence", Float, nullable=False),
    Column("policy_version", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("btrim(trace_id) <> ''", name="ck_lifecycle_decisions_trace_nonempty"),
    CheckConstraint(
        "jsonb_typeof(input_summary) = 'object'",
        name="ck_lifecycle_decisions_input_object",
    ),
    CheckConstraint(
        "jsonb_typeof(candidate_memory_ids) = 'array'",
        name="ck_lifecycle_decisions_candidates_array",
    ),
    CheckConstraint(
        "operation IN ('ADD', 'NO_OP', 'MERGE', 'SUPERSEDE', 'INVALIDATE', 'CONTESTED')",
        name="ck_lifecycle_decisions_operation",
    ),
    CheckConstraint(
        "confidence >= 0.0 AND confidence <= 1.0",
        name="ck_lifecycle_decisions_confidence_range",
    ),
)

Index(
    "ix_lifecycle_decisions_trace_created",
    lifecycle_decisions.c.trace_id,
    lifecycle_decisions.c.created_at,
    lifecycle_decisions.c.decision_id,
)

memory_change_log = Table(
    "memory_change_log",
    metadata,
    Column("change_id", Text, primary_key=True),
    Column(
        "decision_id",
        Text,
        ForeignKey("lifecycle_decisions.decision_id"),
        nullable=False,
    ),
    Column("before_state", JSONB(none_as_null=True), nullable=True),
    Column("after_state", JSONB(none_as_null=True), nullable=True),
    Column("apply_state", Text, nullable=False),
    Column("memory_id", Text, ForeignKey("learning_memories.memory_id"), nullable=True),
    Column("expected_row_version", Integer, nullable=True),
    Column("actual_row_version", Integer, nullable=True),
    Column("error_code", Text, nullable=True),
    Column("trace_id", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("btrim(trace_id) <> ''", name="ck_memory_change_log_trace_nonempty"),
    CheckConstraint(
        "before_state IS NULL OR jsonb_typeof(before_state) = 'object'",
        name="ck_memory_change_log_before_object",
    ),
    CheckConstraint(
        "after_state IS NULL OR jsonb_typeof(after_state) = 'object'",
        name="ck_memory_change_log_after_object",
    ),
    CheckConstraint(
        "apply_state IN ('PLANNED', 'APPLIED', 'IDEMPOTENT', 'CONTESTED', 'STALE', 'FAILED')",
        name="ck_memory_change_log_apply_state",
    ),
    CheckConstraint(
        "expected_row_version IS NULL OR expected_row_version >= 1",
        name="ck_memory_change_log_expected_row_version",
    ),
    CheckConstraint(
        "actual_row_version IS NULL OR actual_row_version >= 1",
        name="ck_memory_change_log_actual_row_version",
    ),
    CheckConstraint(
        "apply_state <> 'FAILED' OR error_code IS NOT NULL",
        name="ck_memory_change_log_failed_error",
    ),
    CheckConstraint(
        "apply_state = 'FAILED' OR error_code IS NULL",
        name="ck_memory_change_log_error_only_on_failure",
    ),
    CheckConstraint(
        "apply_state NOT IN ('APPLIED', 'CONTESTED') "
        "OR (memory_id IS NOT NULL AND after_state IS NOT NULL)",
        name="ck_memory_change_log_success_after",
    ),
    CheckConstraint(
        "apply_state <> 'STALE' "
        "OR (expected_row_version IS NOT NULL AND actual_row_version IS NOT NULL)",
        name="ck_memory_change_log_stale_versions",
    ),
)

Index(
    "ix_memory_change_log_decision_created",
    memory_change_log.c.decision_id,
    memory_change_log.c.created_at,
    memory_change_log.c.change_id,
)
Index(
    "ix_memory_change_log_trace_created",
    memory_change_log.c.trace_id,
    memory_change_log.c.created_at,
    memory_change_log.c.change_id,
)

__all__ = [
    "LEARNING_MEMORY_EMBEDDING_DIMENSION",
    "baseline_memory_facts",
    "event_correction_targets",
    "event_plan_transition_targets",
    "grade_review_events",
    "learning_events",
    "learning_memories",
    "lifecycle_decisions",
    "memory_change_log",
    "memory_provenance",
    "metadata",
    "practice_trace_spans",
    "practice_workflow_checkpoints",
    "student_model_snapshots",
]
