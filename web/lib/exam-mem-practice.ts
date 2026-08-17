import { apiFetch, apiUrl } from "@/lib/api";

export interface PracticeQuestion {
  question_id: string;
  stem: string;
  knowledge_point_ids: string[];
  difficulty: number;
}

export interface PracticeResult {
  practice_session_id: string;
  trace_id: string;
  scope: {
    exam_id: string;
    subject_id: string;
    memory_namespace: string;
  };
  step_state: string;
  question: PracticeQuestion | null;
  grade_result: {
    correct: boolean;
    score: number;
    matched_rubric_items: string[];
    missed_rubric_items: string[];
    evidence: string[];
  } | null;
  diagnosis_result: {
    error_type: string | null;
    explanation: string;
    confidence: number;
  } | null;
  recommendation: {
    reason_codes: string[];
    source_memory_ids: string[];
    policy_version: string;
  } | null;
  resumed_from_state: string;
  replayed: boolean;
  answered_question_count?: number;
  question_count?: number;
  completed?: boolean;
  runtime?: {
    config_revision: string;
    backend_mode: string;
    side_effects: string[];
  } | null;
  grade_artifact?: {
    reused: boolean;
    source_checkpoint: string | null;
    identity: Record<string, string>;
  } | null;
}

export interface PracticeTurnResponse {
  session_id: string;
  turn_id: string;
  response: string;
  practice: PracticeResult;
  assessment?: {
    assessment_id: string;
    version: number;
    attempt_id: string;
  };
}

export interface PracticeIdentity {
  practiceSessionId: string;
  traceId: string;
  examId: string;
  subjectId: string;
}

export interface PracticeAnswerRequest {
  practice_session_id: string;
  trace_id: string;
  session_id: string;
  question_id: string;
  answer: string;
  submitted_at: string;
  idempotency_key: string;
  exam_id: string;
  subject_id: string;
}

export interface ExamScopeOption {
  exam_id: string;
  exam_name: string;
  subject_id: string;
  subject_name: string;
  taxonomy_version: string;
}

export interface ExamKnowledgePointOption {
  id: string;
  name: string;
  aliases: string[];
}

export interface ExamMemCatalog {
  scopes: ExamScopeOption[];
  knowledge_points: ExamKnowledgePointOption[];
}

export interface GeneratedPracticeOptions {
  identity: PracticeIdentity;
  learningPathId: string;
  knowledgePointId: string;
  knowledgePointName: string;
  taxonomyVersion: string;
  numQuestions: number;
  difficulty: "auto" | "easy" | "medium" | "hard";
  language: "zh" | "en";
  attachments: Array<{
    type: "file" | "pdf";
    filename: string;
    mime_type: string;
    base64: string;
  }>;
  assessmentId?: string;
  assessmentTitle?: string;
}

export type PracticeGenerationStage =
  | "scope"
  | "exploring"
  | "planning"
  | "generating"
  | "persisting"
  | "starting";

export interface PracticeGenerationProgress {
  stage: PracticeGenerationStage;
  completed_questions: number;
  total_questions: number;
}

export interface PracticeSessionSnapshot {
  identity: PracticeIdentity;
  turn: PracticeTurnResponse;
  answer: string;
  attemptNumber: number;
  pendingRequest: PracticeAnswerRequest | null;
}

export class PracticeRequestError extends Error {
  constructor(
    message: string,
    readonly partialTurn: PracticeTurnResponse | null,
  ) {
    super(message);
    this.name = "PracticeRequestError";
  }
}

interface PracticeErrorDetail {
  message?: string;
  session_id?: string;
  turn_id?: string;
  practice?: PracticeResult | null;
}

export const PRACTICE_SESSION_STORAGE_KEY = "exam-mem:practice:active-session:v1";

export function createPracticeIdentity(
  uuid: string,
  examId = "postgraduate_entrance_exam",
  subjectId = "math_1",
): PracticeIdentity {
  return {
    practiceSessionId: `practice:web:${uuid}`,
    traceId: `trace:web:${uuid}`,
    examId,
    subjectId,
  };
}

export function buildPracticeAnswerRequest(options: {
  identity: PracticeIdentity;
  sessionId: string;
  questionId: string;
  answer: string;
  submittedAt: string;
  attemptNumber: number;
}): PracticeAnswerRequest {
  return {
    practice_session_id: options.identity.practiceSessionId,
    trace_id: options.identity.traceId,
    session_id: options.sessionId,
    question_id: options.questionId,
    answer: options.answer,
    submitted_at: options.submittedAt,
    idempotency_key: `answer:web:${options.identity.practiceSessionId}:${options.attemptNumber}`,
    exam_id: options.identity.examId,
    subject_id: options.identity.subjectId,
  };
}

export function preparePracticeAnswerRequest(
  pending: PracticeAnswerRequest | null,
  next: Parameters<typeof buildPracticeAnswerRequest>[0],
): PracticeAnswerRequest {
  return pending ?? buildPracticeAnswerRequest(next);
}

export function loadPracticeSession(
  storage: Pick<Storage, "getItem">,
): PracticeSessionSnapshot | null {
  try {
    const raw = storage.getItem(PRACTICE_SESSION_STORAGE_KEY);
    if (!raw) return null;
    const value = JSON.parse(raw) as PracticeSessionSnapshot;
    if (
      !value.identity?.practiceSessionId ||
      !value.identity?.traceId ||
      !value.turn?.session_id ||
      !value.turn?.practice?.practice_session_id ||
      value.attemptNumber < 1
    ) {
      return null;
    }
    value.identity.examId ||= "postgraduate_entrance_exam";
    value.identity.subjectId ||= "math_1";
    return value;
  } catch {
    return null;
  }
}

export function savePracticeSession(
  storage: Pick<Storage, "setItem">,
  value: PracticeSessionSnapshot,
): void {
  storage.setItem(PRACTICE_SESSION_STORAGE_KEY, JSON.stringify(value));
}

export function clearPracticeSession(storage: Pick<Storage, "removeItem">): void {
  storage.removeItem(PRACTICE_SESSION_STORAGE_KEY);
}

async function parsePracticeResponse(response: Response): Promise<PracticeTurnResponse> {
  const rawPayload = await response.text();
  let payload: PracticeTurnResponse | {
    detail?: string | PracticeErrorDetail;
  };
  try {
    payload = JSON.parse(rawPayload) as typeof payload;
  } catch {
    const fallback = rawPayload.trim();
    throw new PracticeRequestError(
      fallback || `Practice request failed (${response.status}).`,
      null,
    );
  }
  if (!response.ok) {
    const detail = "detail" in payload ? payload.detail : undefined;
    const message =
      typeof detail === "string"
        ? detail
        : detail?.message || `Practice request failed (${response.status}).`;
    const partialTurn =
      typeof detail === "object" && detail?.session_id && detail.turn_id && detail.practice
        ? {
            session_id: detail.session_id,
            turn_id: detail.turn_id,
            response: "",
            practice: detail.practice,
          }
        : null;
    throw new PracticeRequestError(message, partialTurn);
  }
  return payload as PracticeTurnResponse;
}

export async function startExamPractice(
  identity: PracticeIdentity,
): Promise<PracticeTurnResponse> {
  const response = await apiFetch(apiUrl("/api/v1/exam-mem/practice/start"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      practice_session_id: identity.practiceSessionId,
      trace_id: identity.traceId,
      exam_id: identity.examId,
      subject_id: identity.subjectId,
    }),
  });
  return parsePracticeResponse(response);
}

export async function getExamMemCatalog(): Promise<ExamMemCatalog> {
  const response = await apiFetch(apiUrl("/api/v1/exam-mem/catalog"));
  if (!response.ok) throw new Error(`Catalog request failed (${response.status}).`);
  return response.json() as Promise<ExamMemCatalog>;
}

export async function generateExamPractice(
  options: GeneratedPracticeOptions,
  onProgress?: (progress: PracticeGenerationProgress) => void,
): Promise<PracticeTurnResponse> {
  const response = await apiFetch(apiUrl("/api/v1/exam-mem/practice/generate/stream"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      practice_session_id: options.identity.practiceSessionId,
      trace_id: options.identity.traceId,
      exam_id: options.identity.examId,
      subject_id: options.identity.subjectId,
      learning_path_id: options.learningPathId,
      knowledge_point_id: options.knowledgePointId,
      knowledge_point_name: options.knowledgePointName,
      taxonomy_version: options.taxonomyVersion,
      num_questions: options.numQuestions,
      difficulty: options.difficulty,
      language: options.language,
      attachments: options.attachments,
      assessment_id: options.assessmentId,
      assessment_title: options.assessmentTitle,
    }),
  });
  if (!response.ok || !response.body) return parsePracticeResponse(response);

  type StreamLine =
    | ({ type: "progress" } & PracticeGenerationProgress)
    | { type: "complete"; result: PracticeTurnResponse }
    | { type: "error"; status: number; detail: string | PracticeErrorDetail };

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result: PracticeTurnResponse | null = null;

  const consume = (rawLine: string) => {
    const line = JSON.parse(rawLine) as StreamLine;
    if (line.type === "progress") {
      onProgress?.(line);
      return;
    }
    if (line.type === "error") {
      const message =
        typeof line.detail === "string"
          ? line.detail
          : line.detail.message || `Practice request failed (${line.status}).`;
      const partialTurn =
        typeof line.detail === "object" &&
        line.detail.session_id &&
        line.detail.turn_id &&
        line.detail.practice
          ? {
              session_id: line.detail.session_id,
              turn_id: line.detail.turn_id,
              response: "",
              practice: line.detail.practice,
            }
          : null;
      throw new PracticeRequestError(message, partialTurn);
    }
    result = line.result;
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let newline = buffer.indexOf("\n");
    while (newline >= 0) {
      const line = buffer.slice(0, newline).trim();
      buffer = buffer.slice(newline + 1);
      if (line) consume(line);
      newline = buffer.indexOf("\n");
    }
  }
  buffer += decoder.decode();
  const tail = buffer.trim();
  if (tail) consume(tail);
  if (result === null) {
    throw new PracticeRequestError("Practice generation stream ended without a result.", null);
  }
  return result;
}

export async function repeatAssessmentVersion(options: {
  assessmentId: string;
  version: number;
  identity: PracticeIdentity;
}): Promise<PracticeTurnResponse> {
  const response = await apiFetch(
    apiUrl(
      `/api/v1/exam-mem/assessments/${encodeURIComponent(options.assessmentId)}/versions/${options.version}/attempts`,
    ),
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        practice_session_id: options.identity.practiceSessionId,
        trace_id: options.identity.traceId,
      }),
    },
  );
  return parsePracticeResponse(response);
}

export async function submitExamPracticeAnswer(
  request: PracticeAnswerRequest,
): Promise<PracticeTurnResponse> {
  const response = await apiFetch(apiUrl("/api/v1/exam-mem/practice/answer"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  return parsePracticeResponse(response);
}
