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
}

export interface PracticeIdentity {
  practiceSessionId: string;
  traceId: string;
}

export interface PracticeAnswerRequest {
  practice_session_id: string;
  trace_id: string;
  session_id: string;
  question_id: string;
  answer: string;
  submitted_at: string;
  idempotency_key: string;
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

export const PRACTICE_SESSION_STORAGE_KEY = "exam-mem:practice:active-session:v1";

export function createPracticeIdentity(uuid: string): PracticeIdentity {
  return {
    practiceSessionId: `practice:web:${uuid}`,
    traceId: `trace:web:${uuid}`,
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
  const payload = (await response.json()) as PracticeTurnResponse | {
    detail?: string | {
      message?: string;
      session_id?: string;
      turn_id?: string;
      practice?: PracticeResult | null;
    };
  };
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
    }),
  });
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
