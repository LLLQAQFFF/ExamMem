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
  FileUp,
  GraduationCap,
  Sparkles,
} from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  clearPracticeSession,
  createPracticeIdentity,
  generateExamPractice,
  getExamMemCatalog,
  loadPracticeSession,
  preparePracticeAnswerRequest,
  PracticeRequestError,
  savePracticeSession,
  startExamPractice,
  submitExamPracticeAnswer,
  type PracticeAnswerRequest,
  type PracticeIdentity,
  type PracticeTurnResponse,
  type ExamMemCatalog,
} from "@/lib/exam-mem-practice";
import { extractBase64FromDataUrl, readFileAsDataUrl } from "@/lib/file-attachments";
import { useAttachmentLimits } from "@/lib/attachment-limits";
import { fetchAllProgress, fetchMasteryMap, type MasteryMapResult, type ProgressSummary } from "@/lib/learning-api";
import {
  listPracticeHistory,
  resumePractice,
  type PracticeHistoryItem,
} from "@/lib/exam-mem-product";

function percent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

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
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<PracticeHistoryItem[]>([]);
  const [catalog, setCatalog] = useState<ExamMemCatalog | null>(null);
  const [selectedScope, setSelectedScope] = useState("");
  const [paths, setPaths] = useState<ProgressSummary[]>([]);
  const [selectedPath, setSelectedPath] = useState("");
  const [pathDetail, setPathDetail] = useState<MasteryMapResult | null>(null);
  const [selectedKnowledgePoint, setSelectedKnowledgePoint] = useState("");
  const [questionCount, setQuestionCount] = useState(4);
  const [difficulty, setDifficulty] = useState<"auto" | "easy" | "medium" | "hard">("auto");
  const [sourceFiles, setSourceFiles] = useState<File[]>([]);

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
    void getExamMemCatalog().then((result) => {
      setCatalog(result);
      const scope = result.scopes[0];
      if (scope) setSelectedScope(`${scope.exam_id}:${scope.subject_id}`);
    }).catch(() => undefined);
    void fetchAllProgress().then((result) => {
      const available = result.summaries.filter((item) => item.kp_count > 0);
      setPaths(available);
      const params = new URLSearchParams(window.location.search);
      setSelectedPath(params.get("path") || available[0]?.book_id || "");
      setSelectedKnowledgePoint(params.get("kp") || "");
    }).catch(() => undefined);
  }, []);

  useEffect(() => {
    const scope = catalog?.scopes.find((item) => `${item.exam_id}:${item.subject_id}` === selectedScope);
    if (!scope) return;
    void listPracticeHistory(scope.exam_id, scope.subject_id).then(setHistory).catch(() => undefined);
  }, [catalog, selectedScope]);

  useEffect(() => {
    if (!selectedPath) { setPathDetail(null); return; }
    void fetchMasteryMap(selectedPath).then((result) => {
      setPathDetail(result);
      setSelectedKnowledgePoint((current) => current || result.map.modules[0]?.knowledge_points[0]?.id || "");
    }).catch(() => setPathDetail(null));
  }, [selectedPath]);

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
    const scope = catalog?.scopes.find((item) => `${item.exam_id}:${item.subject_id}` === selectedScope);
    const nextIdentity = createPracticeIdentity(crypto.randomUUID(), scope?.exam_id, scope?.subject_id);
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

  const beginGenerated = async () => {
    const scope = catalog?.scopes.find((item) => `${item.exam_id}:${item.subject_id}` === selectedScope);
    const knowledgePoint = pathDetail?.map.modules.flatMap((item) => item.knowledge_points).find((item) => item.id === selectedKnowledgePoint);
    if (!scope || !selectedPath || !knowledgePoint) return;
    const nextIdentity = createPracticeIdentity(crypto.randomUUID(), scope.exam_id, scope.subject_id);
    setLoading(true);
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
      setTurn(await generateExamPractice({
        identity: nextIdentity,
        learningPathId: selectedPath,
        knowledgePointId: knowledgePoint.id,
        knowledgePointName: knowledgePoint.name,
        numQuestions: questionCount,
        difficulty,
        attachments,
      }));
    } catch (cause) {
      if (cause instanceof PracticeRequestError && cause.partialTurn) setTurn(cause.partialTurn);
      setError(cause instanceof Error ? cause.message : tr("生成练习失败。", "Practice generation failed."));
    } finally { setLoading(false); }
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
      const scope = catalog?.scopes.find((candidate) => `${candidate.exam_id}:${candidate.subject_id}` === selectedScope);
      if (!scope) throw new Error(tr("考试范围不可用。", "Exam scope is unavailable."));
      const resumed = await resumePractice(item.practice_session_id, scope.exam_id, scope.subject_id);
      setIdentity({
        practiceSessionId: item.practice_session_id,
        traceId: item.trace_id,
        examId: scope.exam_id,
        subjectId: scope.subject_id,
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
            <h1 className="font-serif text-2xl font-semibold">{t("Practice")}</h1>
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
        <Info label={tr("考试范围", "Exam scope")} value={catalog?.scopes[0] ? `${catalog.scopes[0].exam_name} / ${catalog.scopes[0].subject_name}` : "postgraduate_entrance_exam / math_1"} />
        <Info label={t("Recovery")} value={t("Server checkpoint + immutable retry key")} />
      </section>

      <section className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-5">
        <div className="flex items-center gap-2"><GraduationCap className="h-5 w-5 text-[var(--primary)]" /><h2 className="font-semibold">{tr("从学习路径创建专项练习", "Create practice from a learning path")}</h2></div>
        <p className="mt-1 text-xs text-[var(--muted-foreground)]">{tr("选择学过的知识点，可附加本次出题参考文件。大模型生成的题目会被固定在本次练习 checkpoint 中。", "Choose a learned objective and optionally attach source files. Generated questions are pinned to this practice checkpoint.")}</p>
        <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <label className="text-xs text-[var(--muted-foreground)]">{tr("考试范围", "Exam scope")}<select value={selectedScope} onChange={(event) => setSelectedScope(event.target.value)} className="mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-sm" disabled={!catalog?.scopes.length}>{catalog?.scopes.map((scope) => <option key={`${scope.exam_id}:${scope.subject_id}`} value={`${scope.exam_id}:${scope.subject_id}`}>{scope.exam_name} / {scope.subject_name}</option>)}</select></label>
          <label className="text-xs text-[var(--muted-foreground)]">{tr("学习路径", "Learning path")}<select value={selectedPath} onChange={(event) => { setSelectedPath(event.target.value); setSelectedKnowledgePoint(""); }} className="mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-sm"><option value="">{tr("请选择", "Select")}</option>{paths.map((path) => <option key={path.book_id} value={path.book_id}>{path.name}</option>)}</select></label>
          <label className="text-xs text-[var(--muted-foreground)]">{tr("知识点", "Objective")}<select value={selectedKnowledgePoint} onChange={(event) => setSelectedKnowledgePoint(event.target.value)} className="mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-sm"><option value="">{tr("请选择", "Select")}</option>{pathDetail?.map.modules.flatMap((module) => module.knowledge_points).map((kp) => <option key={kp.id} value={kp.id}>{kp.name} · {Math.round(kp.mastery * 100)}%</option>)}</select></label>
          <label className="text-xs text-[var(--muted-foreground)]">{tr("难度", "Difficulty")}<select value={difficulty} onChange={(event) => setDifficulty(event.target.value as typeof difficulty)} className="mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-sm"><option value="auto">{tr("自动", "Auto")}</option><option value="easy">{tr("简单", "Easy")}</option><option value="medium">{tr("中等", "Medium")}</option><option value="hard">{tr("困难", "Hard")}</option></select></label>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <label className="inline-flex cursor-pointer items-center gap-2 rounded-lg border border-[var(--border)] px-3 py-2 text-sm"><FileUp className="h-4 w-4" />{tr("添加参考文件（PDF/TXT/MD）", "Add sources (PDF/TXT/MD)")}<input type="file" multiple accept=".pdf,.txt,.md" className="hidden" onChange={(event) => { const files = Array.from(event.target.files || []); const total = files.reduce((sum, file) => sum + file.size, 0); if (files.some((file) => file.size > attachmentLimits.maxFileBytes) || total > attachmentLimits.maxTotalBytes) { setError(tr("文件超过附件大小限制。", "Files exceed the attachment limit.")); return; } setSourceFiles(files); }} /></label>
          {sourceFiles.map((file) => <span key={`${file.name}:${file.size}`} className="rounded-full bg-[var(--muted)] px-2 py-1 text-xs">{file.name}</span>)}
          <label className="ml-auto flex items-center gap-2 text-sm">{tr("题数", "Questions")}<input type="number" min={2} max={10} value={questionCount} onChange={(event) => setQuestionCount(Math.max(2, Math.min(10, Number(event.target.value) || 2)))} className="w-16 rounded-lg border border-[var(--border)] bg-[var(--background)] px-2 py-1.5" /></label>
          <button type="button" onClick={() => void beginGenerated()} disabled={loading || !selectedPath || !selectedKnowledgePoint} className="inline-flex items-center gap-2 rounded-lg bg-[var(--primary)] px-4 py-2 text-sm font-medium text-[var(--primary-foreground)] disabled:opacity-50"><Sparkles className="h-4 w-4" />{tr("生成并开始练习", "Generate and start")}</button>
        </div>
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
                  {tr(`第 ${item.attempt_number} 次练习`, `Practice #${item.attempt_number}`)} · {item.current_checkpoint.question?.stem ?? t("Practice session")}
                </span>
                <span className="mt-1 block text-xs text-[var(--muted-foreground)]">
                  {item.current_checkpoint.grade_result ? `${tr("得分", "Score")} ${item.current_checkpoint.grade_result.score} · ` : ""}{item.step_state} · {item.answer_count} {t("answers")} · {item.runtime?.backend_mode ?? t("legacy configuration")}
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
