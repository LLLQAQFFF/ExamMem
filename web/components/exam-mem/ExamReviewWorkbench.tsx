"use client";

import {
  Archive,
  ArchiveRestore,
  ChevronRight,
  CircleAlert,
  FileCheck2,
  Filter,
  Layers3,
  LoaderCircle,
  RefreshCw,
} from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  diagnosisTypeLabel,
  disputeGrade,
  formatExamScore,
  getExamReview,
  groupExamReviewHistory,
  listExamReviewHistory,
  practiceStateLabel,
  recommendationReasonLabel,
  type ExamReview,
  type ExamReviewGroup,
  type ExamReviewHistoryItem,
  upholdGrade,
} from "@/lib/exam-mem-product";
import {
  archiveAssessment,
  getAssessmentSourceSnapshot,
  listAssessments,
  listStudyPlans,
  restoreAssessment,
  type AssessmentSourceSnapshot,
} from "@/lib/exam-mem-study-plans";
import ExamMemMarkdown from "@/components/exam-mem/ExamMemMarkdown";

type StatusFilter = "all" | "completed" | "in_progress" | "failed";
type ArchiveFilter = "active" | "archived";

export default function ExamReviewWorkbench() {
  const searchParams = useSearchParams();
  const { t, i18n } = useTranslation();
  const zh = i18n.language?.toLowerCase().startsWith("zh");
  const [history, setHistory] = useState<ExamReviewHistoryItem[]>([]);
  const [selectedExamKey, setSelectedExamKey] = useState<string | null>(null);
  const [review, setReview] = useState<ExamReview | null>(null);
  const [sourceSnapshot, setSourceSnapshot] =
    useState<AssessmentSourceSnapshot | null>(null);
  const [scopeFilter, setScopeFilter] = useState("");
  const [scopeLabels, setScopeLabels] = useState<Record<string, string>>({});
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [archiveFilter, setArchiveFilter] = useState<ArchiveFilter>("active");
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
      const matchesScope = scopeKey(group) === scopeFilter;
      const matchesStatus =
        statusFilter === "all" ||
        group.attempts.some((attempt) => attempt.attempt_status === statusFilter);
      const matchesQuery =
        !normalizedQuery ||
        `${group.title} ${group.exam_id} ${group.subject_id}`
          .toLocaleLowerCase(i18n.language)
          .includes(normalizedQuery);
      const matchesArchive =
        archiveFilter === "archived"
          ? group.archived_at !== null
          : group.archived_at === null;
      return matchesScope && matchesStatus && matchesQuery && matchesArchive;
    });
  }, [archiveFilter, groups, i18n.language, query, scopeFilter, statusFilter]);
  const selectedGroup = groups.find((group) => group.key === selectedExamKey) ?? null;

  const openAttempt = useCallback(
    async (item: ExamReviewHistoryItem) => {
      setLoading(true);
      setError(null);
      setSourceSnapshot(null);
      try {
        const nextReview = await getExamReview(
          item.practice_session_id,
          item.exam_id,
          item.subject_id,
        );
        setReview(nextReview);
        setSourceSnapshot(
          nextReview.assessment
            ? await getAssessmentSourceSnapshot(
                nextReview.assessment.assessment_id,
                nextReview.assessment.assessment_version,
              )
            : null,
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
        listAssessments("all"),
        listStudyPlans("all"),
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
      const requestedSessionId = searchParams.get("practice_session_id");
      const requestedAttempt = sessions.find(
        (item) => item.practice_session_id === requestedSessionId,
      );
      const requestedGroup = requestedAttempt
        ? loadedGroups.find(
            (item) =>
              item.key ===
              (requestedAttempt.assessment_id ??
                `legacy:${requestedAttempt.exam_id}:${requestedAttempt.subject_id}`),
          )
        : null;
      const first =
        requestedGroup ??
        loadedGroups.find((group) => group.archived_at === null) ??
        loadedGroups[0];
      if (first) {
        setArchiveFilter(first.archived_at === null ? "active" : "archived");
        setScopeFilter(scopeKey(first));
        setSelectedExamKey(first.key);
        const firstAttempt =
          requestedAttempt ??
          first.attempts.find((attempt) => attempt.attempt_status === "completed") ??
          first.attempts[0];
        if (firstAttempt) {
          const nextReview = await getExamReview(
            firstAttempt.practice_session_id,
            firstAttempt.exam_id,
            firstAttempt.subject_id,
          );
          setReview(nextReview);
          setSourceSnapshot(
            nextReview.assessment
              ? await getAssessmentSourceSnapshot(
                  nextReview.assessment.assessment_id,
                  nextReview.assessment.assessment_version,
                )
              : null,
          );
        }
      } else {
        setSelectedExamKey(null);
        setReview(null);
        setSourceSnapshot(null);
      }
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : t("Exam Review failed to load."),
      );
    } finally {
      setLoading(false);
    }
  }, [searchParams, t]);

  useEffect(() => void load(), [load]);
  useEffect(() => {
    if (!history.length) return;
    if (!filteredGroups.length) {
      setSelectedExamKey(null);
      setReview(null);
      setSourceSnapshot(null);
      return;
    }
    if (!filteredGroups.some((group) => group.key === selectedExamKey)) {
      selectExam(filteredGroups[0]);
    }
  }, [filteredGroups, history.length, selectExam, selectedExamKey]);

  const graded =
    review?.checkpoints.find((item) => item.grade_result !== null) ?? null;
  const toggleArchive = async () => {
    if (!selectedGroup?.assessment_id) return;
    const isArchived = selectedGroup.archived_at !== null;
    if (
      !isArchived &&
      !window.confirm(
        t(
          "Archive this exam? Its versions, answers, review evidence, and Learning Memory remain available. Any in-progress attempt will end.",
        ),
      )
    ) {
      return;
    }
    setLoading(true);
    setError(null);
    try {
      if (isArchived) {
        await restoreAssessment(selectedGroup.assessment_id);
      } else {
        await archiveAssessment(selectedGroup.assessment_id);
      }
      setArchiveFilter("active");
      setSelectedExamKey(null);
      setReview(null);
      await load();
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : t(isArchived ? "Exam restore failed." : "Exam archive failed."),
      );
    } finally {
      setLoading(false);
    }
  };
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
        <div className="grid gap-3 md:grid-cols-4">
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t("Search exams")}
            className="rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-sm"
          />
          <select
            value={scopeFilter}
            onChange={(event) => {
              setScopeFilter(event.target.value);
              setSelectedExamKey(null);
              setReview(null);
            }}
            className="rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-sm"
          >
            <option value="" disabled>
              {t("Select a study plan")}
            </option>
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
          <select
            value={archiveFilter}
            onChange={(event) => {
              setArchiveFilter(event.target.value as ArchiveFilter);
              setSelectedExamKey(null);
              setReview(null);
            }}
            className="rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-sm"
          >
            <option value="active">{t("Current exams")}</option>
            <option value="archived">{t("Archived exams")}</option>
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
              onToggleArchive={() => void toggleArchive()}
              archivePending={loading}
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
                <Fact label={t("State")} value={practiceStateLabel(review.step_state, zh)} />
                <Fact label={t("Score")} value={review.score_invalid ? (zh ? "暂无有效评分" : "No valid score") : formatExamScore(review.score, zh ? "暂无有效评分" : "No valid score")} />
                <Fact
                  label={t("Correct answers")}
                  value={`${review.correct_count ?? 0}/${review.answer_count}`}
                />
                <Fact
                  label={zh ? "最近更新" : "Last updated"}
                  value={new Date(review.updated_at).toLocaleString(i18n.language)}
                />
              </section>
              <AttemptSummary review={review} chinese={zh} />
              {sourceSnapshot ? (
                <section className="rounded-xl border border-teal-500/30 bg-teal-500/5 p-5">
                  <h2 className="font-semibold">
                    {zh ? "固定教材证据" : "Pinned textbook evidence"}
                  </h2>
                  <p className="mt-1 text-xs text-[var(--muted-foreground)]">
                    {zh
                      ? `试卷 v${sourceSnapshot.assessment_version} 使用生成时固定的教材版本、章节和索引版本。`
                      : `Assessment v${sourceSnapshot.assessment_version} uses textbook versions, sections, and index versions pinned at generation.`}
                  </p>
                  <div className="mt-3 space-y-3">
                    {sourceSnapshot.evidence.map((source) => (
                      <article
                        key={`${source.textbook_title}:${source.textbook_version}:${source.priority}`}
                        className="rounded-lg border border-[var(--border)] bg-[var(--card)] p-3"
                      >
                        <p className="text-sm font-medium">
                          {source.textbook_title} v{source.textbook_version}
                        </p>
                        <p className="text-xs text-[var(--muted-foreground)]">
                          {zh
                            ? `${source.role} · 优先级 ${source.priority}`
                            : `${source.role} · priority ${source.priority}`}
                        </p>
                        {source.evidence.map((item, index) => (
                          <div key={`${item.section_key ?? "section"}:${index}`} className="mt-2 text-xs">
                            <p className="font-medium">
                              {Array.isArray(item.section_path)
                                ? item.section_path.join(" / ")
                                : item.section_path || item.section_key || (zh ? "未知章节" : "Unknown section")}
                              {item.start_page ? ` · ${zh ? "第" : "p. "}${item.start_page}${zh ? "页" : ""}` : ""}
                            </p>
                            <p className="mt-1 line-clamp-3 text-[var(--muted-foreground)]">
                              {item.content}
                            </p>
                          </div>
                        ))}
                      </article>
                    ))}
                  </div>
                </section>
              ) : null}
              {review.checkpoints
                .filter((checkpoint) => checkpoint.submitted_answer)
                .map((checkpoint, index) => (
                  <section
                    key={checkpoint.checkpoint_key}
                    className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-5"
                  >
                  <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-[var(--muted-foreground)]">
                    <p className="font-semibold">{t("Question")} {index + 1}</p>
                    <span className="rounded-full bg-[var(--muted)] px-2 py-1">
                      {practiceStateLabel(checkpoint.step_state, zh)}
                    </span>
                  </div>
                  <ExamMemMarkdown
                    content={checkpoint.question?.stem ?? practiceStateLabel(checkpoint.step_state, zh)}
                    className="mt-1 font-semibold"
                  />
                  <div className="mt-3 grid gap-3 md:grid-cols-2">
                    <AnswerBlock
                      label={t("Your answer")}
                      value={checkpoint.submitted_answer?.answer}
                    />
                    <AnswerBlock
                      label={t("Reference answer")}
                      value={checkpoint.question?.reference_answer}
                    />
                  </div>
                  {checkpoint.question?.grading_rubric ? (
                    <AnswerBlock
                      label={t("Solution and rubric")}
                      value={rubricText(checkpoint.question.grading_rubric)}
                    />
                  ) : null}
                  {checkpoint.grade_result ? (
                    <div className="mt-3 text-sm">
                      <p className="font-medium">
                        {t("Grade")}: {formatExamScore(checkpoint.grade_result.score, zh ? "暂无有效评分" : "No valid score")}
                      </p>
                      <ExamMemMarkdown
                        content={checkpoint.grade_result.evidence.join("\n\n")}
                        className="mt-1 text-[var(--muted-foreground)]"
                      />
                    </div>
                  ) : null}
                  {checkpoint.diagnosis_result ? (
                    <div className="mt-2 text-sm text-[var(--muted-foreground)]">
                      <p>{diagnosisTypeLabel(checkpoint.diagnosis_result.error_type, zh)}</p>
                      {checkpoint.diagnosis_result.error_type ? (
                        <ExamMemMarkdown
                          content={checkpoint.diagnosis_result.explanation}
                          className="mt-1"
                        />
                      ) : null}
                    </div>
                  ) : null}
                  {checkpoint.recommendation ? (
                    <p className="mt-2 text-sm text-[var(--muted-foreground)]">
                      {t("Next recommendation")}: {" "}
                      {checkpoint.recommendation.reason_codes.map((reasonCode) => recommendationReasonLabel(reasonCode, zh)).join(" · ")}
                    </p>
                  ) : null}
                  {checkpoint.learning_event_id && review.exam_id.startsWith("plan:") ? (
                    <Link
                      href={archiveEvidenceHref(review, checkpoint.learning_event_id)}
                      className="mt-3 inline-flex text-xs text-[var(--primary)] underline-offset-4 hover:underline"
                    >
                      {t("View this evidence in Learning Archive")}
                    </Link>
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
                  <ExamMemMarkdown
                    content={openDispute.reason}
                    className="mt-2 text-sm text-[var(--muted-foreground)]"
                  />
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
              <details className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-5">
                <summary className="cursor-pointer font-semibold">
                  {zh ? "技术审计详情" : "Technical audit details"}
                </summary>
                <p className="mt-1 text-xs text-[var(--muted-foreground)]">
                  {zh ? "包含追踪事件、生命周期决策和评分复核记录。" : "Contains trace events, lifecycle decisions, and grade review records."}
                </p>
                <pre className="mt-3 max-h-96 overflow-auto rounded-lg bg-[var(--muted)]/40 p-3 text-xs">
                  {JSON.stringify(
                    {
                      practice_session_id: review.practice_session_id,
                      runtime: review.runtime,
                      checkpoints: review.checkpoints.map((checkpoint) => ({
                        checkpoint_key: checkpoint.checkpoint_key,
                        step_state: checkpoint.step_state,
                        grade_artifact: checkpoint.grade_artifact,
                      })),
                      trace: review.trace,
                      lifecycle: review.lifecycle,
                      grade_reviews: review.grade_reviews,
                    },
                    null,
                    2,
                  )}
                </pre>
              </details>
            </>
          ) : null}
        </main>
      </div>
    </div>
  );
}

function AttemptSummary({ review, chinese }: { review: ExamReview; chinese: boolean }) {
  const { t } = useTranslation();
  const summary = review.attempt_summary;
  return (
    <section className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-5">
      <h2 className="font-semibold">{t("Assessment summary")}</h2>
      <p className="mt-1 text-xs text-[var(--muted-foreground)]">
        {t(
          "Generated deterministically from persisted grades, diagnoses and recommendations.",
        )}
      </p>
      <div className="mt-4 grid gap-3 md:grid-cols-3">
        <SummaryList label={t("Strengths")} items={summary.strengths} />
        <SummaryList label={t("Weak points")} items={summary.weak_points} />
        <SummaryList label={t("Error patterns")} items={summary.error_patterns.map((item) => diagnosisTypeLabel(item, chinese))} />
      </div>
      {summary.next_actions.length ? (
        <div className="mt-4 rounded-lg bg-[var(--muted)]/40 p-3 text-sm">
          <p className="font-medium">{t("Next actions")}</p>
          {summary.next_actions.map((item, index) => (
            <p
              key={`${item.knowledge_point_id}:${index}`}
              className="mt-1 text-[var(--muted-foreground)]"
            >
              {item.knowledge_point_id} · {item.reason_codes.map((reasonCode) => recommendationReasonLabel(reasonCode, chinese)).join(" · ")}
            </p>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function SummaryList({ label, items }: { label: string; items: string[] }) {
  return (
    <div className="rounded-lg bg-[var(--muted)]/40 p-3">
      <p className="text-xs font-medium">{label}</p>
      <p className="mt-1 break-words text-sm text-[var(--muted-foreground)]">
        {items.length ? items.join(" · ") : "—"}
      </p>
    </div>
  );
}

function AnswerBlock({ label, value }: { label: string; value?: string | null }) {
  if (!value) return null;
  return (
    <div className="mt-3 rounded-lg bg-[var(--muted)]/40 p-3">
      <p className="text-xs font-medium">{label}</p>
      <ExamMemMarkdown
        content={value}
        className="mt-1 text-sm text-[var(--muted-foreground)]"
      />
    </div>
  );
}

function rubricText(rubric: Record<string, unknown>): string {
  const steps = rubric.required_steps;
  if (Array.isArray(steps)) {
    const descriptions = steps
      .map((item) =>
        item && typeof item === "object" && "description" in item
          ? String(item.description)
          : "",
      )
      .filter(Boolean);
    if (descriptions.length) return descriptions.join("\n\n");
  }
  return `\`\`\`json\n${JSON.stringify(rubric, null, 2)}\n\`\`\``;
}

function archiveEvidenceHref(review: ExamReview, eventId: string): string {
  const params = new URLSearchParams({
    view: "l1",
    event_id: eventId,
    subject_id: review.subject_id,
  });
  if (review.exam_id.startsWith("plan:")) {
    params.set("plan_id", review.exam_id.slice("plan:".length));
  }
  if (review.assessment?.taxonomy_version) {
    params.set("taxonomy_version", review.assessment.taxonomy_version);
  }
  return `/exam-mem/memories?${params}`;
}

function VersionScores({
  group,
  selectedSessionId,
  locale,
  scopeLabel,
  onOpen,
  onToggleArchive,
  archivePending,
}: {
  group: ExamReviewGroup;
  selectedSessionId: string | null;
  locale: string;
  scopeLabel?: string;
  onOpen: (attempt: ExamReviewHistoryItem) => Promise<void>;
  onToggleArchive: () => void;
  archivePending: boolean;
}) {
  const { t } = useTranslation();
  const chinese = locale.toLowerCase().startsWith("zh");
  const versions = group.versions.length ? group.versions : [null];
  return (
    <section className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <Layers3 className="mt-0.5 h-5 w-5 text-[var(--primary)]" />
          <div className="min-w-0">
            <h2 className="truncate font-semibold">
              {group.title === "Legacy practice"
                ? t("Legacy practice")
                : group.title}
            </h2>
            <p className="mt-1 break-all text-xs text-[var(--muted-foreground)]">
              {scopeLabel ?? group.subject_id}
            </p>
          </div>
        </div>
        {group.assessment_id ? (
          <button
            type="button"
            onClick={onToggleArchive}
            disabled={archivePending}
            className="inline-flex shrink-0 items-center gap-2 rounded-lg border border-[var(--border)] px-3 py-2 text-xs disabled:opacity-50"
          >
            {group.archived_at ? (
              <ArchiveRestore className="h-4 w-4" />
            ) : (
              <Archive className="h-4 w-4" />
            )}
            {group.archived_at ? t("Restore exam") : t("Archive exam")}
          </button>
        ) : null}
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
                      <span>{attempt.score_invalid ? (chinese ? "暂无有效评分" : "No valid score") : formatExamScore(attempt.score, chinese ? "暂无有效评分" : "No valid score")}</span>
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

function statusLabel(
  t: (key: string) => string,
  status: ExamReviewHistoryItem["attempt_status"],
): string {
  if (status === "completed") return t("Completed");
  if (status === "in_progress") return t("In progress");
  if (status === "failed") return t("Failed");
  return t("Legacy practice");
}
