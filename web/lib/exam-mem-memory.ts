import { apiFetch, apiUrl } from "@/lib/api";

export type LearningMemoryNamespace =
  | "mastery"
  | "error_pattern"
  | "plan"
  | "profile"
  | "preference";

export interface LearningMemoryRecord {
  memory_id: string;
  scope: {
    exam_id: string;
    subject_id: string;
    memory_namespace: LearningMemoryNamespace;
  };
  slot_key: string;
  value: { type: string; [key: string]: unknown };
  confidence: number;
  evidence_count: number;
  lifecycle_state: string;
  version: number;
  provenance: string[];
}

export interface LearningMemorySummary {
  memory: LearningMemoryRecord;
  correction_allowed: boolean;
}

export interface LearningMemoryDetail {
  snapshot: { memory: LearningMemoryRecord; row_version: number; policy_version: string };
  version_chain: Array<{
    memory: LearningMemoryRecord;
    row_version: number;
    policy_version: string;
  }>;
  correction_allowed: boolean;
}

export interface LearningMemoryEvidence {
  memory: LearningMemoryRecord;
  events: Array<Record<string, unknown>>;
}

export const LEARNING_MEMORY_NAMESPACES: LearningMemoryNamespace[] = [
  "mastery",
  "error_pattern",
  "plan",
];

export function learningMemoryQuery(
  examId: string,
  subjectId: string,
  namespace: LearningMemoryNamespace,
  query?: string,
): string {
  const params = new URLSearchParams({
    exam_id: examId,
    subject_id: subjectId,
    memory_namespace: namespace,
  });
  if (query?.trim()) params.set("query", query.trim());
  return params.toString();
}

export function memoryValueSummary(value: LearningMemoryRecord["value"]): string {
  if (value.type === "mastery") {
    return `${String(value.level ?? "unknown")} · ${String(value.score ?? "—")}`;
  }
  if (value.type === "error_pattern") {
    return String(value.summary ?? value.error_type ?? "error pattern");
  }
  if (value.type === "plan") {
    return `${String(value.goal ?? "plan")} · ${String(value.status ?? "unknown")}`;
  }
  return JSON.stringify(value);
}

async function requireOk(response: Response): Promise<void> {
  if (response.ok) return;
  const payload = (await response.json().catch(() => ({}))) as {
    detail?: string | { message?: string };
  };
  const detail = payload.detail;
  throw new Error(
    typeof detail === "string"
      ? detail
      : detail?.message || `Learning Memory request failed (${response.status}).`,
  );
}

export async function correctLearningMemory(options: {
  memoryId: string;
  namespace: LearningMemoryNamespace;
  examId: string;
  subjectId: string;
  statement: string;
  idempotencyKey: string;
}): Promise<void> {
  const params = learningMemoryQuery(
    options.examId,
    options.subjectId,
    options.namespace,
  );
  const response = await apiFetch(
    apiUrl(`/api/v1/exam-mem/memories/${encodeURIComponent(options.memoryId)}/corrections?${params}`),
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: `correction:web:${options.idempotencyKey}`,
        idempotency_key: options.idempotencyKey,
        statement: options.statement,
        occurred_at: new Date().toISOString(),
        uncertain: true,
        confirmed: true,
      }),
    },
  );
  await requireOk(response);
}

export async function cancelLearningPlan(options: {
  memoryId: string;
  examId: string;
  subjectId: string;
  reason: string;
  idempotencyKey: string;
}): Promise<void> {
  const params = new URLSearchParams({
    exam_id: options.examId,
    subject_id: options.subjectId,
  });
  const response = await apiFetch(
    apiUrl(`/api/v1/exam-mem/plans/${encodeURIComponent(options.memoryId)}/transitions?${params}`),
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        kind: "user_cancellation",
        session_id: `plan:web:${options.idempotencyKey}`,
        idempotency_key: options.idempotencyKey,
        reason: options.reason,
        occurred_at: new Date().toISOString(),
        confirmed: true,
      }),
    },
  );
  await requireOk(response);
}
