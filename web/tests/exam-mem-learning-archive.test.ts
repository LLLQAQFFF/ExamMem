import assert from "node:assert/strict";
import test from "node:test";

import {
  buildLearningArchiveGraph,
  learningArchiveKnowledgePointFilter,
  type LearningArchive,
} from "../lib/exam-mem-learning-archive";

test("learning archive filters only when the learner selects a chapter or point", () => {
  assert.equal(learningArchiveKnowledgePointFilter("", null), undefined);
  assert.deepEqual(
    learningArchiveKnowledgePointFilter("", ["kp-1", "kp-2"]),
    ["kp-1", "kp-2"],
  );
  assert.deepEqual(
    learningArchiveKnowledgePointFilter("kp-2", ["kp-1", "kp-2"]),
    ["kp-2"],
  );
});

test("ExamMem graph keeps formal evidence, versioned memory and L3 projection linked", () => {
  const archive: LearningArchive = {
    scope: {
      exam_id: "plan:test",
      subject_id: "math",
      taxonomy_version: "math-v1",
    },
    counts: { l1: 1, l2: 1, l3: 2 },
    l3_scope: {
      exam_id: "plan:test",
      subject_id: "math",
      taxonomy_version: null,
      aggregation: "plan_subject_all_taxonomy_versions",
    },
    learning_path_observations: [],
    l1: [
      {
        created_at: "2026-08-14T00:00:00Z",
        event: {
          event_id: "event-1",
          event_type: "answer_attempt",
          session_id: "practice-1",
          question_id: "question-1",
          knowledge_point_ids: ["kp-1"],
          answer_correct: false,
        },
        detail: {
          question: {
            question_id: "question-1",
            stem: "求函数极限",
            reference_answer: "使用洛必达法则",
            grading_rubric: {},
          },
          submitted_answer: {
            answer: "直接代入",
            submitted_at: "2026-08-14T00:00:00Z",
          },
          grade_result: { correct: false, score: 0.2, evidence: ["步骤不完整"] },
          diagnosis_result: { error_type: "conceptual", explanation: "未识别未定式" },
          recommendation: {
            target_knowledge_point_id: "kp-1",
            reason_codes: ["REMEDIATE_ERROR_PATTERN"],
            source_memory_ids: ["memory-1"],
          },
          checkpoint_key: "answer:1",
        },
        memories: [
          {
            memory_id: "memory-1",
            memory_namespace: "mastery",
            slot_key: "mastery:kp-1",
            version: 1,
            lifecycle_state: "active",
            relation_type: "supports",
          },
        ],
        source: {
          attempt_id: "attempt-1",
          assessment_id: "exam-1",
          assessment_title: "函数极限检测",
          assessment_version: 2,
          taxonomy_version: "math-v1",
        },
      },
    ],
    l2: [
      {
        memory: {
          memory_id: "memory-1",
          scope: {
            exam_id: "plan:test",
            subject_id: "math",
            memory_namespace: "mastery",
          },
          slot_key: "mastery:kp-1",
          value: { type: "mastery", level: "developing", score: 0.4 },
          confidence: 0.9,
          evidence_count: 1,
          lifecycle_state: "active",
          version: 1,
          provenance: ["event-1"],
        },
        sources: [
          {
            event_id: "event-1",
            event_type: "answer_attempt",
            session_id: "practice-1",
            knowledge_point_ids: ["kp-1"],
            attempt_id: "attempt-1",
            assessment_id: "exam-1",
            assessment_title: "函数极限检测",
            assessment_version: 2,
            taxonomy_version: "math-v1",
          },
        ],
      },
    ],
    l3: {
      snapshot_id: "snapshot-1",
      model: {
        weak_points: ["kp-1"],
        mastered_points: [],
        stable_error_patterns: ["sign-error"],
        active_plans: [],
        projection_version: 3,
        source_watermark: "event-1",
      },
    },
  };

  const graph = buildLearningArchiveGraph(archive);

  assert.equal(
    graph.clusters.find((cluster) => cluster.id === "L1:quiz")?.label,
    "正式刷题证据",
  );
  assert.equal(
    graph.clusters.find((cluster) => cluster.id === "L2:quiz")?.label,
    "掌握度",
  );
  assert.equal(graph.clusters.some((cluster) => cluster.id === "L1:chat"), false);
  assert.equal(graph.clusters.some((cluster) => cluster.id === "L2:chat"), false);
  assert.ok(graph.edges.some((edge) => edge.source.startsWith("L2:quiz:")));
  assert.ok(graph.nodes.every((node) => node.href === "/exam-mem/memories"));
});
