"use client";

import Link from "next/link";
import {
  Archive,
  ArchiveRestore,
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
  FileUp,
  GraduationCap,
  Sparkles,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  createPracticeIdentity,
  generateExamPractice,
  loadPracticeSession,
  preparePracticeAnswerRequest,
  PracticeRequestError,
  savePracticeSession,
  submitExamPracticeAnswer,
  repeatAssessmentVersion,
  type PracticeAnswerRequest,
  type PracticeGenerationProgress,
  type PracticeIdentity,
  type PracticeTurnResponse,
} from "@/lib/exam-mem-practice";
import { extractBase64FromDataUrl, readFileAsDataUrl } from "@/lib/file-attachments";
import { useAttachmentLimits } from "@/lib/attachment-limits";
import {
  archiveAssessment,
  listAssessments,
  listStudyPlans,
  restoreAssessment,
  type Assessment,
  type StudyPlan,
} from "@/lib/exam-mem-study-plans";
import {
  formatExamScore,
  listPracticeHistory,
  resumePractice,
  selectVisiblePracticeHistory,
  type PracticeHistoryItem,
} from "@/lib/exam-mem-product";
import ExamMemMarkdown from "@/components/exam-mem/ExamMemMarkdown";

function percent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

const GENERATION_WAIT_STEPS = [
  { stage: "scope", cn: "校验考试范围与知识点", en: "Validate the exam scope and objective" },
  { stage: "exploring", cn: "探索出题方向与参考资料", en: "Explore question directions and sources" },
  { stage: "planning", cn: "规划题型、难度与知识覆盖", en: "Plan question types, difficulty, and coverage" },
  { stage: "generating", cn: "逐题生成并校验题目与答案", en: "Generate and validate each question and answer" },
  { stage: "persisting", cn: "固定不可变试卷版本", en: "Freeze the immutable assessment version" },
  { stage: "starting", cn: "创建作答并启动检测", en: "Create the attempt and start the assessment" },
] as const;

export default function PracticeWorkbench() {
  const { t, i18n } = useTranslation();
  const zh = i18n.language?.toLowerCase().startsWith("zh");
  const tr = (cn: string, en: string) => (zh ? cn : en);
  const attachmentLimits = useAttachmentLimits();
  const [identity, setIdentity] = useState<PracticeIdentity | null>(null);
  const [turn, setTurn] = useState<PracticeTurnResponse | null>(null);
  const [answer, setAnswer] = useState("");
  const [attemptNumber, setAttemptNumber] = useState(1);
  const [pendingRequest, setPendingRequest] =
    useState<PracticeAnswerRequest | null>(null);
  const [loading, setLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [generationProgress, setGenerationProgress] =
    useState<PracticeGenerationProgress | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<PracticeHistoryItem[]>([]);
  const [plans, setPlans] = useState<StudyPlan[]>([]);
  const [selectedPlan, setSelectedPlan] = useState("");
  const [selectedSubject, setSelectedSubject] = useState("");
  const [selectedKnowledgePoint, setSelectedKnowledgePoint] = useState("");
  const [assessments, setAssessments] = useState<Assessment[]>([]);
  const [archiveFilter, setArchiveFilter] = useState<"active" | "archived">("active");
  const [regenerateAssessmentId, setRegenerateAssessmentId] = useState<string | undefined>();
  const [questionCount, setQuestionCount] = useState(4);
  const [difficulty, setDifficulty] = useState<"auto" | "easy" | "medium" | "hard">("auto");
  const [sourceFiles, setSourceFiles] = useState<File[]>([]);
  const refreshAssessments = useCallback(async () => {
    setAssessments(await listAssessments("all"));
  }, []);
  const refreshPracticeHistory = useCallback(async (examId: string, subjectId: string) => {
    setHistory(await listPracticeHistory(examId, subjectId));
  }, []);

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
    void listStudyPlans().then((result) => {
      const available = result.filter((item) => item.published);
      setPlans(available);
      const params = new URLSearchParams(window.location.search);
      setSelectedPlan(params.get("plan") || available[0]?.plan_id || "");
      setSelectedSubject(params.get("subject") || "");
      setSelectedKnowledgePoint(params.get("kp") || "");
    }).catch(() => undefined);
    void refreshAssessments().catch(() => undefined);
  }, [refreshAssessments]);

  useEffect(() => {
    const plan = plans.find((item) => item.plan_id === selectedPlan);
    const subjects = plan?.published?.tree.subjects ?? [];
    const subject = subjects.find((item) => item.id === selectedSubject) ?? subjects[0];
    if (!plan?.published || !subject) return;
    if (subject.id !== selectedSubject) setSelectedSubject(subject.id);
    const objectives = subject.modules.flatMap((item) => item.knowledge_points);
    if (!objectives.some((item) => item.id === selectedKnowledgePoint)) {
      setSelectedKnowledgePoint(objectives[0]?.id || "");
    }
    void refreshPracticeHistory(`plan:${plan.plan_id}`, subject.id).catch(() => undefined);
  }, [plans, refreshPracticeHistory, selectedKnowledgePoint, selectedPlan, selectedSubject]);

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

  const beginGenerated = async () => {
    const plan = plans.find((item) => item.plan_id === selectedPlan);
    const subject = plan?.published?.tree.subjects.find((item) => item.id === selectedSubject);
    const knowledgePoint = subject?.modules.flatMap((item) => item.knowledge_points).find((item) => item.id === selectedKnowledgePoint);
    if (!plan?.published || !subject || !knowledgePoint) return;
    const nextIdentity = createPracticeIdentity(crypto.randomUUID(), `plan:${plan.plan_id}`, subject.id);
    setLoading(true);
    setGenerating(true);
    setGenerationProgress(null);
    setError(null);
    setTurn(null);
    setAnswer("");
    setAttemptNumber(1);
    setPendingRequest(null);
    setIdentity(nextIdentity);
    try {
      const attachments = await Promise.all(sourceFiles.map(async (file) => {
        const suffix = file.name.toLowerCase().split(".").pop();
        const mimeType = file.type || (suffix === "pdf" ? "application/pdf" : suffix === "md" ? "text/markdown" : "text/plain");
        return {
          type: suffix === "pdf" ? "pdf" as const : "file" as const,
          filename: file.name,
          mime_type: mimeType,
          base64: extractBase64FromDataUrl(await readFileAsDataUrl(file)),
        };
      }));
      setTurn(
        await generateExamPractice(
          {
            identity: nextIdentity,
            learningPathId: `${plan.plan_id}:${plan.published.version}:${knowledgePoint.id}`,
            knowledgePointId: knowledgePoint.id,
            knowledgePointName: knowledgePoint.name,
            taxonomyVersion: plan.published.taxonomy_versions[subject.id],
            numQuestions: questionCount,
            difficulty,
            language: zh ? "zh" : "en",
            attachments,
            assessmentId: regenerateAssessmentId,
            assessmentTitle: tr(
              `${knowledgePoint.name} 专项检测`,
              `${knowledgePoint.name} assessment`,
            ),
          },
          setGenerationProgress,
        ),
      );
      setRegenerateAssessmentId(undefined);
      void refreshAssessments();
      void refreshPracticeHistory(nextIdentity.examId, nextIdentity.subjectId);
    } catch (cause) {
      if (cause instanceof PracticeRequestError && cause.partialTurn) setTurn(cause.partialTurn);
      setError(cause instanceof Error ? cause.message : tr("生成练习失败。", "Practice generation failed."));
    } finally {
      setGenerating(false);
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
      void refreshAssessments();
      void refreshPracticeHistory(identity.examId, identity.subjectId);
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
      const plan = plans.find((candidate) => candidate.plan_id === selectedPlan);
      const subject = plan?.published?.tree.subjects.find((candidate) => candidate.id === selectedSubject);
      if (!plan || !subject) throw new Error(tr("考试范围不可用。", "Exam scope is unavailable."));
      const resumed = await resumePractice(item.practice_session_id, `plan:${plan.plan_id}`, subject.id);
      setIdentity({
        practiceSessionId: item.practice_session_id,
        traceId: item.trace_id,
        examId: `plan:${plan.plan_id}`,
        subjectId: subject.id,
      });
      setTurn(resumed);
      setAnswer("");
      setAttemptNumber(item.answer_count + 1);
      setPendingRequest(null);
      void refreshAssessments();
      void refreshPracticeHistory(`plan:${plan.plan_id}`, subject.id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("Practice recovery failed."));
    } finally {
      setLoading(false);
    }
  };

  const repeatAssessment = async (assessment: Assessment, version: number) => {
    const nextIdentity = createPracticeIdentity(
      crypto.randomUUID(),
      assessment.exam_id,
      assessment.subject_id,
    );
    setLoading(true);
    setError(null);
    try {
      setIdentity(nextIdentity);
      setTurn(await repeatAssessmentVersion({ assessmentId: assessment.assessment_id, version, identity: nextIdentity }));
      setAnswer("");
      setAttemptNumber(1);
      setPendingRequest(null);
      void refreshAssessments();
      void refreshPracticeHistory(nextIdentity.examId, nextIdentity.subjectId);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : tr("重考失败。", "Could not repeat the assessment."));
    } finally { setLoading(false); }
  };

  const toggleAssessmentArchive = async (assessment: Assessment) => {
    setLoading(true);
    setError(null);
    try {
      if (assessment.archived_at) await restoreAssessment(assessment.assessment_id);
      else await archiveAssessment(assessment.assessment_id);
      await refreshAssessments();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : tr("无法更新练习归档状态。", "Could not update practice archive state."));
    } finally {
      setLoading(false);
    }
  };

  const plan = plans.find((item) => item.plan_id === selectedPlan);
  const subjects = plan?.published?.tree.subjects ?? [];
  const subject = subjects.find((item) => item.id === selectedSubject) ?? subjects[0];
  const objectives = subject?.modules.flatMap((item) => item.knowledge_points) ?? [];
  const allScopedAssessments = assessments.filter((item) => item.exam_id === `plan:${selectedPlan}` && item.subject_id === subject?.id);
  const scopedAssessments = allScopedAssessments.filter((item) => archiveFilter === "archived" ? item.archived_at !== null : item.archived_at === null);
  const archivedPracticeSessions = useMemo(
    () => new Set(assessments.filter((item) => item.archived_at).flatMap((item) => item.attempts.map((attempt) => attempt.practice_session_id))),
    [assessments],
  );
  const visibleHistory = useMemo(
    () => selectVisiblePracticeHistory(history, archivedPracticeSessions, archiveFilter),
    [archiveFilter, archivedPracticeSessions, history],
  );
  const archivedHistoryCount = useMemo(
    () => history.filter((item) => archivedPracticeSessions.has(item.practice_session_id)).length,
    [archivedPracticeSessions, history],
  );

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
            <h1 className="font-serif text-2xl font-semibold">{t("Practice")}</h1>
            <p className="mt-1 max-w-2xl text-sm text-[var(--muted-foreground)]">
              {t("Question, grading, diagnosis, Learning Memory, and an explainable next recommendation in one recoverable turn.")}
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={() => void beginGenerated()}
          disabled={loading || !plan || !subject || !selectedKnowledgePoint}
          className="inline-flex items-center gap-2 rounded-lg bg-[var(--primary)] px-4 py-2 text-sm font-medium text-[var(--primary-foreground)] disabled:opacity-50"
        >
          {turn ? <RotateCcw className="h-4 w-4" /> : <ArrowRight className="h-4 w-4" />}
          {turn ? tr("生成新版检测", "Generate a new version") : tr("生成并开始检测", "Generate and start")}
        </button>
      </header>

      <section className="grid gap-3 sm:grid-cols-3">
        <Info label={t("Business store")} value={t("Independent ExamMem PostgreSQL")} />
        <Info label={tr("考试范围", "Exam scope")} value={plan && subject ? `${plan.name} / ${subject.name}` : tr("请先发布学习计划", "Publish a study plan first")} />
        <Info label={t("Recovery")} value={t("Server checkpoint + immutable retry key")} />
      </section>

      <section className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-5">
        <div className="flex items-center gap-2"><GraduationCap className="h-5 w-5 text-[var(--primary)]" /><h2 className="font-semibold">{tr("从已发布学习计划创建专项检测", "Create an assessment from a published study plan")}</h2></div>
        <p className="mt-1 text-xs text-[var(--muted-foreground)]">{tr("考试范围来自已确认的大纲版本。选择叶子知识点后，可附加本次出题参考文件；生成题集会固定为可重考的试卷版本。", "The scope comes from a reviewed syllabus version. Choose a leaf objective and optional sources; the generated catalog becomes a repeatable assessment version.")}</p>
        <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <label className="text-xs text-[var(--muted-foreground)]">{tr("学习计划", "Study plan")}<select value={selectedPlan} onChange={(event) => { setSelectedPlan(event.target.value); setSelectedSubject(""); setSelectedKnowledgePoint(""); }} className="mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-sm"><option value="">{tr("请选择", "Select")}</option>{plans.map((item) => <option key={item.plan_id} value={item.plan_id}>{tr(`${item.name} · 版本 ${item.active_version}`, `${item.name} · v${item.active_version}`)}</option>)}</select></label>
          <label className="text-xs text-[var(--muted-foreground)]">{tr("考试科目", "Exam subject")}<select value={subject?.id || ""} onChange={(event) => { setSelectedSubject(event.target.value); setSelectedKnowledgePoint(""); }} className="mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-sm"><option value="">{tr("请选择", "Select")}</option>{subjects.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
          <label className="text-xs text-[var(--muted-foreground)]">{tr("知识点", "Objective")}<select value={selectedKnowledgePoint} onChange={(event) => { setSelectedKnowledgePoint(event.target.value); setRegenerateAssessmentId(undefined); }} className="mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-sm"><option value="">{tr("请选择", "Select")}</option>{objectives.map((objective) => <option key={objective.id} value={objective.id}>{objective.name}</option>)}</select></label>
          <label className="text-xs text-[var(--muted-foreground)]">{tr("难度", "Difficulty")}<select value={difficulty} onChange={(event) => setDifficulty(event.target.value as typeof difficulty)} className="mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-sm"><option value="auto">{tr("自动", "Auto")}</option><option value="easy">{tr("简单", "Easy")}</option><option value="medium">{tr("中等", "Medium")}</option><option value="hard">{tr("困难", "Hard")}</option></select></label>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <label className="inline-flex cursor-pointer items-center gap-2 rounded-lg border border-[var(--border)] px-3 py-2 text-sm"><FileUp className="h-4 w-4" />{tr("添加参考文件（PDF/TXT/MD）", "Add sources (PDF/TXT/MD)")}<input type="file" multiple accept=".pdf,.txt,.md" className="hidden" onChange={(event) => { const files = Array.from(event.target.files || []); const total = files.reduce((sum, file) => sum + file.size, 0); if (files.some((file) => file.size > attachmentLimits.maxFileBytes) || total > attachmentLimits.maxTotalBytes) { setError(tr("文件超过附件大小限制。", "Files exceed the attachment limit.")); return; } setSourceFiles(files); }} /></label>
          {sourceFiles.map((file) => <span key={`${file.name}:${file.size}`} className="rounded-full bg-[var(--muted)] px-2 py-1 text-xs">{file.name}</span>)}
          <label className="ml-auto flex items-center gap-2 text-sm">{tr("题数", "Questions")}<input type="number" min={2} max={10} value={questionCount} onChange={(event) => setQuestionCount(Math.max(2, Math.min(10, Number(event.target.value) || 2)))} className="w-16 rounded-lg border border-[var(--border)] bg-[var(--background)] px-2 py-1.5" /></label>
          <button type="button" onClick={() => void beginGenerated()} disabled={loading || !plan || !subject || !selectedKnowledgePoint} className="inline-flex items-center gap-2 rounded-lg bg-[var(--primary)] px-4 py-2 text-sm font-medium text-[var(--primary-foreground)] disabled:opacity-50"><Sparkles className="h-4 w-4" />{regenerateAssessmentId ? tr("生成下一版本", "Generate next version") : tr("生成并开始检测", "Generate and start")}</button>
        </div>
      </section>

      {allScopedAssessments.length ? (
        <section className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2 text-sm font-semibold"><History className="h-4 w-4 text-[var(--primary)]" />{tr("考试版本与多次作答", "Assessment versions and attempts")}</div>
            <div className="flex rounded-lg bg-[var(--muted)]/40 p-1">
              {(["active", "archived"] as const).map((value) => <button key={value} type="button" onClick={() => setArchiveFilter(value)} className={`rounded-md px-3 py-1 text-xs ${archiveFilter === value ? "bg-[var(--background)] font-medium shadow-sm" : "text-[var(--muted-foreground)]"}`}>{value === "active" ? tr("当前练习", "Current") : tr("已归档", "Archived")}</button>)}
            </div>
          </div>
          <div className="mt-3 grid gap-2 md:grid-cols-2">
            {scopedAssessments.map((assessment) => <article key={assessment.assessment_id} className="rounded-lg border border-[var(--border)] p-3"><p className="text-sm font-medium">{assessment.title}</p><p className="mt-1 text-xs text-[var(--muted-foreground)]">{tr(`当前 v${assessment.latest_version} · ${assessment.attempts.length} 次作答`, `Current v${assessment.latest_version} · ${assessment.attempts.length} attempts`)}</p><div className="mt-3 flex flex-wrap gap-2">{!assessment.archived_at ? <button type="button" disabled={loading} onClick={() => void repeatAssessment(assessment, assessment.latest_version)} className="rounded-lg border border-[var(--border)] px-3 py-1.5 text-xs">{tr("重考当前版本", "Repeat current version")}</button> : null}{!assessment.archived_at ? <button type="button" disabled={loading} onClick={() => { setRegenerateAssessmentId(assessment.assessment_id); setSelectedKnowledgePoint(assessment.knowledge_point_ids[0] || ""); }} className="rounded-lg border border-[var(--border)] px-3 py-1.5 text-xs text-[var(--primary)]">{tr("基于同一考试生成新版", "Generate a new version")}</button> : null}<button type="button" disabled={loading} onClick={() => void toggleAssessmentArchive(assessment)} className="inline-flex items-center gap-1 rounded-lg border border-[var(--border)] px-3 py-1.5 text-xs">{assessment.archived_at ? <ArchiveRestore className="h-3.5 w-3.5" /> : <Archive className="h-3.5 w-3.5" />}{assessment.archived_at ? tr("恢复", "Restore") : tr("归档", "Archive")}</button></div></article>)}
          </div>
          {!scopedAssessments.length ? <p className="mt-3 rounded-lg border border-dashed border-[var(--border)] p-5 text-center text-sm text-[var(--muted-foreground)]">{archiveFilter === "archived" ? tr("没有已归档练习。", "No archived practices.") : tr("没有当前练习。", "No current practices.")}</p> : null}
        </section>
      ) : null}

      {visibleHistory.length ? (
        <section className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-4">
          <div className="flex items-center gap-2 text-sm font-semibold">
            <History className="h-4 w-4 text-[var(--primary)]" />
            {t("Practice history and server recovery")}
          </div>
          {archiveFilter === "active" && archivedHistoryCount > 0 ? (
            <p className="mt-2 text-xs text-[var(--muted-foreground)]">
              {tr(
                `当前列表保留原始练习序号；缺少的 ${archivedHistoryCount} 次记录在“已归档”中。`,
                `Original attempt numbers are preserved; ${archivedHistoryCount} hidden record(s) are under Archived.`,
              )}
            </p>
          ) : null}
          <div className="mt-3 grid gap-2 md:grid-cols-2">
            {visibleHistory.slice(0, 6).map((item) => (
              <button
                key={item.practice_session_id}
                type="button"
                onClick={() => void resume(item)}
                disabled={loading}
                className="min-w-0 rounded-lg border border-[var(--border)] p-3 text-left hover:bg-[var(--muted)]/40 disabled:opacity-50"
              >
                <span className="block truncate text-sm font-medium">
                  {tr(`第 ${item.attempt_number} 次练习`, `Practice #${item.attempt_number}`)} · {item.current_checkpoint.question?.stem ?? t("Practice session")}
                </span>
                <span className="mt-1 block text-xs text-[var(--muted-foreground)]">
                  {item.current_checkpoint.grade_result ? `${tr("得分", "Score")} ${formatExamScore(item.current_checkpoint.grade_result.score, tr("评分数据异常", "Invalid score data"))} · ` : ""}{item.step_state} · {item.answer_count} {t("answers")} · {item.runtime?.backend_mode ?? t("legacy configuration")}
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

      {loading && generating ? (
        <GenerationWaiting
          questionCount={questionCount}
          progress={generationProgress}
          tr={tr}
        />
      ) : loading ? (
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
            {tr("先在“学习计划”中导入并发布考试大纲，然后从叶子知识点生成检测。", "Import and publish a syllabus in Study Plans, then generate an assessment from a leaf objective.")}
          </p>
        </section>
      ) : null}

      {!loading && practice ? (
        <div className="grid min-w-0 gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(280px,340px)]">
          <main className="min-w-0 space-y-5">
            {practice.completed ? (
              <section className="rounded-xl border border-emerald-500/30 bg-emerald-500/5 p-5">
                <div className="flex items-center gap-2"><CheckCircle2 className="h-5 w-5 text-emerald-500" /><h2 className="font-semibold">{tr("本次检测已完成", "Assessment completed")}</h2></div>
                <p className="mt-2 text-sm text-[var(--muted-foreground)]">{tr(`已完成 ${practice.answered_question_count ?? 0}/${practice.question_count ?? 0} 道题。你可以在上方重考同一版本，或基于同一考试 ID 生成新版。`, `Completed ${practice.answered_question_count ?? 0}/${practice.question_count ?? 0} questions. Repeat this version or generate a new version under the same assessment ID above.`)}</p>
              </section>
            ) : null}
            {grade ? (
              <section className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-5">
                <div className="flex items-center gap-2">
                  {grade.correct ? <CheckCircle2 className="h-5 w-5 text-emerald-500" /> : <CircleAlert className="h-5 w-5 text-amber-500" />}
                  <h2 className="font-semibold">
                    {grade.correct ? t("Correct") : t("Needs review")} · {t("Score")} {formatExamScore(grade.score, tr("评分数据异常", "Invalid score data"))}
                  </h2>
                </div>
                <ExamMemMarkdown
                  content={grade.evidence.join("\n\n")}
                  className="mt-3 text-sm text-[var(--muted-foreground)]"
                />
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
                <ExamMemMarkdown
                  content={question.stem}
                  className="mt-4 text-lg font-medium leading-8"
                />
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
                <ExamMemMarkdown
                  content={diagnosis.explanation}
                  className="mt-2 text-xs leading-5 text-[var(--muted-foreground)]"
                />
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

function GenerationWaiting({
  questionCount,
  progress,
  tr,
}: {
  questionCount: number;
  progress: PracticeGenerationProgress | null;
  tr: (cn: string, en: string) => string;
}) {
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const currentStep = progress
    ? GENERATION_WAIT_STEPS.findIndex((step) => step.stage === progress.stage)
    : -1;
  const generatedQuestions = Math.min(
    progress?.completed_questions ?? 0,
    progress?.total_questions ?? questionCount,
  );
  const totalQuestions = progress?.total_questions ?? questionCount;

  useEffect(() => {
    const startedAt = Date.now();
    const timer = window.setInterval(() => {
      setElapsedSeconds(Math.floor((Date.now() - startedAt) / 1000));
    }, 1000);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <section className="rounded-xl border border-[var(--primary)]/30 bg-[var(--card)] p-5">
      <div className="flex items-start gap-3">
        <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-[var(--primary)]/10 text-[var(--primary)]">
          <LoaderCircle className="h-5 w-5 animate-spin" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 role="status" className="font-semibold">
              {currentStep >= 0
                ? tr(
                    GENERATION_WAIT_STEPS[currentStep].cn,
                    GENERATION_WAIT_STEPS[currentStep].en,
                  )
                : tr("正在连接出题服务", "Connecting to the question service")}
            </h2>
            <span className="text-xs tabular-nums text-[var(--muted-foreground)]">{tr(`已等待 ${elapsedSeconds} 秒`, `${elapsedSeconds}s elapsed`)}</span>
          </div>
          <p className="mt-1 text-xs text-[var(--muted-foreground)]">
            {tr("出题 Agent 会依次完成以下流程，全部校验通过后才会固定试卷并开始检测。", "The question agent completes these steps before freezing the assessment and starting the attempt.")}
          </p>
        </div>
      </div>

      {progress?.stage === "generating" ? (
        <div className="mt-5">
          <div className="mb-1 flex justify-between text-xs text-[var(--muted-foreground)]">
            <span>{tr("题目生成进度", "Question generation progress")}</span>
            <span className="tabular-nums">{generatedQuestions}/{totalQuestions}</span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-[var(--muted)]">
            <div
              className="h-full rounded-full bg-[var(--primary)] transition-[width] duration-300"
              style={{ width: `${totalQuestions > 0 ? (generatedQuestions / totalQuestions) * 100 : 0}%` }}
            />
          </div>
        </div>
      ) : (
        <div className="mt-5 h-1.5 overflow-hidden rounded-full bg-[var(--muted)]">
          <div className="h-full w-1/2 animate-pulse rounded-full bg-[var(--primary)]" />
        </div>
      )}
      <ol className="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
        {GENERATION_WAIT_STEPS.map((step, index) => {
          const completed = currentStep > index;
          const active = currentStep === index;
          const marker = completed
            ? <CheckCircle2 className="h-4 w-4" />
            : active
              ? <LoaderCircle className="h-4 w-4 animate-spin" />
              : index + 1;
          return (
            <li
              key={step.stage}
              className={`flex items-start gap-2 rounded-lg border p-3 text-xs ${active ? "border-[var(--primary)]/50 bg-[var(--primary)]/5" : "border-[var(--border)] bg-[var(--background)]/50"}`}
            >
              <span className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-[var(--primary)]/10 font-medium text-[var(--primary)]">
                {marker}
              </span>
              <span>
                {tr(step.cn, step.en)}
                {step.stage === "generating" && progress?.stage === "generating" ? (
                  <span className="mt-1 block tabular-nums text-[var(--muted-foreground)]">
                    {generatedQuestions}/{totalQuestions}
                  </span>
                ) : null}
              </span>
            </li>
          );
        })}
      </ol>
      <p className="mt-3 text-[11px] text-[var(--muted-foreground)]">
        {tr("状态来自服务端真实出题事件；参考答案、评分规则和模型内部内容不会进入进度流。", "Status comes from real server-side question events; reference answers, grading rubrics, and internal model content are never sent through the progress stream.")}
      </p>
    </section>
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
