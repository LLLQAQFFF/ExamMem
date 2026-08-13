"use client";

import { CircleAlert, FileCheck2, LoaderCircle, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  disputeGrade,
  getExamReview,
  listPracticeHistory,
  type ExamReview,
  type PracticeHistoryItem,
  upholdGrade,
} from "@/lib/exam-mem-product";

export default function ExamReviewWorkbench() {
  const { t } = useTranslation();
  const [history, setHistory] = useState<PracticeHistoryItem[]>([]);
  const [review, setReview] = useState<ExamReview | null>(null);
  const [reason, setReason] = useState("");
  const [pendingKey, setPendingKey] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const sessions = await listPracticeHistory();
      setHistory(sessions);
      if (sessions.length) {
        setReview(await getExamReview(sessions[0].practice_session_id));
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("Exam Review failed to load."));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => void load(), [load]);

  const open = async (item: PracticeHistoryItem) => {
    setLoading(true);
    try {
      setReview(await getExamReview(item.practice_session_id));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("Exam Review failed to load."));
    } finally {
      setLoading(false);
    }
  };

  const graded = review?.checkpoints.find((item) => item.grade_result !== null) ?? null;
  const submitDispute = async () => {
    if (!review || !graded || (!pendingKey && !reason.trim())) return;
    const idempotencyKey = pendingKey ?? `grade-review:web:${crypto.randomUUID()}`;
    setPendingKey(idempotencyKey);
    setLoading(true);
    try {
      await disputeGrade({
        practiceSessionId: review.practice_session_id,
        checkpointKey: graded.checkpoint_key,
        reason: reason.trim(),
        idempotencyKey,
      });
      setReview(await getExamReview(review.practice_session_id));
      setReason("");
      setPendingKey(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("Grade dispute failed."));
    } finally {
      setLoading(false);
    }
  };
  const openDispute = review?.grade_reviews.find((item) => item.action === "dispute");
  const resolvedReview = review?.grade_reviews.some(
    (item) => item.action === "uphold" || item.action === "overturn",
  );
  const uphold = async () => {
    if (!review || !openDispute || resolvedReview) return;
    const key = pendingKey ?? `grade-uphold:web:${crypto.randomUUID()}`;
    setPendingKey(key);
    setLoading(true);
    try {
      await upholdGrade({
        reviewChainId: openDispute.review_chain_id,
        practiceSessionId: review.practice_session_id,
        checkpointKey: openDispute.checkpoint_key,
        reason: "Administrator confirmed the original Grade and evidence.",
        idempotencyKey: key,
      });
      setReview(await getExamReview(review.practice_session_id));
      setPendingKey(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("Grade Review disposition failed."));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="mx-auto w-full max-w-7xl space-y-6 px-4 py-8 sm:px-6 lg:px-10">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex gap-3">
          <span className="grid h-11 w-11 place-items-center rounded-xl bg-[var(--primary)]/10 text-[var(--primary)]"><FileCheck2 className="h-5 w-5" /></span>
          <div><h1 className="font-serif text-2xl font-semibold">{t("Exam Review")}</h1><p className="mt-1 text-sm text-[var(--muted-foreground)]">{t("Question, Grade, Trace, Learning Events, lifecycle decisions, and recommendations in one audit chain.")}</p></div>
        </div>
        <button type="button" onClick={() => void load()} className="inline-flex items-center gap-2 rounded-lg border border-[var(--border)] px-3 py-2 text-sm"><RefreshCw className="h-4 w-4" />{t("Refresh")}</button>
      </header>
      {error ? <p className="flex items-center gap-2 rounded-lg bg-red-500/10 p-3 text-sm text-red-600"><CircleAlert className="h-4 w-4" />{error}</p> : null}
      <div className="grid min-w-0 gap-5 lg:grid-cols-[280px_minmax(0,1fr)]">
        <aside className="min-w-0 space-y-2">
          {history.map((item) => <button key={item.practice_session_id} type="button" onClick={() => void open(item)} className="w-full min-w-0 rounded-xl border border-[var(--border)] bg-[var(--card)] p-3 text-left"><span className="block truncate text-sm font-medium">{item.current_checkpoint.question?.stem ?? item.practice_session_id}</span><span className="mt-1 block text-xs text-[var(--muted-foreground)]">{item.step_state} · {item.answer_count} {t("answers")}</span></button>)}
        </aside>
        <main className="min-w-0 space-y-4">
          {loading ? <p className="flex items-center justify-center rounded-xl border p-12 text-sm text-[var(--muted-foreground)]"><LoaderCircle className="mr-2 h-5 w-5 animate-spin" />{t("Loading audit chain…")}</p> : null}
          {!loading && !review ? <p className="rounded-xl border border-dashed p-10 text-center text-sm text-[var(--muted-foreground)]">{t("No practice history is available.")}</p> : null}
          {review ? <>
            <section className="grid gap-3 sm:grid-cols-3"><Fact label={t("State")} value={review.step_state} /><Fact label={t("Pinned Backend")} value={review.runtime?.backend_mode ?? "legacy"} /><Fact label={t("Trace spans")} value={String(review.trace.length)} /></section>
            {review.checkpoints.map((checkpoint) => <section key={checkpoint.checkpoint_key} className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-5"><p className="break-all font-mono text-xs text-[var(--muted-foreground)]">{checkpoint.checkpoint_key}</p><h2 className="mt-2 font-semibold">{checkpoint.question?.stem ?? checkpoint.step_state}</h2>{checkpoint.grade_result ? <p className="mt-3 text-sm">{t("Grade")}: {checkpoint.grade_result.score} · {checkpoint.grade_result.evidence.join(" ")}</p> : null}{checkpoint.diagnosis_result ? <p className="mt-2 text-sm text-[var(--muted-foreground)]">{checkpoint.diagnosis_result.error_type}: {checkpoint.diagnosis_result.explanation}</p> : null}{checkpoint.grade_artifact ? <p className="mt-2 text-xs text-[var(--muted-foreground)]">{checkpoint.grade_artifact.reused ? t("Grade Artifact reused; evidence remained new.") : t("New Grade Artifact")}</p> : null}</section>)}
            {graded ? <section className="rounded-xl border border-amber-500/30 bg-amber-500/5 p-5"><h2 className="font-semibold">{t("Dispute this Grade")}</h2><p className="mt-1 text-xs text-[var(--muted-foreground)]">{t("This creates an append-only Grade Review event. It does not edit Learning Memory.")}</p><textarea rows={3} value={reason} onChange={(event) => setReason(event.target.value)} disabled={pendingKey !== null} className="mt-3 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] p-3 text-sm" /><button type="button" onClick={() => void submitDispute()} disabled={loading || (!pendingKey && !reason.trim())} className="mt-2 rounded-lg bg-[var(--primary)] px-4 py-2 text-sm text-[var(--primary-foreground)] disabled:opacity-50">{pendingKey ? t("Retry identical dispute") : t("Submit Grade dispute")}</button></section> : null}
            {openDispute && !resolvedReview ? <section className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-5"><h2 className="font-semibold">{t("Administrator Grade Review")}</h2><p className="mt-2 text-sm text-[var(--muted-foreground)]">{openDispute.reason}</p><button type="button" onClick={() => void uphold()} disabled={loading} className="mt-3 rounded-lg border border-[var(--border)] px-4 py-2 text-sm disabled:opacity-50">{pendingKey ? t("Retry identical disposition") : t("Uphold original Grade")}</button><p className="mt-2 text-xs text-[var(--muted-foreground)]">{t("Overturn requires complete replacement Grade evidence and remains available through the administrator API.")}</p></section> : null}
            <section className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-5"><h2 className="font-semibold">{t("Trace and lifecycle audit")}</h2><pre className="mt-3 max-h-96 overflow-auto rounded-lg bg-[var(--muted)]/40 p-3 text-xs">{JSON.stringify({ trace: review.trace, lifecycle: review.lifecycle, grade_reviews: review.grade_reviews }, null, 2)}</pre></section>
          </> : null}
        </main>
      </div>
    </div>
  );
}

function Fact({ label, value }: { label: string; value: string }) { return <div className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-4"><p className="text-xs text-[var(--muted-foreground)]">{label}</p><p className="mt-1 break-all text-sm font-semibold">{value}</p></div>; }
