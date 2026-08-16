import { apiFetch, apiUrl } from "@/lib/api";
import {
  buildGraph,
  L3_SLOTS,
  SURFACES,
  type MemoryGraph,
  type ParsedDoc,
  type RawMemorySnapshot,
} from "@/lib/memory-graph";
import type { LearningMemoryRecord } from "@/lib/exam-mem-memory";

export interface LearningArchiveSource {
  event_id: string;
  event_type: string;
  relation_type?: string;
  session_id: string;
  knowledge_point_ids: string[];
  assessment_id: string | null;
  assessment_title: string | null;
  assessment_version: number | null;
  attempt_id: string | null;
  taxonomy_version: string | null;
}

export interface LearningArchiveEvent {
  event: {
    event_id: string;
    event_type: string;
    session_id: string;
    question_id?: string | null;
    knowledge_point_ids: string[];
    answer_correct?: boolean | null;
    error_type?: string | null;
    error_detail?: string | null;
    [key: string]: unknown;
  };
  created_at: string;
  source: Omit<LearningArchiveSource, "event_id" | "event_type" | "session_id" | "knowledge_point_ids"> | null;
}

export interface LearningArchiveMemory {
  memory: LearningMemoryRecord;
  sources: LearningArchiveSource[];
}

export interface LearningObservation {
  observation_id: string;
  exam_id: string;
  subject_id: string;
  taxonomy_version: string;
  channel: "chat" | "learning_path";
  source_session_id: string;
  source_turn_ids: string[];
  knowledge_point_ids: string[];
  summary: string;
  rationale: string;
  confidence: number;
  status: "pending" | "confirmed" | "dismissed";
  created_at: string;
}

export interface LearningArchive {
  scope: { exam_id: string; subject_id: string; taxonomy_version: string | null };
  l1: LearningArchiveEvent[];
  l2: LearningArchiveMemory[];
  l3: {
    snapshot_id: string;
    model: {
      weak_points: string[];
      mastered_points: string[];
      stable_error_patterns: string[];
      active_plans: string[];
      projection_version: number;
      source_watermark: string;
    };
  } | null;
  counts: { l1: number; l2: number; l3: number };
  learning_path_observations: LearningObservation[];
}

export interface ConversationSummary {
  session_id: string;
  title: string;
  message_count: number;
  updated_at: string;
}

export function learningArchiveKnowledgePointFilter(
  knowledgePointId: string,
  moduleKnowledgePointIds: readonly string[] | null,
): string[] | undefined {
  if (knowledgePointId) return [knowledgePointId];
  return moduleKnowledgePointIds ? [...moduleKnowledgePointIds] : undefined;
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
        : detail?.message || `Learning archive request failed (${response.status}).`,
    );
  }
  return payload;
}

function scopeQuery(options: {
  examId: string;
  subjectId: string;
  taxonomyVersion?: string;
  knowledgePointIds?: string[];
  namespaces?: string[];
  lifecycleStates?: string[];
}): URLSearchParams {
  const params = new URLSearchParams({
    exam_id: options.examId,
    subject_id: options.subjectId,
  });
  if (options.taxonomyVersion) params.set("taxonomy_version", options.taxonomyVersion);
  for (const knowledgePointId of options.knowledgePointIds ?? [])
    params.append("knowledge_point_id", knowledgePointId);
  for (const namespace of options.namespaces ?? []) params.append("memory_namespace", namespace);
  for (const state of options.lifecycleStates ?? []) params.append("lifecycle_state", state);
  return params;
}

export async function getLearningArchive(options: Parameters<typeof scopeQuery>[0]): Promise<LearningArchive> {
  const response = await apiFetch(
    apiUrl(`/api/v1/exam-mem/learning-archive?${scopeQuery(options)}`),
  );
  return jsonOrError<LearningArchive>(response);
}

export async function listConversations(): Promise<ConversationSummary[]> {
  const response = await apiFetch(
    apiUrl("/api/v1/exam-mem/learning-observations/conversations"),
  );
  return (await jsonOrError<{ conversations: ConversationSummary[] }>(response)).conversations;
}

export async function listChatObservations(options: Parameters<typeof scopeQuery>[0]): Promise<LearningObservation[]> {
  const params = scopeQuery(options);
  params.set("channel", "chat");
  const response = await apiFetch(
    apiUrl(`/api/v1/exam-mem/learning-observations?${params}`),
  );
  return (await jsonOrError<{ observations: LearningObservation[] }>(response)).observations;
}

export async function analyzeConversation(options: {
  sessionId: string;
  examId: string;
  subjectId: string;
  taxonomyVersion: string;
  language: "zh" | "en";
}): Promise<LearningObservation | null> {
  const response = await apiFetch(
    apiUrl("/api/v1/exam-mem/learning-observations/analyze-conversation"),
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: options.sessionId,
        exam_id: options.examId,
        subject_id: options.subjectId,
        taxonomy_version: options.taxonomyVersion,
        language: options.language,
      }),
    },
  );
  return (await jsonOrError<{ observation: LearningObservation | null }>(response)).observation;
}

export async function actOnObservation(
  observationId: string,
  action: "confirm" | "dismiss",
): Promise<LearningObservation> {
  const response = await apiFetch(
    apiUrl(`/api/v1/exam-mem/learning-observations/${encodeURIComponent(observationId)}/actions`),
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        action,
        idempotency_key: `observation:${action}:web:${crypto.randomUUID()}`,
      }),
    },
  );
  return (await jsonOrError<{ observation: LearningObservation }>(response)).observation;
}

export async function summarizeLearningPath(
  planId: string,
  objectiveId: string,
  version: number,
  language: "zh" | "en",
): Promise<LearningObservation> {
  const response = await apiFetch(
    apiUrl(`/api/v1/exam-mem/study-plans/${encodeURIComponent(planId)}/objectives/${encodeURIComponent(objectiveId)}/summarize`),
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ version, language }),
    },
  );
  return (await jsonOrError<{ observation: LearningObservation }>(response)).observation;
}

function parsed(entries: ParsedDoc["entries"]): ParsedDoc {
  return { title: "", entries };
}

export function buildLearningArchiveGraph(archive: LearningArchive): MemoryGraph {
  const l1 = Object.fromEntries(
    SURFACES.map((surface) => [surface, []]),
  ) as unknown as RawMemorySnapshot["l1"];
  const l2 = Object.fromEntries(
    SURFACES.map((surface) => [surface, parsed([])]),
  ) as RawMemorySnapshot["l2"];
  const l3 = Object.fromEntries(
    L3_SLOTS.map((slot) => [slot, parsed([])]),
  ) as RawMemorySnapshot["l3"];
  l1.quiz = archive.l1.map((item) => ({
    id: item.event.event_id,
    label: item.source?.assessment_title || item.event.event_type,
    ts: item.created_at,
    content: item.event.error_detail || item.event.question_id || item.event.event_type,
  }));
  const surfaceByNamespace = { mastery: "quiz", error_pattern: "notebook", plan: "book", profile: "chat", preference: "kb" } as const;
  for (const item of archive.l2) {
    const surface = surfaceByNamespace[item.memory.scope.memory_namespace as keyof typeof surfaceByNamespace] ?? "kb";
    l2[surface].entries.push({
      id: encodeURIComponent(item.memory.memory_id),
      section: item.memory.slot_key,
      text: JSON.stringify(item.memory.value),
      refs: item.sources.map((source) => `quiz:${source.event_id}`),
    });
  }
  const model = archive.l3?.model;
  if (model) {
    l3.profile.entries = model.mastered_points.map((point, index) => ({ id: `mastered_${index}`, section: "已掌握", text: point, refs: ["quiz"] }));
    l3.recent.entries = model.weak_points.map((point, index) => ({ id: `weak_${index}`, section: "薄弱点", text: point, refs: ["quiz"] }));
    l3.scope.entries = [...model.stable_error_patterns, ...model.active_plans].map((point, index) => ({ id: `scope_${index}`, section: "跨模块", text: point, refs: ["notebook", "book"] }));
  }
  const graph = buildGraph({ l1, l2, l3 });
  const labels: Record<string, string> = {
    "L1:quiz": "正式刷题证据",
    "L2:chat": "学习画像",
    "L2:quiz": "掌握度",
    "L2:notebook": "错因模式",
    "L2:book": "学习计划",
    "L2:kb": "学习偏好",
    "L3:profile": "已掌握",
    "L3:recent": "薄弱点",
    "L3:scope": "跨模块知识",
  };
  for (const cluster of graph.clusters) cluster.label = labels[cluster.id] ?? cluster.label;
  graph.clusters = graph.clusters.filter((cluster) => cluster.layer === "L3" || cluster.count > 0);
  for (const node of graph.nodes) node.href = "/exam-mem/memories";
  return graph;
}
