import { apiFetch, apiUrl } from "@/lib/api";

export type LearningPointStatus =
  | "unassessed"
  | "developing"
  | "weak"
  | "mastered"
  | "contested";

export interface KnowledgePointProfile {
  knowledge_point_id: string;
  name: string;
  module_id: string;
  module_name: string;
  status: LearningPointStatus;
  mastery_level: "low" | "improving" | "high" | "mastered" | null;
  mastery_score: number | null;
  confidence: number | null;
  attempts: number;
  correct_attempts: number;
  accuracy: number | null;
  latest_correct: boolean | null;
  last_practiced_at: string | null;
  error_types: string[];
  source_memory_ids: string[];
}

export interface ReviewQueueItem {
  knowledge_point_id: string;
  name: string;
  module_name: string;
  status: "due" | "upcoming" | "unassessed";
  due_at: string;
  interval_days: number;
  priority: number;
  suggested_difficulty: number;
  reason_codes: string[];
  source_memory_ids: string[];
}

export interface LearningProfile {
  context: { exam_id: string; subject_id: string };
  taxonomy_version: string;
  evaluated_at: string;
  policy_version: "learning_profile_policy_v1";
  projection_version: number | null;
  source_watermark: string | null;
  summary: {
    knowledge_point_count: number;
    assessed_count: number;
    mastered_count: number;
    weak_count: number;
    due_count: number;
    total_attempts: number;
    accuracy: number | null;
    recent_accuracy: number | null;
    coverage_rate: number;
    mastery_rate: number;
    trend: "improving" | "stable" | "declining" | "insufficient_evidence";
  };
  knowledge_points: KnowledgePointProfile[];
  review_queue: ReviewQueueItem[];
}

async function jsonOrError<T>(response: Response): Promise<T> {
  const payload = (await response.json().catch(() => ({}))) as T & {
    detail?: string | { message?: string };
  };
  if (!response.ok) {
    const detail = payload.detail;
    throw new Error(
      typeof detail === "string"
        ? detail
        : detail?.message || `Learning profile request failed (${response.status}).`,
    );
  }
  return payload;
}

export async function getLearningProfile(options: {
  examId: string;
  subjectId: string;
  taxonomyVersion: string;
}): Promise<LearningProfile> {
  const query = new URLSearchParams({
    exam_id: options.examId,
    subject_id: options.subjectId,
    taxonomy_version: options.taxonomyVersion,
  });
  return jsonOrError<LearningProfile>(
    await apiFetch(apiUrl(`/api/v1/exam-mem/learning-profile?${query}`)),
  );
}
