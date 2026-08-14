"use client";

import {
  ChevronRight,
  CircleAlert,
  FileCheck2,
  Filter,
  Layers3,
  LoaderCircle,
  RefreshCw,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  disputeGrade,
  getExamReview,
  groupExamReviewHistory,
  listExamReviewHistory,
  type ExamReview,
  type ExamReviewGroup,
  type ExamReviewHistoryItem,
  upholdGrade,
} from "@/lib/exam-mem-product";
import { listAssessments, listStudyPlans } from "@/lib/exam-mem-study-plans";

type StatusFilter = "all" | "completed" | "in_progress" | "failed";

export default function ExamReviewWorkbench() {
  const { t, i18n } = useTranslation();
  const [history, setHistory] = useState<ExamReviewHistoryItem[]>([]);
  const [selectedExamKey, setSelectedExamKey] = useState<string | null>(null);
  const [review, setReview] = useState<ExamReview | null>(null);
  const [scopeFilter, setScopeFilter] = useState("all");
  const [scopeLabels, setScopeLabels] = useState<Record<string, string>>({});
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [query, setQuery] = useState("");
  const [reason, setReason] = useState("");
  const [pendingKey, setPendingKey] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const groups = useMemo(() => groupExamReviewHistory(history), [history]);
  const scopeOptions = useMemo(
    () =>
      [...new Map(groups.map((group) => [scopeKey(group), group])).values()].map(
        (group) => ({
          key: scopeKey(group),
          label: scopeLabels[scopeKey(group)] ?? group.subject_id,
        }),
      ),
    [groups, scopeLabels],
  );
  const filteredGroups = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase(i18n.language);
    return groups.filter((group) => {
      const matchesScope = scopeFilter === "all" || scopeKey(group) === scopeFilter;
      const matchesStatus =
        statusFilter === "all" ||
        group.attempts.some((attempt) => attempt.attempt_status === statusFilter);
      const matchesQuery =
        !normalizedQuery ||
        `${group.title} ${group.exam_id} ${group.subject_id}`
          .toLocaleLowerCase(i18n.language)
          .includes(normalizedQuery);
      return matchesScope && matchesStatus && matchesQuery;
    });
  }, [groups, i18n.language, query, scopeFilter, statusFilter]);
  const selectedGroup = groups.find((group) => group.key === selectedExamKey) ?? null;

  const openAttempt = useCallback(
    async (item: ExamReviewHistoryItem) => {
      setLoading(true);
      setError(null);
      try {
        setReview(
          await getExamReview(item.practice_session_id, item.exam_id, item.subject_id),
        );
      } catch (cause) {
        setError(
          cause instanceof Error ? cause.message : t("Exam Review failed to load."),
        );
      } finally {
        setLoading(false);
      }
    },
    [t],
  );

  const selectExam = useCallback(
    (group: ExamReviewGroup) => {
      setSelectedExamKey(group.key);
      const latestCompleted = group.attempts.find(
        (attempt) => attempt.attempt_status === "completed",
      );
      const attempt = latestCompleted ?? group.attempts[0];
      if (attempt) void openAttempt(attempt);
    },
    [openAttempt],
  );

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [assessments, plans] = await Promise.all([
        listAssessments(),
        listStudyPlans(),
      ]);
      const sessions = await listExamReviewHistory(assessments);
      const loadedGroups = groupExamReviewHistory(sessions);
      const labels: Record<string, string> = {
        [`postgraduate_entrance_exam\u001fmath_1`]: t("Built-in Mathematics I"),
      };
      for (const plan of plans) {
        for (const subject of plan.published?.tree.subjects ?? []) {
          labels[`plan:${plan.plan_id}\u001f${subject.id}`] = `${plan.name} / ${subject.name}`;
        }
      }
      setHistory(sessions);
      setScopeLabels(labels);
      if (loadedGroups.length) {
        const first = loadedGroups[0];
        setSelectedExamKey(first.key);
        const firstAttempt =
          first.attempts.find((attempt) => attempt.attempt_status === "completed") ??
          first.attempts[0];
        if (firstAttempt) {
          setReview(
            await getExamReview(
              firstAttempt.practice_session_id,
              firstAttempt.exam_id,
              firstAttempt.subject_id,
            ),
          );
        }
      } else {
        setSelectedExamKey(null);
        setReview(null);
      }
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : t("Exam Review failed to load."),
      );
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => void load(), [load]);
  useEffect(() => {
    if (!history.length) return;
    if (!filteredGroups.length) {
      setSelectedExamKey(null);
      setReview(null);
      return;
    }
    if (!filteredGroups.some((group) => group.key === selectedExamKey)) {
      selectExam(filteredGroups[0]);
    }
  }, [filteredGroups, history.length, selectExam, selectedExamKey]);

  const graded =
    review?.checkpoints.find((item) => item.grade_result !== null) ?? null;
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
        examId: review.exam_id,
        subjectId: review.subject_id,
      });
      setReview(
        await getExamReview(
          review.practice_session_id,
          review.exam_id,
          review.subject_id,
        ),
      );
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
        examId: review.exam_id,
        subjectId: review.subject_id,
      });
      setReview(
        await getExamReview(
          review.practice_session_id,
          review.exam_id,
          review.subject_id,
        ),
      );
      setPendingKey(null);
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : t("Grade Review disposition failed."),
      );
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="mx-auto w-full max-w-7xl space-y-6 px-4 py-8 sm:px-6 lg:px-10">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex gap-3">
          <span className="grid h-11 w-11 place-items-center rounded-xl bg-[var(--primary)]/10 text-[var(--primary)]">
            <FileCheck2 className="h-5 w-5" />
          </span>
          <div>
            <h1 className="font-serif text-2xl font-semibold">{t("Exam Review")}</h1>
            <p className="mt-1 text-sm text-[var(--muted-foreground)]">
              {t(
                "Browse exams by category, compare scores across versions, then inspect one attempt's complete audit chain.",
              )}
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          className="inline-flex items-center gap-2 rounded-lg border border-[var(--border)] px-3 py-2 text-sm"
        >
          <RefreshCw className="h-4 w-4" />
          {t("Refresh")}
        </button>
      </header>

      {error ? (
        <p className="flex items-center gap-2 rounded-lg bg-red-500/10 p-3 text-sm text-red-600">
          <CircleAlert className="h-4 w-4" />
          {error}
        </p>
      ) : null}

      <section className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-4">
        <div className="mb-3 flex items-center gap-2 text-sm font-semibold">
          <Filter className="h-4 w-4 text-[var(--primary)]" />
          {t("Exam categories and filters")}
        </div>
        <div className="grid gap-3 md:grid-cols-3">
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t("Search exams")}
            className="rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-sm"
          />
          <select
            value={scopeFilter}
            onChange={(event) => setScopeFilter(event.target.value)}
            className="rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-sm"
          >
            <option value="all">{t("All subjects")}</option>
            {scopeOptions.map((option) => (
              <option key={option.key} value={option.key}>
                {option.label}
              </option>
            ))}
          </select>
          <select
            value={statusFilter}
            onChange={(event) => setStatusFilter(event.target.value as StatusFilter)}
            className="rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-sm"
          >
            <option value="all">{t("All statuses")}</option>
            <option value="completed">{t("Completed")}</option>
            <option value="in_progress">{t("In progress")}</option>
            <option value="failed">{t("Failed")}</option>
          </select>
        </div>
      </section>

      <div className="grid min-w-0 gap-5 lg:grid-cols-[300px_minmax(0,1fr)]">
        <aside className="min-w-0 space-y-2">
          {filteredGroups.map((group) => (
            <button
              key={group.key}
              type="button"
              onClick={() => selectExam(group)}
              className={`w-full min-w-0 rounded-xl border p-3 text-left ${
                selectedExamKey === group.key
                  ? "border-[var(--primary)] bg-[var(--primary)]/5"
                  : "border-[var(--border)] bg-[var(--card)]"
              }`}
            >
              <span className="flex items-center justify-between gap-2">
                <span className="block truncate text-sm font-medium">
                  {group.title === "Legacy practice" ? t("Legacy practice") : group.title}
                </span>
                <ChevronRight className="h-4 w-4 shrink-0" />
              </span>
              <span className="mt-1 block text-xs text-[var(--muted-foreground)]">
                {group.versions.length} {t("versions")} · {group.attempts.length}{" "}
                {t("attempts")}
              </span>
            </button>
          ))}
          {!loading && !filteredGroups.length ? (
            <p className="rounded-xl border border-dashed p-6 text-center text-sm text-[var(--muted-foreground)]">
              {t("No exams match the current filters.")}
            </p>
          ) : null}
        </aside>

        <main className="min-w-0 space-y-4">
          {selectedGroup ? (
            <VersionScores
              group={selectedGroup}
              selectedSessionId={review?.practice_session_id ?? null}
              locale={i18n.language}
              scopeLabel={scopeLabels[scopeKey(selectedGroup)]}
              onOpen={openAttempt}
            />
          ) : null}

          {loading ? (
            <p className="flex items-center justify-center rounded-xl border p-12 text-sm text-[var(--muted-foreground)]">
              <LoaderCircle className="mr-2 h-5 w-5 animate-spin" />
              {t("Loading audit chain…")}
            </p>
          ) : null}
          {!loading && !review ? (
            <p className="rounded-xl border border-dashed p-10 text-center text-sm text-[var(--muted-foreground)]">
              {t("Select an exam attempt to inspect its audit chain.")}
            </p>
          ) : null}
          {review ? (
            <>
              <section className="grid gap-3 sm:grid-cols-4">
                <Fact label={t("State")} value={review.step_state} />
                <Fact label={t("Score")} value={formatScore(review.score)} />
                <Fact
                  label={t("Correct answers")}
                  value={`${review.correct_count ?? 0}/${review.answer_count}`}
                />
                <Fact
                  label={t("Pinned Backend")}
                  value={review.runtime?.backend_mode ?? "legacy"}
                />
              </section>
              {review.checkpoints.map((checkpoint) => (
                <section
                  key={checkpoint.checkpoint_key}
                  className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-5"
                >
                  <p className="break-all font-mono text-xs text-[var(--muted-foreground)]">
                    {checkpoint.checkpoint_key}
                  </p>
                  <h2 className="mt-2 font-semibold">
                    {checkpoint.question?.stem ?? checkpoint.step_state}
                  </h2>
                  {checkpoint.grade_result ? (
                    <p className="mt-3 text-sm">
                      {t("Grade")}: {formatScore(checkpoint.grade_result.score)} ·{" "}
                      {checkpoint.grade_result.evidence.join(" ")}
                    </p>
                  ) : null}
                  {checkpoint.diagnosis_result ? (
                    <p className="mt-2 text-sm text-[var(--muted-foreground)]">
                      {checkpoint.diagnosis_result.error_type}:{" "}
                      {checkpoint.diagnosis_result.explanation}
                    </p>
                  ) : null}
                  {checkpoint.grade_artifact ? (
                    <p className="mt-2 text-xs text-[var(--muted-foreground)]">
                      {checkpoint.grade_artifact.reused
                        ? t("Grade Artifact reused; evidence remained new.")
                        : t("New Grade Artifact")}
                    </p>
                  ) : null}
                </section>
              ))}
              {graded ? (
                <section className="rounded-xl border border-amber-500/30 bg-amber-500/5 p-5">
                  <h2 className="font-semibold">{t("Dispute this Grade")}</h2>
                  <p className="mt-1 text-xs text-[var(--muted-foreground)]">
                    {t(
                      "This creates an append-only Grade Review event. It does not edit Learning Memory.",
                    )}
                  </p>
                  <textarea
                    rows={3}
                    value={reason}
                    onChange={(event) => setReason(event.target.value)}
                    disabled={pendingKey !== null}
                    className="mt-3 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] p-3 text-sm"
                  />
                  <button
                    type="button"
                    onClick={() => void submitDispute()}
                    disabled={loading || (!pendingKey && !reason.trim())}
                    className="mt-2 rounded-lg bg-[var(--primary)] px-4 py-2 text-sm text-[var(--primary-foreground)] disabled:opacity-50"
                  >
                    {pendingKey ? t("Retry identical dispute") : t("Submit Grade dispute")}
                  </button>
                </section>
              ) : null}
              {openDispute && !resolvedReview ? (
                <section className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-5">
                  <h2 className="font-semibold">{t("Administrator Grade Review")}</h2>
                  <p className="mt-2 text-sm text-[var(--muted-foreground)]">
                    {openDispute.reason}
                  </p>
                  <button
                    type="button"
                    onClick={() => void uphold()}
                    disabled={loading}
                    className="mt-3 rounded-lg border border-[var(--border)] px-4 py-2 text-sm disabled:opacity-50"
                  >
                    {pendingKey
                      ? t("Retry identical disposition")
                      : t("Uphold original Grade")}
                  </button>
                  <p className="mt-2 text-xs text-[var(--muted-foreground)]">
                    {t(
                      "Overturn requires complete replacement Grade evidence and remains available through the administrator API.",
                    )}
                  </p>
                </section>
              ) : null}
              <section className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-5">
                <h2 className="font-semibold">{t("Trace and lifecycle audit")}</h2>
                <pre className="mt-3 max-h-96 overflow-auto rounded-lg bg-[var(--muted)]/40 p-3 text-xs">
                  {JSON.stringify(
                    {
                      trace: review.trace,
                      lifecycle: review.lifecycle,
                      grade_reviews: review.grade_reviews,
                    },
                    null,
                    2,
                  )}
                </pre>
              </section>
            </>
          ) : null}
        </main>
      </div>
    </div>
  );
}

function VersionScores({
  group,
  selectedSessionId,
  locale,
  scopeLabel,
  onOpen,
}: {
  group: ExamReviewGroup;
  selectedSessionId: string | null;
  locale: string;
  scopeLabel?: string;
  onOpen: (attempt: ExamReviewHistoryItem) => Promise<void>;
}) {
  const { t } = useTranslation();
  const versions = group.versions.length ? group.versions : [null];
  return (
    <section className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-5">
      <div className="flex items-start gap-3">
        <Layers3 className="mt-0.5 h-5 w-5 text-[var(--primary)]" />
        <div className="min-w-0">
          <h2 className="truncate font-semibold">
            {group.title === "Legacy practice" ? t("Legacy practice") : group.title}
          </h2>
          <p className="mt-1 break-all text-xs text-[var(--muted-foreground)]">
            {scopeLabel ?? group.subject_id}
          </p>
        </div>
      </div>
      <div className="mt-4 space-y-4">
        {versions.map((version) => {
          const attempts = group.attempts.filter(
            (attempt) => attempt.assessment_version === version,
          );
          return (
            <div key={version ?? "legacy"}>
              <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--muted-foreground)]">
                {version === null ? t("Legacy version") : `${t("Version")} ${version}`}
              </p>
              <div className="grid gap-2 sm:grid-cols-2">
                {attempts.map((attempt) => (
                  <button
                    key={attempt.practice_session_id}
                    type="button"
                    onClick={() => void onOpen(attempt)}
                    className={`rounded-lg border p-3 text-left ${
                      selectedSessionId === attempt.practice_session_id
                        ? "border-[var(--primary)] bg-[var(--primary)]/5"
                        : "border-[var(--border)]"
                    }`}
                  >
                    <span className="flex items-center justify-between gap-3 text-sm font-medium">
                      <span>
                        {t("Attempt #{{number}}", {
                          number:
                            attempt.assessment_attempt_number ?? attempt.attempt_number,
                        })}
                      </span>
                      <span>{formatScore(attempt.score)}</span>
                    </span>
                    <span className="mt-1 block text-xs text-[var(--muted-foreground)]">
                      {statusLabel(t, attempt.attempt_status)} · {attempt.correct_count ?? 0}/
                      {attempt.answer_count} {t("correct")}
                    </span>
                    <span className="mt-1 block text-xs text-[var(--muted-foreground)]">
                      {new Date(attempt.started_at).toLocaleString(locale)}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-4">
      <p className="text-xs text-[var(--muted-foreground)]">{label}</p>
      <p className="mt-1 break-all text-sm font-semibold">{value}</p>
    </div>
  );
}

function scopeKey(group: Pick<ExamReviewGroup, "exam_id" | "subject_id">): string {
  return `${group.exam_id}\u001f${group.subject_id}`;
}

function formatScore(score: number | null | undefined): string {
  return score === null || score === undefined ? "—" : `${(score * 100).toFixed(1)}%`;
}

function statusLabel(
  t: (key: string) => string,
  status: ExamReviewHistoryItem["attempt_status"],
): string {
  if (status === "completed") return t("Completed");
  if (status === "in_progress") return t("In progress");
  if (status === "failed") return t("Failed");
  return t("Legacy practice");
}
