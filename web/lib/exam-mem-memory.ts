export type LearningMemoryNamespace = "mastery" | "error_pattern" | "plan";

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
