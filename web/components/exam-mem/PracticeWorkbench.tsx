"use client";

import Link from "next/link";
import {
  ArrowRight,
  BookOpenCheck,
  Brain,
  CheckCircle2,
  CircleAlert,
  Database,
  History,
  LoaderCircle,
  RotateCcw,
  ShieldCheck,
} from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  clearPracticeSession,
  createPracticeIdentity,
  loadPracticeSession,
  preparePracticeAnswerRequest,
  PracticeRequestError,
  savePracticeSession,
  startExamPractice,
  submitExamPracticeAnswer,
  type PracticeAnswerRequest,
  type PracticeIdentity,
  type PracticeTurnResponse,
} from "@/lib/exam-mem-practice";
import {
  listPracticeHistory,
  resumePractice,
  type PracticeHistoryItem,
} from "@/lib/exam-mem-product";

function percent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

export default function PracticeWorkbench() {
  const { t } = useTranslation();
  const [identity, setIdentity] = useState<PracticeIdentity | null>(null);
  const [turn, setTurn] = useState<PracticeTurnResponse | null>(null);
  const [answer, setAnswer] = useState("");
  const [attemptNumber, setAttemptNumber] = useState(1);
  const [pendingRequest, setPendingRequest] =
    useState<PracticeAnswerRequest | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<PracticeHistoryItem[]>([]);

  useEffect(() => {
    const restored = loadPracticeSession(window.sessionStorage);
    if (!restored) return;
    setIdentity(restored.identity);
    setTurn(restored.turn);
    setAnswer(restored.answer);
    setAttemptNumber(restored.attemptNumber);
    setPendingRequest(restored.pendingRequest);
  }, []);

  useEffect(() => {
    void listPracticeHistory().then(setHistory).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!identity || !turn) return;
    try {
      savePracticeSession(window.sessionStorage, {
        identity,
        turn,
        answer,
        attemptNumber,
        pendingRequest,
      });
    } catch {
      // The server-side checkpoint remains the recovery source of truth.
    }
  }, [answer, attemptNumber, identity, pendingRequest, turn]);

  const begin = async () => {
    const nextIdentity = createPracticeIdentity(crypto.randomUUID());
    try {
      clearPracticeSession(window.sessionStorage);
    } catch {
      // Browser storage is an optimization, not a workflow dependency.
    }
    setLoading(true);
    setError(null);
    setTurn(null);
    setAnswer("");
    setAttemptNumber(1);
    setPendingRequest(null);
    setIdentity(nextIdentity);
    try {
      setTurn(await startExamPractice(nextIdentity));
    } catch (cause) {
      if (cause instanceof PracticeRequestError && cause.partialTurn) {
        setTurn(cause.partialTurn);
      }
      setError(cause instanceof Error ? cause.message : t("Practice could not start."));
    } finally {
      setLoading(false);
    }
  };

  const submit = async () => {
    const question = turn?.practice.question;
    if (!identity || !turn || !question || (!pendingRequest && !answer.trim())) return;
    const request = preparePracticeAnswerRequest(pendingRequest, {
      identity,
      sessionId: turn.session_id,
      questionId: question.question_id,
      answer: answer.trim(),
      submittedAt: new Date().toISOString(),
      attemptNumber,
    });
    setPendingRequest(request);
    setLoading(true);
    setError(null);
    try {
      setTurn(await submitExamPracticeAnswer(request));
      setAnswer("");
      setAttemptNumber((value) => value + 1);
      setPendingRequest(null);
    } catch (cause) {
      if (cause instanceof PracticeRequestError && cause.partialTurn) {
        setTurn(cause.partialTurn);
      }
      setError(cause instanceof Error ? cause.message : t("Answer submission failed."));
    } finally {
      setLoading(false);
    }
  };

  const resume = async (item: PracticeHistoryItem) => {
    setLoading(true);
    setError(null);
    try {
      const resumed = await resumePractice(item.practice_session_id);
      setIdentity({
        practiceSessionId: item.practice_session_id,
        traceId: item.trace_id,
      });
      setTurn(resumed);
      setAnswer("");
      setAttemptNumber(item.answer_count + 1);
      setPendingRequest(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("Practice recovery failed."));
    } finally {
      setLoading(false);
    }
  };

  const practice = turn?.practice;
  const question = practice?.question;
  const grade = practice?.grade_result;
  const diagnosis = practice?.diagnosis_result;
  const recommendation = practice?.recommendation;

  return (
    <div className="mx-auto w-full min-w-0 max-w-6xl space-y-6 px-4 py-8 sm:px-6 md:px-8 lg:px-10">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <span className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-[var(--primary)]/10 text-[var(--primary)]">
            <BookOpenCheck className="h-5 w-5" />
          </span>
          <div>
            <h1 className="font-serif text-2xl font-semibold">{t("Exam Practice")}</h1>
            <p className="mt-1 max-w-2xl text-sm text-[var(--muted-foreground)]">
              {t("Question, grading, diagnosis, Learning Memory, and an explainable next recommendation in one recoverable turn.")}
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={() => void begin()}
          disabled={loading}
          className="inline-flex items-center gap-2 rounded-lg bg-[var(--primary)] px-4 py-2 text-sm font-medium text-[var(--primary-foreground)] disabled:opacity-50"
        >
          {turn ? <RotateCcw className="h-4 w-4" /> : <ArrowRight className="h-4 w-4" />}
          {turn ? t("Start a new practice") : t("Start practice")}
        </button>
      </header>

      <section className="grid gap-3 sm:grid-cols-3">
        <Info label={t("Business store")} value={t("Independent ExamMem PostgreSQL")} />
        <Info label={t("Frozen Scope")} value="postgraduate_entrance_exam / math_1" />
        <Info label={t("Recovery")} value={t("Server checkpoint + immutable retry key")} />
      </section>

      {history.length ? (
        <section className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-4">
          <div className="flex items-center gap-2 text-sm font-semibold">
            <History className="h-4 w-4 text-[var(--primary)]" />
            {t("Practice history and server recovery")}
          </div>
          <div className="mt-3 grid gap-2 md:grid-cols-2">
            {history.slice(0, 6).map((item) => (
              <button
                key={item.practice_session_id}
                type="button"
                onClick={() => void resume(item)}
                disabled={loading}
                className="min-w-0 rounded-lg border border-[var(--border)] p-3 text-left hover:bg-[var(--muted)]/40 disabled:opacity-50"
              >
                <span className="block truncate text-sm font-medium">
                  {item.current_checkpoint.question?.stem ?? t("Practice session")}
                </span>
                <span className="mt-1 block text-xs text-[var(--muted-foreground)]">
                  {item.step_state} · {item.answer_count} {t("answers")} · {item.runtime?.backend_mode ?? t("legacy configuration")}
                </span>
              </button>
            ))}
          </div>
        </section>
      ) : null}

      <div className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-700 dark:text-amber-300">
        <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0" />
        <span>{t("Reference answers and grading rubrics remain server-side. Submitting an answer performs real Learning Memory writes; it is not a dry-run.")}</span>
      </div>

      {error ? (
        <p className="flex items-center gap-2 rounded-lg bg-red-500/10 px-3 py-2 text-sm text-red-600">
          <CircleAlert className="h-4 w-4 shrink-0" />
          {error}
        </p>
      ) : null}

      {loading ? (
        <div className="flex min-h-40 items-center justify-center rounded-xl border border-[var(--border)] bg-[var(--card)] text-sm text-[var(--muted-foreground)]">
          <LoaderCircle className="mr-2 h-5 w-5 animate-spin" />
          {t("Running the real exam_practice capability…")}
        </div>
      ) : null}

      {!loading && !turn ? (
        <section className="rounded-2xl border border-dashed border-[var(--border)] bg-[var(--card)] px-6 py-14 text-center">
          <BookOpenCheck className="mx-auto h-9 w-9 text-[var(--primary)]" />
          <h2 className="mt-4 text-lg font-semibold">{t("Ready for a scoped practice turn")}</h2>
          <p className="mx-auto mt-2 max-w-xl text-sm text-[var(--muted-foreground)]">
            {t("The controlled catalog covers linear algebra and probability with canonical math1_v1 knowledge-point IDs.")}
          </p>
        </section>
      ) : null}

      {!loading && practice ? (
        <div className="grid min-w-0 gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(280px,340px)]">
          <main className="min-w-0 space-y-5">
            {grade ? (
              <section className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-5">
                <div className="flex items-center gap-2">
                  {grade.correct ? <CheckCircle2 className="h-5 w-5 text-emerald-500" /> : <CircleAlert className="h-5 w-5 text-amber-500" />}
                  <h2 className="font-semibold">
                    {grade.correct ? t("Correct") : t("Needs review")} · {t("Score")} {grade.score}
                  </h2>
                </div>
                <p className="mt-3 text-sm text-[var(--muted-foreground)]">{grade.evidence.join(" ")}</p>
              </section>
            ) : null}
            {question ? (
              <section className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-6">
                <div className="flex flex-wrap items-center gap-2 text-xs text-[var(--muted-foreground)]">
                  <span className="rounded-full bg-[var(--primary)]/10 px-2 py-1 text-[var(--primary)]">
                    {practice.step_state === "RECOMMENDED" ? t("Recommended next") : t("Current question")}
                  </span>
                  <span>{question.knowledge_point_ids.join(", ")}</span>
                  <span>·</span>
                  <span>{t("Difficulty")} {percent(question.difficulty)}</span>
                </div>
                <h2 className="mt-4 text-lg font-medium leading-8">{question.stem}</h2>
                <label className="mt-5 block">
                  <span className="text-sm font-medium">{t("Your answer")}</span>
                  <textarea
                    value={answer}
                    onChange={(event) => setAnswer(event.target.value)}
                    disabled={pendingRequest !== null}
                    rows={6}
                    className="mt-2 w-full resize-y rounded-xl border border-[var(--border)] bg-[var(--background)] px-4 py-3 text-sm outline-none focus:border-[var(--primary)]"
                    placeholder={t("Write your reasoning and final answer…")}
                  />
                </label>
                <button
                  type="button"
                  onClick={() => void submit()}
                  disabled={(!pendingRequest && !answer.trim()) || loading}
                  className="mt-3 inline-flex items-center gap-2 rounded-lg bg-[var(--primary)] px-4 py-2 text-sm font-medium text-[var(--primary-foreground)] disabled:opacity-50"
                >
                  <ArrowRight className="h-4 w-4" />
                  {pendingRequest ? t("Retry identical submission") : t("Submit answer and update Learning Memory")}
                </button>
                {pendingRequest ? (
                  <p className="mt-2 break-all font-mono text-[11px] text-[var(--muted-foreground)]">
                    {t("Retry key")}: {pendingRequest.idempotency_key}
                  </p>
                ) : null}
              </section>
            ) : null}
          </main>

          <aside className="min-w-0 space-y-4">
            <section className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-4">
              <div className="flex items-center gap-2 text-sm font-semibold"><Database className="h-4 w-4 text-[var(--primary)]" />{t("Run identity")}</div>
              <dl className="mt-3 space-y-3 text-xs">
                <Identity label={t("State")} value={practice.step_state} />
                <Identity label={t("Trace ID")} value={practice.trace_id} />
                <Identity label={t("Practice session")} value={practice.practice_session_id} />
                <Identity label={t("DeepTutor session")} value={turn?.session_id ?? ""} />
                <Identity label={t("Pinned Backend")} value={practice.runtime?.backend_mode ?? "legacy"} />
                <Identity label={t("Config revision")} value={practice.runtime?.config_revision ?? "legacy"} />
              </dl>
            </section>
            {diagnosis ? (
              <section className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-4">
                <div className="flex items-center gap-2 text-sm font-semibold"><Brain className="h-4 w-4 text-amber-500" />{t("Diagnosis")}</div>
                <p className="mt-3 text-sm">{diagnosis.error_type || t("No supported error type")}</p>
                <p className="mt-2 text-xs leading-5 text-[var(--muted-foreground)]">{diagnosis.explanation}</p>
              </section>
            ) : null}
            {recommendation ? (
              <section className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-4">
                <h2 className="text-sm font-semibold">{t("Why this question")}</h2>
                <p className="mt-2 text-xs text-[var(--muted-foreground)]">{recommendation.reason_codes.join(", ")}</p>
                {recommendation.source_memory_ids.length ? (
                  <Link href="/exam-mem/memories" className="mt-3 inline-block text-xs text-[var(--primary)] hover:underline">{t("Inspect recommendation evidence")}</Link>
                ) : null}
              </section>
            ) : null}
            {grade && practice.grade_artifact ? (
              <section className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-4">
                <h2 className="text-sm font-semibold">{t("Grade Artifact")}</h2>
                <p className="mt-2 text-xs text-[var(--muted-foreground)]">
                  {practice.grade_artifact.reused
                    ? t("Grading computation was strictly reused; this answer still created new learning evidence.")
                    : t("This submission produced a new grading computation.")}
                </p>
              </section>
            ) : null}
          </aside>
        </div>
      ) : null}
    </div>
  );
}

function Info({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-4">
      <p className="text-xs text-[var(--muted-foreground)]">{label}</p>
      <p className="mt-1 text-sm font-semibold">{value}</p>
    </div>
  );
}

function Identity({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-[var(--muted-foreground)]">{label}</dt>
      <dd className="mt-1 break-all font-mono">{value}</dd>
    </div>
  );
}
