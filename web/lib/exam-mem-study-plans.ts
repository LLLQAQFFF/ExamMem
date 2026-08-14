import { apiFetch, apiUrl } from "@/lib/api";

export type StudyObjectiveType = "memory" | "concept" | "procedure" | "design";

export interface StudyObjective {
  id: string;
  name: string;
  type: StudyObjectiveType;
  order: number;
}

export interface StudyModule {
  id: string;
  name: string;
  order: number;
  knowledge_points: StudyObjective[];
}

export interface StudySubject {
  id: string;
  name: string;
  order: number;
  modules: StudyModule[];
}

export interface StudyPlanTree {
  name: string;
  subjects: StudySubject[];
}

export interface StudyPlanSource {
  tree: StudyPlanTree;
  source_kind: "file" | "url" | "generated";
  source_metadata: Record<string, unknown>;
  content_hash: string;
}

export interface PublishedStudyPlan extends StudyPlanSource {
  version: number;
  taxonomy_versions: Record<string, string>;
  published_at: string;
}

export interface ObjectiveSession {
  objective_id: string;
  session_id: string;
  initial_turn_id: string;
  chat_url: string;
  created: boolean;
  learning_status: string;
  learning_mastery: number;
  created_at: string;
  updated_at: string;
}

export interface StudyPlan {
  plan_id: string;
  name: string;
  active_version: number | null;
  created_at: string;
  updated_at: string;
  draft: (StudyPlanSource & { updated_at: string }) | null;
  published: PublishedStudyPlan | null;
  objective_sessions?: Record<string, ObjectiveSession>;
}

export type StudyPlanImportRequest =
  | { name: string; source_kind: "file"; filename: string; mime_type: string; base64: string }
  | { name: string; source_kind: "url"; url: string }
  | { name: string; source_kind: "generated"; request: string };

export interface AssessmentAttempt {
  attempt_id: string;
  assessment_version: number;
  practice_session_id: string;
  trace_id: string;
  status: "in_progress" | "completed" | "failed";
  started_at: string;
  completed_at: string | null;
}

export interface Assessment {
  assessment_id: string;
  title: string;
  exam_id: string;
  subject_id: string;
  taxonomy_version: string;
  knowledge_point_ids: string[];
  latest_version: number;
  attempts: AssessmentAttempt[];
}

async function jsonOrError<T>(response: Response): Promise<T> {
  const payload = (await response.json()) as T & { detail?: string | { message?: string } };
  if (!response.ok) {
    const detail = payload.detail;
    throw new Error(
      typeof detail === "string"
        ? detail
        : detail?.message || `ExamMem request failed (${response.status}).`,
    );
  }
  return payload;
}

export async function listStudyPlans(): Promise<StudyPlan[]> {
  const response = await apiFetch(apiUrl("/api/v1/exam-mem/study-plans"));
  return (await jsonOrError<{ plans: StudyPlan[] }>(response)).plans;
}

export async function getStudyPlan(planId: string): Promise<StudyPlan> {
  const response = await apiFetch(
    apiUrl(`/api/v1/exam-mem/study-plans/${encodeURIComponent(planId)}`),
  );
  return jsonOrError<StudyPlan>(response);
}

export async function importStudyPlan(body: StudyPlanImportRequest): Promise<StudyPlan> {
  const response = await apiFetch(apiUrl("/api/v1/exam-mem/study-plans/import"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return jsonOrError<StudyPlan>(response);
}

export async function saveStudyPlanDraft(
  planId: string,
  tree: StudyPlanTree,
): Promise<StudyPlan> {
  const response = await apiFetch(
    apiUrl(`/api/v1/exam-mem/study-plans/${encodeURIComponent(planId)}/draft`),
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tree }),
    },
  );
  return jsonOrError<StudyPlan>(response);
}

export async function publishStudyPlan(planId: string): Promise<StudyPlan> {
  const response = await apiFetch(
    apiUrl(`/api/v1/exam-mem/study-plans/${encodeURIComponent(planId)}/publish`),
    { method: "POST" },
  );
  return jsonOrError<StudyPlan>(response);
}

export async function openStudyObjective(
  planId: string,
  objectiveId: string,
  version: number,
  language: "zh" | "en",
): Promise<ObjectiveSession> {
  const response = await apiFetch(
    apiUrl(
      `/api/v1/exam-mem/study-plans/${encodeURIComponent(planId)}/objectives/${encodeURIComponent(objectiveId)}/open`,
    ),
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ version, language }),
    },
  );
  return jsonOrError<ObjectiveSession>(response);
}

export async function listAssessments(): Promise<Assessment[]> {
  const response = await apiFetch(apiUrl("/api/v1/exam-mem/assessments"));
  return (await jsonOrError<{ assessments: Assessment[] }>(response)).assessments;
}
