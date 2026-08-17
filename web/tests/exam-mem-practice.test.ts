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
import {
  groupExamReviewHistory,
  listExamReviewHistory,
} from "../lib/exam-mem-product";
import type { Assessment } from "../lib/exam-mem-study-plans";

test("exam review history includes completed attempts from imported scopes", async () => {
  const originalFetch = globalThis.fetch;
  const requested: string[] = [];
  globalThis.fetch = async (input) => {
    const url = String(input);
    requested.push(url);
    const dynamic = url.includes("exam_id=plan%3Atest");
    return Response.json({
      sessions: dynamic
        ? [
            {
              practice_session_id: "practice:dynamic:completed",
              trace_id: "trace:dynamic:completed",
              started_at: "2026-08-14T06:35:00Z",
              step_state: "MEMORY_UPDATED",
              updated_at: "2026-08-14T06:51:00Z",
              attempt_number: 1,
              answer_count: 4,
              score: 0.75,
              correct_count: 3,
              current_checkpoint: {
                checkpoint_key: "answer:4",
                step_state: "MEMORY_UPDATED",
                question: null,
                grade_result: { correct: true, score: 1, evidence: ["ok"] },
                grade_artifact: null,
                diagnosis_result: null,
                recommendation: null,
                answered_question_count: 4,
                question_count: 4,
                completed: true,
              },
              runtime: null,
            },
            {
              practice_session_id: "practice:dynamic:v2",
              trace_id: "trace:dynamic:v2",
              started_at: "2026-08-14T07:00:00Z",
              step_state: "MEMORY_UPDATED",
              updated_at: "2026-08-14T07:15:00Z",
              attempt_number: 2,
              answer_count: 4,
              score: 1,
              correct_count: 4,
              current_checkpoint: {
                checkpoint_key: "answer:4",
                step_state: "MEMORY_UPDATED",
                question: null,
                grade_result: { correct: true, score: 1, evidence: ["ok"] },
                grade_artifact: null,
                diagnosis_result: null,
                recommendation: null,
                answered_question_count: 4,
                question_count: 4,
                completed: true,
              },
              runtime: null,
            },
          ]
        : [],
    });
  };
  const assessments: Assessment[] = [
    {
      assessment_id: "assessment:dynamic",
      title: "动态知识点专项检测",
      exam_id: "plan:test",
      subject_id: "math",
      taxonomy_version: "ptest_s001_v1",
      knowledge_point_ids: ["ptest.s001.m001.k001"],
      latest_version: 2,
      attempts: [
        {
          attempt_id: "attempt:dynamic:v2",
          assessment_version: 2,
          practice_session_id: "practice:dynamic:v2",
          trace_id: "trace:dynamic:v2",
          status: "completed",
          started_at: "2026-08-14T07:00:00Z",
          completed_at: "2026-08-14T07:15:00Z",
        },
        {
          attempt_id: "attempt:dynamic",
          assessment_version: 1,
          practice_session_id: "practice:dynamic:completed",
          trace_id: "trace:dynamic:completed",
          status: "completed",
          started_at: "2026-08-14T06:35:00Z",
          completed_at: "2026-08-14T06:51:00Z",
        },
      ],
    },
  ];

  try {
    const history = await listExamReviewHistory(assessments);
    const [latest] = history;
    const [group] = groupExamReviewHistory(history);
    assert.equal(requested.length, 2);
    assert.equal(latest.assessment_title, "动态知识点专项检测");
    assert.equal(latest.attempt_status, "completed");
    assert.equal(latest.exam_id, "plan:test");
    assert.equal(latest.subject_id, "math");
    assert.equal(latest.score, 1);
    assert.deepEqual(group.versions, [2, 1]);
    assert.deepEqual(
      group.attempts.map((attempt) => attempt.assessment_attempt_number),
      [2, 1],
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

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
        language: "zh",
        attachments: [],
      }),
      (error: unknown) =>
        error instanceof PracticeRequestError && error.message === "Internal Server Error",
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("generated practice reports real server stages and emitted question counts", async () => {
  const originalFetch = globalThis.fetch;
  const requested: string[] = [];
  const progress: Array<{ stage: string; completed_questions: number }> = [];
  const result = {
    session_id: "session:generated",
    turn_id: "turn:generated",
    response: "ready",
    practice: {
      practice_session_id: "practice:web:stream",
      trace_id: "trace:web:stream",
      scope: { exam_id: "plan:test", subject_id: "math", memory_namespace: "learning" },
      step_state: "QUESTION_PRESENTED",
      question: {
        question_id: "generated:q1",
        stem: "题目一",
        knowledge_point_ids: ["ptest.s001.m001.k001"],
        difficulty: 0.5,
      },
      grade_result: null,
      diagnosis_result: null,
      recommendation: null,
      resumed_from_state: "QUESTION_PRESENTED",
      replayed: false,
    },
  };
  const lines = [
    { type: "progress", stage: "scope", completed_questions: 0, total_questions: 2 },
    { type: "progress", stage: "exploring", completed_questions: 0, total_questions: 2 },
    { type: "progress", stage: "planning", completed_questions: 0, total_questions: 2 },
    { type: "progress", stage: "generating", completed_questions: 1, total_questions: 2 },
    { type: "progress", stage: "generating", completed_questions: 2, total_questions: 2 },
    { type: "progress", stage: "persisting", completed_questions: 2, total_questions: 2 },
    { type: "progress", stage: "starting", completed_questions: 2, total_questions: 2 },
    { type: "complete", result },
  ];
  globalThis.fetch = async (input) => {
    requested.push(String(input));
    return new Response(`${lines.map((line) => JSON.stringify(line)).join("\n")}\n`, {
      headers: { "Content-Type": "application/x-ndjson" },
    });
  };

  try {
    const generated = await generateExamPractice(
      {
        identity: createPracticeIdentity("stream", "plan:test", "math"),
        learningPathId: "plan:test:1:ptest.s001.m001.k001",
        knowledgePointId: "ptest.s001.m001.k001",
        knowledgePointName: "知识点一",
        taxonomyVersion: "ptest_s001_v1",
        numQuestions: 2,
        difficulty: "auto",
        language: "zh",
        attachments: [],
      },
      (event) => progress.push(event),
    );

    assert.match(requested[0], /\/practice\/generate\/stream$/);
    assert.deepEqual(
      progress.map((event) => [event.stage, event.completed_questions]),
      [
        ["scope", 0],
        ["exploring", 0],
        ["planning", 0],
        ["generating", 1],
        ["generating", 2],
        ["persisting", 2],
        ["starting", 2],
      ],
    );
    assert.equal(generated.practice.question?.question_id, "generated:q1");
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
  assert.doesNotMatch(learning, /summarizeLearningPath|organizeLearningRecord|Agent 整理学习记录/);
  assert.match(practice, /GENERATION_WAIT_STEPS/);
  assert.match(practice, /正在连接出题服务/);
  assert.match(practice, /状态来自服务端真实出题事件/);
  assert.match(memory, /<MemoryIssuesWorkbench embedded/);
});
