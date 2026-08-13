import { apiFetch, apiUrl } from "@/lib/api";
import type { PracticeTurnResponse } from "@/lib/exam-mem-practice";

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
}

export interface PracticeHistoryItem {
  practice_session_id: string;
  trace_id: string;
  step_state: string;
  updated_at: string;
  answer_count: number;
  current_checkpoint: PracticeCheckpointSummary;
  runtime: RuntimeSnapshot | null;
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

export async function listPracticeHistory(): Promise<PracticeHistoryItem[]> {
  const payload = await jsonOrThrow<{ sessions: PracticeHistoryItem[] }>(
    await apiFetch(apiUrl("/api/v1/exam-mem/practice/sessions")),
  );
  return payload.sessions;
}

export async function resumePractice(id: string): Promise<PracticeTurnResponse> {
  return jsonOrThrow<PracticeTurnResponse>(
    await apiFetch(
      apiUrl(`/api/v1/exam-mem/practice/sessions/${encodeURIComponent(id)}/resume`),
      { method: "POST" },
    ),
  );
}

export async function getExamReview(id: string): Promise<ExamReview> {
  return jsonOrThrow<ExamReview>(
    await apiFetch(
      apiUrl(`/api/v1/exam-mem/practice/sessions/${encodeURIComponent(id)}`),
    ),
  );
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
