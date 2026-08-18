import assert from "node:assert/strict";
import test from "node:test";

import {
  learningMemoryQuery,
  memoryValueSummary,
} from "../lib/exam-mem-memory";
import { getLearningProfile } from "../lib/exam-mem-learning-profile";

test("Learning Memory query always carries the authenticated three-dimensional URL scope", () => {
  const params = new URLSearchParams(
    learningMemoryQuery(
      "postgraduate_entrance_exam",
      "math_1",
      "error_pattern",
      "  matrix  ",
    ),
  );
  assert.deepEqual(Object.fromEntries(params), {
    exam_id: "postgraduate_entrance_exam",
    subject_id: "math_1",
    memory_namespace: "error_pattern",
    query: "matrix",
  });
  assert.equal(params.has("user_id"), false);
});

test("typed Memory values have stable human summaries", () => {
  assert.equal(memoryValueSummary({ type: "mastery", level: "developing", score: 0.4 }), "developing · 0.4");
  assert.equal(memoryValueSummary({ type: "error_pattern", summary: "Reversed condition" }), "Reversed condition");
  assert.equal(memoryValueSummary({ type: "plan", goal: "Review Bayes", status: "active" }), "Review Bayes · active");
});

test("learning profile query carries only the authenticated study-plan scope", async () => {
  const originalFetch = globalThis.fetch;
  let requested = "";
  globalThis.fetch = async (input) => {
    requested = String(input);
    return Response.json({ summary: {}, knowledge_points: [], review_queue: [] });
  };

  try {
    await getLearningProfile({
      examId: "plan:math",
      subjectId: "math-one",
      taxonomyVersion: "pmath_s001_v3",
    });
    const query = new URL(requested, "http://localhost").searchParams;
    assert.equal(query.get("exam_id"), "plan:math");
    assert.equal(query.get("subject_id"), "math-one");
    assert.equal(query.get("taxonomy_version"), "pmath_s001_v3");
    assert.equal(query.has("user_id"), false);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
