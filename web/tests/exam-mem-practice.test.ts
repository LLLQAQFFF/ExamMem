import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

import {
  buildPracticeAnswerRequest,
  clearPracticeSession,
  createPracticeIdentity,
  generateExamPractice,
  loadPracticeSession,
  PRACTICE_SESSION_STORAGE_KEY,
  PracticeRequestError,
  preparePracticeAnswerRequest,
  savePracticeSession,
} from "../lib/exam-mem-practice";

test("practice requests surface a plain-text proxy failure without a JSON parse error", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response("Internal Server Error", { status: 500 });
  try {
    await assert.rejects(
      generateExamPractice({
        identity: createPracticeIdentity("dynamic", "ptest", "ptest"),
        learningPathId: "plan:test",
        knowledgePointId: "ptest.module.point",
        knowledgePointName: "Imported knowledge point",
        taxonomyVersion: "ptest_s001_v1",
        numQuestions: 4,
        difficulty: "auto",
        attachments: [],
      }),
      (error: unknown) =>
        error instanceof PracticeRequestError && error.message === "Internal Server Error",
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("browser sends only public question identity and stable retry material", () => {
  const identity = createPracticeIdentity("fixed-uuid");
  const request = buildPracticeAnswerRequest({
    identity,
    sessionId: "deeptutor-session",
    questionId: "stage07:probability:bayes:001",
    answer: "0.48",
    submittedAt: "2026-08-13T12:00:00.000Z",
    attemptNumber: 2,
  });

  assert.deepEqual(identity, {
    practiceSessionId: "practice:web:fixed-uuid",
    traceId: "trace:web:fixed-uuid",
    examId: "postgraduate_entrance_exam",
    subjectId: "math_1",
  });
  assert.equal(request.idempotency_key, "answer:web:practice:web:fixed-uuid:2");
  assert.equal(request.exam_id, "postgraduate_entrance_exam");
  assert.equal(request.subject_id, "math_1");
  assert.equal("reference_answer" in request, false);
  assert.equal("grading_rubric" in request, false);
});

test("failed answer retries reuse the complete immutable submission", () => {
  const original = buildPracticeAnswerRequest({
    identity: createPracticeIdentity("fixed-uuid"),
    sessionId: "deeptutor-session",
    questionId: "stage07:probability:bayes:001",
    answer: "0.48",
    submittedAt: "2026-08-13T12:00:00.000Z",
    attemptNumber: 1,
  });
  const replay = preparePracticeAnswerRequest(original, {
    identity: createPracticeIdentity("fixed-uuid"),
    sessionId: "deeptutor-session",
    questionId: original.question_id,
    answer: "changed after response loss",
    submittedAt: "2026-08-13T12:01:00.000Z",
    attemptNumber: 1,
  });
  assert.equal(replay, original);
});

test("practice recovery snapshot persists without server-only grading data", () => {
  const values = new Map<string, string>();
  const storage = {
    getItem: (key: string) => values.get(key) ?? null,
    removeItem: (key: string) => values.delete(key),
    setItem: (key: string, value: string) => values.set(key, value),
  };
  const snapshot = {
    identity: createPracticeIdentity("fixed-uuid"),
    turn: {
      session_id: "deeptutor-session",
      turn_id: "turn-1",
      response: "ready",
      practice: {
        practice_session_id: "practice:web:fixed-uuid",
        trace_id: "trace:web:fixed-uuid",
        scope: {
          exam_id: "postgraduate_entrance_exam",
          subject_id: "math_1",
          memory_namespace: "mastery",
        },
        step_state: "QUESTION_READY",
        question: null,
        grade_result: null,
        diagnosis_result: null,
        recommendation: null,
        resumed_from_state: "IDLE",
        replayed: false,
      },
    },
    answer: "0.48",
    attemptNumber: 1,
    pendingRequest: null,
  };

  savePracticeSession(storage, snapshot);
  assert.deepEqual(loadPracticeSession(storage), snapshot);
  assert.equal(values.get(PRACTICE_SESSION_STORAGE_KEY)?.includes("reference_answer"), false);
  clearPracticeSession(storage);
  assert.equal(loadPracticeSession(storage), null);
});

test("practice route owns scrolling and narrow screens defer the side column", () => {
  const page = readFileSync(
    path.resolve(process.cwd(), "app/(utility)/exam-mem/practice/page.tsx"),
    "utf8",
  );
  const workbench = readFileSync(
    path.resolve(process.cwd(), "components/exam-mem/PracticeWorkbench.tsx"),
    "utf8",
  );
  assert.match(page, /h-full min-h-0 overflow-y-auto/);
  assert.match(
    workbench,
    /xl:grid-cols-\[minmax\(0,1fr\)_minmax\(280px,340px\)\]/,
  );
});

test("smart exam prep owns imported scopes, versioned assessments, and merged memory issues", () => {
  const shell = readFileSync(
    path.resolve(process.cwd(), "components/exam-mem/SmartExamPrepShell.tsx"),
    "utf8",
  );
  const learningSpace = readFileSync(
    path.resolve(process.cwd(), "components/space/SpaceDashboard.tsx"),
    "utf8",
  );
  const practice = readFileSync(
    path.resolve(process.cwd(), "components/exam-mem/PracticeWorkbench.tsx"),
    "utf8",
  );
  const learning = readFileSync(
    path.resolve(process.cwd(), "components/exam-mem/LearningPathsWorkbench.tsx"),
    "utf8",
  );
  const memory = readFileSync(
    path.resolve(process.cwd(), "components/exam-mem/LearningMemoryWorkbench.tsx"),
    "utf8",
  );

  assert.match(shell, /href: "\/exam-mem\/learning"/);
  assert.doesNotMatch(shell, /href: "\/exam-mem\/issues"/);
  assert.doesNotMatch(learningSpace, /mastery_path/);
  assert.match(practice, /generateExamPractice/);
  assert.match(practice, /listStudyPlans/);
  assert.match(practice, /taxonomyVersion:/);
  assert.match(practice, /repeatAssessmentVersion/);
  assert.doesNotMatch(practice, /fetchAllProgress|fetchMasteryMap/);
  assert.match(practice, /accept="\.pdf,\.txt,\.md"/);
  assert.doesNotMatch(practice, /\.pptx|\.docx/);
  assert.match(learning, /importStudyPlan/);
  assert.match(learning, /publishStudyPlan/);
  assert.match(learning, /openStudyObjective/);
  assert.match(learning, /router\.push\(session\.chat_url\)/);
  assert.match(memory, /<MemoryIssuesWorkbench embedded/);
});
