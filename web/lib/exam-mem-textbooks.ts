import { apiFetch, apiUrl } from "@/lib/api";

export interface TextbookSection {
  section_id: string;
  parent_section_id: string | null;
  level: number;
  order: number;
  title: string;
  path: string[];
  start_page: number | null;
  end_page: number | null;
  confidence: number;
  inferred: boolean;
}

export interface TextbookJob {
  job_id: string;
  version_id: string;
  stage: "saved" | "parsing" | "structuring" | "chunking" | "indexing" | "completed" | "failed";
  progress: number;
  error_code: string | null;
  error_message: string | null;
  retry_count: number;
}

export interface TextbookVersion {
  version_id: string;
  textbook_id: string;
  version: number;
  filename: string;
  mime_type: string;
  size_bytes: number;
  status: "queued" | "processing" | "completed" | "failed";
  host_index_ref: string | null;
  index_version: string | null;
  sections?: TextbookSection[];
  job?: TextbookJob | null;
  created_at: string;
}

export interface Textbook {
  textbook_id: string;
  title: string;
  metadata: Record<string, unknown>;
  archived_at: string | null;
  updated_at: string;
  versions: TextbookVersion[];
}

export interface TextbookBinding {
  binding_id: string;
  textbook_version_id: string;
  revision: number;
  role: "primary" | "supplement" | "reference";
  priority: number;
  status: "candidate" | "confirmed" | "inactive";
}

export interface TextbookMapping {
  mapping_id: string;
  objective_id: string;
  textbook_section_id: string;
  mapping_version: number;
  confidence: number;
  created_via: "manual" | "recommended";
  status: "candidate" | "confirmed" | "rejected";
}

async function jsonOrError<T>(response: Response): Promise<T> {
  const payload = (await response.json()) as T & { detail?: string | { message?: string } };
  if (!response.ok) {
    throw new Error(typeof payload.detail === "string" ? payload.detail : payload.detail?.message || `Textbook request failed (${response.status}).`);
  }
  return payload;
}

export async function listTextbooks(archival: "active" | "archived" | "all" = "active"): Promise<Textbook[]> {
  const response = await apiFetch(apiUrl(`/api/v1/exam-mem/textbooks?${new URLSearchParams({ archival })}`));
  return (await jsonOrError<{ textbooks: Textbook[] }>(response)).textbooks;
}

export async function getTextbook(textbookId: string): Promise<Textbook> {
  return jsonOrError<Textbook>(await apiFetch(apiUrl(`/api/v1/exam-mem/textbooks/${encodeURIComponent(textbookId)}`)));
}

export async function getTextbookVersion(textbookId: string, versionId: string): Promise<TextbookVersion> {
  return jsonOrError<TextbookVersion>(await apiFetch(apiUrl(`/api/v1/exam-mem/textbooks/${encodeURIComponent(textbookId)}/versions/${encodeURIComponent(versionId)}`)));
}

export async function uploadTextbook(body: { title: string; filename: string; mime_type: string; base64: string; idempotency_key: string }, textbookId?: string): Promise<TextbookVersion> {
  const path = textbookId ? `/api/v1/exam-mem/textbooks/${encodeURIComponent(textbookId)}/versions` : "/api/v1/exam-mem/textbooks";
  const response = await apiFetch(apiUrl(path), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...body, metadata: {} }) });
  return (await jsonOrError<{ version: TextbookVersion }>(response)).version;
}

export async function retryTextbookJob(jobId: string): Promise<void> {
  await jsonOrError(await apiFetch(apiUrl(`/api/v1/exam-mem/textbooks/ingestions/${encodeURIComponent(jobId)}/retry`), { method: "POST" }));
}

export async function archiveTextbook(textbookId: string): Promise<void> {
  await jsonOrError(await apiFetch(apiUrl(`/api/v1/exam-mem/textbooks/${encodeURIComponent(textbookId)}/archive`), { method: "POST" }));
}

export async function listTextbookBindings(planId: string, version: number): Promise<TextbookBinding[]> {
  const response = await apiFetch(apiUrl(`/api/v1/exam-mem/study-plans/${encodeURIComponent(planId)}/versions/${version}/textbooks`));
  return (await jsonOrError<{ bindings: TextbookBinding[] }>(response)).bindings;
}

export async function setTextbookBinding(planId: string, version: number, body: Omit<TextbookBinding, "binding_id" | "revision">): Promise<TextbookBinding> {
  const response = await apiFetch(apiUrl(`/api/v1/exam-mem/study-plans/${encodeURIComponent(planId)}/versions/${version}/textbooks`), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...body, idempotency_key: crypto.randomUUID() }) });
  return (await jsonOrError<{ binding: TextbookBinding }>(response)).binding;
}

export async function listTextbookMappings(planId: string, version: number): Promise<TextbookMapping[]> {
  const response = await apiFetch(apiUrl(`/api/v1/exam-mem/study-plans/${encodeURIComponent(planId)}/versions/${version}/textbook-mappings`));
  return (await jsonOrError<{ mappings: TextbookMapping[] }>(response)).mappings;
}

export async function setTextbookMapping(planId: string, version: number, body: Omit<TextbookMapping, "mapping_id" | "mapping_version">): Promise<TextbookMapping> {
  const response = await apiFetch(apiUrl(`/api/v1/exam-mem/study-plans/${encodeURIComponent(planId)}/versions/${version}/textbook-mappings`), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...body, idempotency_key: crypto.randomUUID() }) });
  return (await jsonOrError<{ mapping: TextbookMapping }>(response)).mapping;
}
