"use client";

import {
  ArrowRight,
  BrainCircuit,
  CalendarClock,
  CheckCircle2,
  CircleAlert,
  Clock3,
  RefreshCw,
  ShieldCheck,
  Target,
  TrendingDown,
  TrendingUp,
} from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  getLearningProfile,
  type KnowledgePointProfile,
  type LearningProfile,
  type ReviewQueueItem,
} from "@/lib/exam-mem-learning-profile";
import {
  listStudyPlans,
  type PublishedStudyPlan,
  type StudyPlan,
  type StudySubject,
} from "@/lib/exam-mem-study-plans";

type Mode = "profile" | "review";
type Tr = (zh: string, en: string) => string;

export default function LearningInsightsWorkbench({ mode }: { mode: Mode }) {
  const searchParams = useSearchParams();
  const { i18n } = useTranslation();
  const tr = useCallback<Tr>(
    (zh, en) => (i18n.language.startsWith("zh") ? zh : en),
    [i18n.language],
  );
  const [plans, setPlans] = useState<StudyPlan[]>([]);
  const [planId, setPlanId] = useState("");
  const [versionNumber, setVersionNumber] = useState<number | null>(null);
  const [subjectId, setSubjectId] = useState("");
  const [profile, setProfile] = useState<LearningProfile | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reviewFilter, setReviewFilter] = useState<"all" | "due" | "upcoming">("all");

  useEffect(() => {
    void listStudyPlans("active")
      .then((items) => {
        const published = items.filter((item) => item.versions.length > 0);
        const requested = searchParams.get("plan") || searchParams.get("plan_id");
        setPlans(published);
        setPlanId(
          published.some((item) => item.plan_id === requested)
            ? requested || ""
            : published[0]?.plan_id || "",
        );
      })
      .catch((cause) => setError(cause instanceof Error ? cause.message : String(cause)));
  }, [searchParams]);

  const plan = plans.find((item) => item.plan_id === planId) ?? null;
  const versions = useMemo(() => plan?.versions ?? [], [plan]);
  const version =
    versions.find((item) => item.version === versionNumber) ?? versions[0] ?? null;
  const subjects = useMemo(() => version?.tree.subjects ?? [], [version]);
  const subject = subjects.find((item) => item.id === subjectId) ?? subjects[0] ?? null;

  useEffect(() => {
    setVersionNumber(plan?.active_version ?? versions[0]?.version ?? null);
  }, [plan?.active_version, planId, versions]);
  useEffect(() => {
    const requested = searchParams.get("subject");
    setSubjectId((current) =>
      subjects.some((item) => item.id === requested)
        ? requested || ""
        : subjects.some((item) => item.id === current)
          ? current
          : subjects[0]?.id || "",
    );
  }, [searchParams, subjects]);

  const load = useCallback(async () => {
    if (!plan || !version || !subject) {
      setProfile(null);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      setProfile(
        await getLearningProfile({
          examId: `plan:${plan.plan_id}`,
          subjectId: subject.id,
          taxonomyVersion: version.taxonomy_versions[subject.id],
        }),
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  }, [plan, subject, version]);
  useEffect(() => void load(), [load]);

  return (
    <div className="mx-auto max-w-7xl space-y-6 px-4 py-6 sm:px-6 sm:py-8 md:px-10">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <span className="grid h-11 w-11 place-items-center rounded-xl bg-[var(--primary)]/10 text-[var(--primary)]">
            {mode === "profile" ? (
              <BrainCircuit className="h-5 w-5" />
            ) : (
              <CalendarClock className="h-5 w-5" />
            )}
          </span>
          <div>
            <h1 className="font-serif text-2xl font-semibold">
              {mode === "profile" ? tr("学习画像", "Learning profile") : tr("复习中心", "Review center")}
            </h1>
            <p className="mt-1 max-w-3xl text-sm text-[var(--muted-foreground)]">
              {mode === "profile"
                ? tr(
                    "从正式作答证据和版本化学习记忆分析掌握结构、薄弱点与趋势，每个结论都可追溯。",
                    "Analyze mastery, weaknesses and trends from formal attempts and versioned Learning Memory, with traceable evidence.",
                  )
                : tr(
                    "根据掌握度、稳定错因和遗忘风险决定何时复习、复习什么，并进入针对性练习。",
                    "Use mastery, stable errors and forgetting risk to decide what to review and when, then start targeted practice.",
                  )}
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          disabled={loading || !subject}
          className="inline-flex items-center gap-2 rounded-lg border border-[var(--border)] px-3 py-2 text-sm disabled:opacity-50"
        >
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          {tr("刷新", "Refresh")}
        </button>
      </header>

      <ScopeFilters
        plans={plans}
        planId={planId}
        setPlanId={setPlanId}
        versions={versions}
        version={version}
        setVersionNumber={setVersionNumber}
        subjects={subjects}
        subject={subject}
        setSubjectId={setSubjectId}
        tr={tr}
      />

      {!plans.length ? (
        <Empty text={tr("请先在学习路径中导入并发布学习计划。", "Import and publish a study plan first.")} />
      ) : null}
      {error ? (
        <p className="flex items-center gap-2 rounded-lg bg-red-500/10 px-3 py-2 text-sm text-red-600">
          <CircleAlert className="h-4 w-4" />
          {error}
        </p>
      ) : null}
      {loading && !profile ? <Empty text={tr("正在分析学习记忆…", "Analyzing Learning Memory…")} /> : null}
      {profile && mode === "profile" ? (
        <ProfileView profile={profile} planId={planId} subjectId={subject?.id || ""} tr={tr} />
      ) : null}
      {profile && mode === "review" ? (
        <ReviewView
          profile={profile}
          planId={planId}
          subjectId={subject?.id || ""}
          filter={reviewFilter}
          setFilter={setReviewFilter}
          tr={tr}
        />
      ) : null}

      <div className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-700 dark:text-amber-300">
        <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0" />
        <span>
          {tr(
            "画像和复习队列是 L1/L2/L3 的可重建视图，不会改写历史证据；Agent 负责解释，确定性策略负责排序。",
            "The profile and review queue are rebuildable L1/L2/L3 views. The Agent explains; a deterministic policy ranks.",
          )}
        </span>
      </div>
    </div>
  );
}

function ScopeFilters(props: {
  plans: StudyPlan[];
  planId: string;
  setPlanId: (value: string) => void;
  versions: PublishedStudyPlan[];
  version: PublishedStudyPlan | null;
  setVersionNumber: (value: number) => void;
  subjects: StudySubject[];
  subject: StudySubject | null;
  setSubjectId: (value: string) => void;
  tr: Tr;
}) {
  return (
    <section className="grid gap-3 rounded-xl border border-[var(--border)] bg-[var(--card)] p-4 md:grid-cols-3">
      <Select label={props.tr("学习计划", "Study plan")} value={props.planId} onChange={props.setPlanId}>
        {props.plans.map((item) => <option key={item.plan_id} value={item.plan_id}>{item.name}</option>)}
      </Select>
      <Select label={props.tr("大纲版本", "Syllabus version")} value={String(props.version?.version ?? "")} onChange={(value) => props.setVersionNumber(Number(value))}>
        {props.versions.map((item) => <option key={item.version} value={item.version}>v{item.version} · {new Date(item.published_at).toLocaleDateString()}</option>)}
      </Select>
      <Select label={props.tr("考试科目", "Subject")} value={props.subject?.id || ""} onChange={props.setSubjectId}>
        {props.subjects.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
      </Select>
      <p className="text-xs text-[var(--muted-foreground)] md:col-span-3">
        {props.tr(
          "大纲版本决定当前展示的知识结构；正式学习记忆按学习计划与科目连续追踪，不会因发布新版大纲而丢失。",
          "The syllabus version selects the visible knowledge structure; formal memory remains continuous within the study plan and subject.",
        )}
      </p>
    </section>
  );
}

function Select(props: { label: string; value: string; onChange: (value: string) => void; children: React.ReactNode }) {
  return (
    <label className="text-xs text-[var(--muted-foreground)]">
      {props.label}
      <select value={props.value} onChange={(event) => props.onChange(event.target.value)} className="mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-sm text-[var(--foreground)]">
        {props.children}
      </select>
    </label>
  );
}

function ProfileView(props: { profile: LearningProfile; planId: string; subjectId: string; tr: Tr }) {
  const { profile, tr } = props;
  const summary = profile.summary;
  const weak = profile.knowledge_points.filter((item) => item.status === "weak" || item.status === "contested");
  return (
    <div className="space-y-5">
      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <Metric label={tr("覆盖率", "Coverage")} value={percent(summary.coverage_rate)} />
        <Metric label={tr("掌握率", "Mastery")} value={percent(summary.mastery_rate)} />
        <Metric label={tr("正式作答正确率", "Formal accuracy")} value={optionalPercent(summary.accuracy)} />
        <Metric label={tr("薄弱点", "Weak points")} value={String(summary.weak_count)} />
        <Metric label={tr("到期复习", "Due reviews")} value={String(summary.due_count)} />
      </section>

      <section className="rounded-xl border border-[var(--primary)]/30 bg-[var(--primary)]/5 p-5">
        <div className="flex items-center gap-2 font-semibold"><BrainCircuit className="h-5 w-5 text-[var(--primary)]" />{tr("Agent 画像解读", "Agent profile briefing")}</div>
        <p className="mt-3 text-sm leading-6">{profileBriefing(profile, tr)}</p>
        <p className="mt-2 text-xs text-[var(--muted-foreground)]">
          {tr(`画像策略 ${profile.policy_version} · L3 投影 ${profile.projection_version ?? "尚未生成"}`, `Policy ${profile.policy_version} · L3 projection ${profile.projection_version ?? "not built"}`)}
        </p>
      </section>

      <section className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="font-semibold">{tr("知识点掌握结构", "Knowledge mastery map")}</h2>
          {weak.length ? <Link href={`/exam-mem/review-center?plan=${encodeURIComponent(props.planId)}&subject=${encodeURIComponent(props.subjectId)}`} className="inline-flex items-center gap-1 text-sm text-[var(--primary)]">{tr("查看复习安排", "Open review plan")}<ArrowRight className="h-4 w-4" /></Link> : null}
        </div>
        <div className="grid gap-3 lg:grid-cols-2">
          {profile.knowledge_points.map((item) => (
            <KnowledgeCard key={item.knowledge_point_id} item={item} planId={props.planId} subjectId={props.subjectId} tr={tr} />
          ))}
        </div>
      </section>
    </div>
  );
}

function KnowledgeCard(props: { item: KnowledgePointProfile; planId: string; subjectId: string; tr: Tr }) {
  const { item, tr } = props;
  return (
    <article className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0"><p className="truncate font-medium">{item.name}</p><p className="mt-1 text-xs text-[var(--muted-foreground)]">{item.module_name} · {statusLabel(item.status, tr)}</p></div>
        <StatusDot status={item.status} />
      </div>
      <div className="mt-3 grid grid-cols-3 gap-2 text-xs">
        <Mini label={tr("掌握度", "Mastery")} value={optionalPercent(item.mastery_score)} />
        <Mini label={tr("正确率", "Accuracy")} value={optionalPercent(item.accuracy)} />
        <Mini label={tr("作答", "Attempts")} value={String(item.attempts)} />
      </div>
      {item.error_types.length ? <p className="mt-3 text-xs text-amber-600">{tr("稳定错因", "Stable errors")}: {item.error_types.map((value) => reasonLabel(value, tr)).join("、")}</p> : null}
      <div className="mt-3 flex flex-wrap gap-2">
        {item.source_memory_ids[0] ? <Link href={`/exam-mem/memories?view=l2&plan_id=${encodeURIComponent(props.planId)}&subject_id=${encodeURIComponent(props.subjectId)}&memory_id=${encodeURIComponent(item.source_memory_ids[0])}`} className="rounded-lg border border-[var(--border)] px-3 py-1.5 text-xs">{tr("查看记忆证据", "Inspect evidence")}</Link> : null}
        {(item.status === "weak" || item.status === "contested") ? <PracticeLink planId={props.planId} subjectId={props.subjectId} knowledgePointId={item.knowledge_point_id} tr={tr} /> : null}
      </div>
    </article>
  );
}

function ReviewView(props: { profile: LearningProfile; planId: string; subjectId: string; filter: "all" | "due" | "upcoming"; setFilter: (value: "all" | "due" | "upcoming") => void; tr: Tr }) {
  const { profile, tr } = props;
  const items = profile.review_queue.filter((item) => props.filter === "all" || (props.filter === "due" ? item.status !== "upcoming" : item.status === "upcoming"));
  return (
    <div className="space-y-5">
      <section className="grid gap-3 sm:grid-cols-3">
        <Metric label={tr("现在需要复习", "Due now")} value={String(profile.review_queue.filter((item) => item.status === "due").length)} />
        <Metric label={tr("尚未检测", "Unassessed")} value={String(profile.review_queue.filter((item) => item.status === "unassessed").length)} />
        <Metric label={tr("后续复习", "Upcoming")} value={String(profile.review_queue.filter((item) => item.status === "upcoming").length)} />
      </section>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="font-semibold">{tr("复习优先队列", "Review priority queue")}</h2>
        <div className="flex rounded-lg bg-[var(--muted)]/50 p-1">
          {(["all", "due", "upcoming"] as const).map((value) => <button key={value} type="button" onClick={() => props.setFilter(value)} className={`rounded-md px-3 py-1.5 text-xs ${props.filter === value ? "bg-[var(--background)] font-medium shadow-sm" : "text-[var(--muted-foreground)]"}`}>{value === "all" ? tr("全部", "All") : value === "due" ? tr("当前", "Due") : tr("稍后", "Upcoming")}</button>)}
        </div>
      </div>
      <section className="space-y-3">
        {items.map((item, index) => <ReviewCard key={item.knowledge_point_id} item={item} rank={index + 1} planId={props.planId} subjectId={props.subjectId} tr={tr} />)}
        {!items.length ? <Empty text={tr("当前筛选下没有复习任务。", "No review tasks match this filter.")} /> : null}
      </section>
    </div>
  );
}

function ReviewCard(props: { item: ReviewQueueItem; rank: number; planId: string; subjectId: string; tr: Tr }) {
  const { item, tr } = props;
  return (
    <article className="grid gap-4 rounded-xl border border-[var(--border)] bg-[var(--card)] p-4 md:grid-cols-[auto_minmax(0,1fr)_auto] md:items-center">
      <span className="grid h-9 w-9 place-items-center rounded-full bg-[var(--primary)]/10 text-sm font-semibold text-[var(--primary)]">{props.rank}</span>
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2"><p className="font-medium">{item.name}</p><span className="rounded-full bg-[var(--muted)] px-2 py-0.5 text-[11px]">{item.status === "due" ? tr("已到期", "Due") : item.status === "unassessed" ? tr("尚未检测", "Unassessed") : tr("计划中", "Upcoming")}</span></div>
        <p className="mt-1 text-xs text-[var(--muted-foreground)]">{item.module_name} · {tr("优先级", "Priority")} {percent(item.priority)} · {reviewTime(item, tr)}</p>
        <p className="mt-2 text-sm">{item.reason_codes.map((reason) => reasonLabel(reason, tr)).join(" · ")}</p>
      </div>
      <div className="flex flex-wrap gap-2 md:justify-end">
        {item.source_memory_ids[0] ? <Link href={`/exam-mem/memories?view=l2&plan_id=${encodeURIComponent(props.planId)}&subject_id=${encodeURIComponent(props.subjectId)}&memory_id=${encodeURIComponent(item.source_memory_ids[0])}`} className="rounded-lg border border-[var(--border)] px-3 py-2 text-xs">{tr("依据", "Evidence")}</Link> : null}
        <PracticeLink planId={props.planId} subjectId={props.subjectId} knowledgePointId={item.knowledge_point_id} tr={tr} />
      </div>
    </article>
  );
}

function PracticeLink(props: { planId: string; subjectId: string; knowledgePointId: string; tr: Tr }) {
  return <Link href={`/exam-mem/practice?plan=${encodeURIComponent(props.planId)}&subject=${encodeURIComponent(props.subjectId)}&kp=${encodeURIComponent(props.knowledgePointId)}`} className="inline-flex items-center gap-1 rounded-lg bg-[var(--primary)] px-3 py-1.5 text-xs font-medium text-[var(--primary-foreground)]"><Target className="h-3.5 w-3.5" />{props.tr("薄弱点专项练习", "Targeted practice")}</Link>;
}

function Metric({ label, value }: { label: string; value: string }) {
  return <article className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-4"><p className="text-xs text-[var(--muted-foreground)]">{label}</p><p className="mt-2 text-2xl font-semibold">{value}</p></article>;
}

function Mini({ label, value }: { label: string; value: string }) {
  return <div className="rounded-lg bg-[var(--muted)]/40 p-2"><p className="text-[var(--muted-foreground)]">{label}</p><p className="mt-1 font-medium">{value}</p></div>;
}

function StatusDot({ status }: { status: KnowledgePointProfile["status"] }) {
  if (status === "mastered") return <CheckCircle2 className="h-5 w-5 text-emerald-500" />;
  if (status === "weak" || status === "contested") return <TrendingDown className="h-5 w-5 text-amber-500" />;
  if (status === "developing") return <TrendingUp className="h-5 w-5 text-blue-500" />;
  return <Clock3 className="h-5 w-5 text-[var(--muted-foreground)]" />;
}

function Empty({ text }: { text: string }) {
  return <div className="rounded-xl border border-dashed border-[var(--border)] p-10 text-center text-sm text-[var(--muted-foreground)]">{text}</div>;
}

function percent(value: number): string { return `${Math.round(value * 100)}%`; }
function optionalPercent(value: number | null): string { return value === null ? "—" : percent(value); }

function statusLabel(status: KnowledgePointProfile["status"], tr: Tr): string {
  return { unassessed: tr("尚未检测", "Unassessed"), developing: tr("学习中", "Developing"), weak: tr("薄弱", "Weak"), mastered: tr("已掌握", "Mastered"), contested: tr("证据冲突", "Contested") }[status];
}

function reasonLabel(reason: string, tr: Tr): string {
  return ({ weakness: tr("当前掌握薄弱", "Current weakness"), contested_evidence: tr("证据存在冲突，建议重新验证", "Conflicting evidence needs verification"), stable_error: tr("存在稳定重复错因", "Stable recurring error"), forgetting_risk: tr("已达到复习时间", "Review is due"), active_plan_priority: tr("当前学习计划优先", "Active plan priority"), coverage_gap: tr("尚无正式检测证据", "No formal assessment evidence"), scheduled_review: tr("按掌握水平安排复习", "Scheduled by mastery level"), concept_confusion: tr("概念混淆", "Concept confusion"), formula_misuse: tr("公式误用", "Formula misuse"), condition_omission: tr("条件遗漏", "Condition omission"), calculation_error: tr("计算错误", "Calculation error"), reasoning_gap: tr("推理缺口", "Reasoning gap"), reading_error: tr("审题错误", "Reading error"), careless_error: tr("粗心错误", "Careless error"), unknown: tr("未分类错因", "Unclassified error") } as Record<string, string>)[reason] || reason;
}

function reviewTime(item: ReviewQueueItem, tr: Tr): string {
  if (item.status === "unassessed") return tr("建议现在进行首次检测", "Take the first assessment now");
  if (item.status === "due") return tr("建议现在复习", "Review now");
  return tr(`建议 ${new Date(item.due_at).toLocaleDateString()} 复习`, `Review on ${new Date(item.due_at).toLocaleDateString()}`);
}

function profileBriefing(profile: LearningProfile, tr: Tr): string {
  const { summary } = profile;
  if (!summary.total_attempts) return tr("目前还没有正式作答证据。建议先从尚未检测的知识点开始专项练习，建立第一版可信画像。", "There is no formal assessment evidence yet. Start with an unassessed objective to establish the first reliable profile.");
  const trend = summary.trend === "improving" ? tr("近期表现正在提升", "Recent performance is improving") : summary.trend === "declining" ? tr("近期表现有所下降", "Recent performance is declining") : summary.trend === "stable" ? tr("近期表现基本稳定", "Recent performance is stable") : tr("当前证据还不足以判断趋势", "There is not enough evidence for a trend yet");
  const priority = profile.review_queue.find((item) => item.status !== "upcoming");
  return tr(`已覆盖 ${summary.assessed_count}/${summary.knowledge_point_count} 个知识点，识别出 ${summary.weak_count} 个薄弱或待验证知识点，${trend}。${priority ? `当前最优先复习“${priority.name}”，原因是：${priority.reason_codes.map((item) => reasonLabel(item, tr)).join("、")}。` : "当前没有到期任务。"}`, `${summary.assessed_count}/${summary.knowledge_point_count} objectives have formal evidence, with ${summary.weak_count} weak or contested. ${trend}. ${priority ? `The top review priority is “${priority.name}”: ${priority.reason_codes.map((item) => reasonLabel(item, tr)).join(", ")}.` : "Nothing is due now."}`);
}
