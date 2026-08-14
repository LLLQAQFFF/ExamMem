import { apiFetch, apiUrl } from "@/lib/api";
import type { PracticeTurnResponse } from "@/lib/exam-mem-practice";
import type { Assessment, AssessmentAttempt } from "@/lib/exam-mem-study-plans";

export interface PracticeCheckpointSummary {
  checkpoint_key: string;
  step_state: string;
  question: { question_id: string; stem: string } | null;
  grade_result: { correct: boolean; score: number; evidence: string[] } | null;
  grade_artifact: {
    reused: boolean;
    source_checkpoint: string | null;
    identity: Record<string, string>;
  } | null;
  diagnosis_result: { error_type: string | null; explanation: string } | null;
  recommendation: { reason_codes: string[]; source_memory_ids: string[] } | null;
  answered_question_count: number;
  question_count: number;
  completed: boolean;
}

export interface PracticeHistoryItem {
  practice_session_id: string;
  trace_id: string;
  started_at: string;
  step_state: string;
  updated_at: string;
  attempt_number: number;
  answer_count: number;
  score: number | null;
  correct_count: number;
  current_checkpoint: PracticeCheckpointSummary;
  runtime: RuntimeSnapshot | null;
  exam_id: string;
  subject_id: string;
}

export interface ExamReviewHistoryItem extends PracticeHistoryItem {
  assessment_id: string | null;
  assessment_title: string | null;
  assessment_version: number | null;
  attempt_status: AssessmentAttempt["status"] | null;
  completed_at: string | null;
  assessment_attempt_number: number | null;
}

export interface ExamReviewGroup {
  key: string;
  assessment_id: string | null;
  title: string;
  exam_id: string;
  subject_id: string;
  versions: number[];
  attempts: ExamReviewHistoryItem[];
}

export interface RuntimeSnapshot {
  config_revision: string;
  backend_mode: string;
  side_effects: string[];
}

export interface ExamReview extends PracticeHistoryItem {
  checkpoints: PracticeCheckpointSummary[];
  trace: Array<Record<string, unknown>>;
  lifecycle: {
    decisions: Array<Record<string, unknown>>;
    changes: Array<Record<string, unknown>>;
  };
  grade_reviews: Array<{
    review_chain_id: string;
    action: string;
    reason: string;
    checkpoint_key: string;
  }>;
}

export interface MemoryIssue {
  issue_id: string;
  type: string;
  status: string;
  summary: string;
  trace_id?: string;
  practice_session_id?: string;
  memory_id?: string;
}

export interface ConfigurationView {
  saved: ConfigurationState;
  effective: ConfigurationState;
  pinned: RuntimeSnapshot | null;
  restart_required?: boolean;
}

export interface ConfigurationState {
  revision: string;
  settings: {
    enabled: boolean;
    subject: string;
    memory_backend: string;
    capabilities: { exam_practice: boolean };
  };
  side_effects: string[];
}

async function jsonOrThrow<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const payload = (await response.json().catch(() => ({}))) as { detail?: string };
    throw new Error(payload.detail || `ExamMem request failed (${response.status}).`);
  }
  return (await response.json()) as T;
}

export async function listPracticeHistory(
  examId = "postgraduate_entrance_exam",
  subjectId = "math_1",
): Promise<PracticeHistoryItem[]> {
  const query = new URLSearchParams({ exam_id: examId, subject_id: subjectId });
  const payload = await jsonOrThrow<{ sessions: PracticeHistoryItem[] }>(
    await apiFetch(apiUrl(`/api/v1/exam-mem/practice/sessions?${query}`)),
  );
  return payload.sessions.map((item) => ({
    ...item,
    exam_id: examId,
    subject_id: subjectId,
  }));
}

export async function listExamReviewHistory(
  assessments: Assessment[],
): Promise<ExamReviewHistoryItem[]> {
  const scopes = new Map<string, { examId: string; subjectId: string }>();
  const addScope = (examId: string, subjectId: string) => {
    scopes.set(`${examId}\u001f${subjectId}`, { examId, subjectId });
  };
  addScope("postgraduate_entrance_exam", "math_1");
  for (const assessment of assessments) {
    addScope(assessment.exam_id, assessment.subject_id);
  }

  const sessions = (
    await Promise.all(
      [...scopes.values()].map(({ examId, subjectId }) =>
        listPracticeHistory(examId, subjectId),
      ),
    )
  ).flat();
  const attempts = new Map<
    string,
    {
      assessment: Assessment;
      attempt: AssessmentAttempt;
      assessmentAttemptNumber: number;
    }
  >();
  for (const assessment of assessments) {
    for (const [index, attempt] of assessment.attempts.entries()) {
      attempts.set(attempt.practice_session_id, {
        assessment,
        attempt,
        assessmentAttemptNumber: assessment.attempts.length - index,
      });
    }
  }

  return sessions
    .map((session) => {
      const matched = attempts.get(session.practice_session_id);
      return {
        ...session,
        assessment_id: matched?.assessment.assessment_id ?? null,
        assessment_title: matched?.assessment.title ?? null,
        assessment_version: matched?.attempt.assessment_version ?? null,
        attempt_status: matched?.attempt.status ?? null,
        completed_at: matched?.attempt.completed_at ?? null,
        assessment_attempt_number: matched?.assessmentAttemptNumber ?? null,
      };
    })
    .sort((left, right) => right.updated_at.localeCompare(left.updated_at));
}

export function groupExamReviewHistory(
  history: ExamReviewHistoryItem[],
): ExamReviewGroup[] {
  const groups = new Map<string, ExamReviewGroup>();
  for (const item of history) {
    const key = item.assessment_id ?? `legacy:${item.exam_id}:${item.subject_id}`;
    const group = groups.get(key) ?? {
      key,
      assessment_id: item.assessment_id,
      title: item.assessment_title ?? "Legacy practice",
      exam_id: item.exam_id,
      subject_id: item.subject_id,
      versions: [],
      attempts: [],
    };
    group.attempts.push(item);
    if (
      item.assessment_version !== null &&
      !group.versions.includes(item.assessment_version)
    ) {
      group.versions.push(item.assessment_version);
      group.versions.sort((left, right) => right - left);
    }
    groups.set(key, group);
  }
  return [...groups.values()].sort((left, right) =>
    right.attempts[0].updated_at.localeCompare(left.attempts[0].updated_at),
  );
}

export async function resumePractice(
  id: string,
  examId = "postgraduate_entrance_exam",
  subjectId = "math_1",
): Promise<PracticeTurnResponse> {
  const query = new URLSearchParams({ exam_id: examId, subject_id: subjectId });
  return jsonOrThrow<PracticeTurnResponse>(
    await apiFetch(
      apiUrl(`/api/v1/exam-mem/practice/sessions/${encodeURIComponent(id)}/resume?${query}`),
      { method: "POST" },
    ),
  );
}

export async function getExamReview(
  id: string,
  examId = "postgraduate_entrance_exam",
  subjectId = "math_1",
): Promise<ExamReview> {
  const query = new URLSearchParams({ exam_id: examId, subject_id: subjectId });
  const review = await jsonOrThrow<Omit<ExamReview, "exam_id" | "subject_id">>(
    await apiFetch(
      apiUrl(`/api/v1/exam-mem/practice/sessions/${encodeURIComponent(id)}?${query}`),
    ),
  );
  return { ...review, exam_id: examId, subject_id: subjectId };
}

export async function listMemoryIssues(): Promise<MemoryIssue[]> {
  const payload = await jsonOrThrow<{ issues: MemoryIssue[] }>(
    await apiFetch(apiUrl("/api/v1/exam-mem/issues")),
  );
  return payload.issues;
}

export async function disputeGrade(options: {
  practiceSessionId: string;
  checkpointKey: string;
  reason: string;
  idempotencyKey: string;
  examId: string;
  subjectId: string;
}): Promise<void> {
  await jsonOrThrow(
    await apiFetch(apiUrl("/api/v1/exam-mem/grade-reviews/disputes"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        practice_session_id: options.practiceSessionId,
        checkpoint_key: options.checkpointKey,
        reason: options.reason,
        idempotency_key: options.idempotencyKey,
        exam_id: options.examId,
        subject_id: options.subjectId,
      }),
    }),
  );
}

export async function upholdGrade(options: {
  reviewChainId: string;
  practiceSessionId: string;
  checkpointKey: string;
  reason: string;
  idempotencyKey: string;
  examId: string;
  subjectId: string;
}): Promise<void> {
  await jsonOrThrow(
    await apiFetch(
      apiUrl(`/api/v1/exam-mem/grade-reviews/${encodeURIComponent(options.reviewChainId)}/dispositions`),
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "uphold",
          practice_session_id: options.practiceSessionId,
          checkpoint_key: options.checkpointKey,
          reason: options.reason,
          idempotency_key: options.idempotencyKey,
          exam_id: options.examId,
          subject_id: options.subjectId,
        }),
      },
    ),
  );
}

export async function getConfiguration(
  practiceSessionId?: string,
): Promise<ConfigurationView> {
  const query = practiceSessionId
    ? `?practice_session_id=${encodeURIComponent(practiceSessionId)}`
    : "";
  return jsonOrThrow<ConfigurationView>(
    await apiFetch(apiUrl(`/api/v1/exam-mem/configuration${query}`)),
  );
}

export async function saveConfiguration(
  settings: ConfigurationState["settings"],
): Promise<ConfigurationView> {
  return jsonOrThrow<ConfigurationView>(
    await apiFetch(apiUrl("/api/v1/exam-mem/configuration"), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(settings),
    }),
  );
}
