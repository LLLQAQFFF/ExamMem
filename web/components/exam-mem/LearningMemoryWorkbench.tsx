"use client";

import {
  AlertTriangle,
  Brain,
  Check,
  Database,
  Layers,
  MessageSquareText,
  Network,
  RefreshCw,
  ShieldCheck,
  Workflow,
  X,
} from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import MemoryGraph from "@/components/memory/MemoryGraph";
import ThreeLayerMemoryOverview from "@/components/memory/ThreeLayerMemoryOverview";
import MemoryIssuesWorkbench from "@/components/exam-mem/MemoryIssuesWorkbench";
import {
  actOnObservation,
  analyzeConversation,
  buildLearningArchiveGraph,
  getLearningArchive,
  listChatObservations,
  listConversations,
  type ConversationSummary,
  type LearningArchive,
  type LearningArchiveMemory,
  type LearningObservation,
} from "@/lib/exam-mem-learning-archive";
import {
  cancelLearningPlan,
  correctLearningMemory,
  memoryValueSummary,
} from "@/lib/exam-mem-memory";
import {
  listStudyPlans,
  type PublishedStudyPlan,
  type StudyModule,
  type StudyObjective,
  type StudyPlan,
  type StudySubject,
} from "@/lib/exam-mem-study-plans";

type View = "overview" | "l1" | "l2" | "l3" | "chat" | "graph";
type Tr = (zh: string, en: string) => string;
const VIEWS: View[] = ["overview", "l1", "l2", "l3", "chat", "graph"];

export default function LearningMemoryWorkbench() {
  const searchParams = useSearchParams();
  const { i18n } = useTranslation();
  const zh = i18n.language.startsWith("zh");
  const tr = useCallback<Tr>(
    (cn, en) => (i18n.language.startsWith("zh") ? cn : en),
    [i18n.language],
  );
  const requestedView = searchParams.get("view") as View | null;
  const [view, setView] = useState<View>(
    requestedView && VIEWS.includes(requestedView) ? requestedView : "overview",
  );
  const [plans, setPlans] = useState<StudyPlan[]>([]);
  const [planId, setPlanId] = useState("");
  const [versionNumber, setVersionNumber] = useState<number | null>(null);
  const [subjectId, setSubjectId] = useState("");
  const [moduleId, setModuleId] = useState("");
  const [knowledgePointId, setKnowledgePointId] = useState("");
  const [namespace, setNamespace] = useState("");
  const [lifecycleState, setLifecycleState] = useState("");
  const [archive, setArchive] = useState<LearningArchive | null>(null);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [conversationId, setConversationId] = useState("");
  const [chatClues, setChatClues] = useState<LearningObservation[]>([]);
  const [selectedMemory, setSelectedMemory] =
    useState<LearningArchiveMemory | null>(null);
  const [correction, setCorrection] = useState("");
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (requestedView && VIEWS.includes(requestedView)) setView(requestedView);
  }, [requestedView]);

  useEffect(() => {
    void listStudyPlans()
      .then((items) => {
        const published = items.filter((item) => item.versions.length > 0);
        setPlans(published);
        setPlanId((current) => current || published[0]?.plan_id || "");
      })
      .catch((cause) =>
        setError(cause instanceof Error ? cause.message : String(cause)),
      );
  }, []);

  const plan = plans.find((item) => item.plan_id === planId) ?? null;
  const versions = useMemo(() => plan?.versions ?? [], [plan]);
  const version =
    versions.find((item) => item.version === versionNumber) ??
    versions[0] ??
    null;
  const subjects = useMemo(() => version?.tree.subjects ?? [], [version]);
  const subject =
    subjects.find((item) => item.id === subjectId) ?? subjects[0] ?? null;
  const modules = useMemo(() => subject?.modules ?? [], [subject]);
  const selectedModule =
    modules.find((item) => item.id === moduleId) ?? modules[0] ?? null;
  const knowledgePoints = useMemo(
    () => selectedModule?.knowledge_points ?? [],
    [selectedModule],
  );

  useEffect(() => {
    setVersionNumber(plan?.active_version ?? versions[0]?.version ?? null);
  }, [planId, plan?.active_version, versions]);
  useEffect(() => {
    setSubjectId((current) =>
      subjects.some((item) => item.id === current)
        ? current
        : subjects[0]?.id || "",
    );
  }, [versionNumber, subjects]);
  useEffect(() => {
    setModuleId((current) =>
      modules.some((item) => item.id === current)
        ? current
        : modules[0]?.id || "",
    );
    setKnowledgePointId("");
  }, [subjectId, modules]);

  const scope = useMemo(() => {
    if (!plan || !version || !subject) return null;
    return {
      examId: `plan:${plan.plan_id}`,
      subjectId: subject.id,
      taxonomyVersion: version.taxonomy_versions[subject.id],
      knowledgePointIds: knowledgePointId
        ? [knowledgePointId]
        : knowledgePoints.map((item) => item.id),
      namespaces: namespace ? [namespace] : undefined,
      lifecycleStates: lifecycleState ? [lifecycleState] : undefined,
    };
  }, [knowledgePointId, knowledgePoints, lifecycleState, namespace, plan, subject, version]);

  const load = useCallback(async () => {
    if (!scope) {
      setArchive(null);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const [nextArchive, clues] = await Promise.all([
        getLearningArchive(scope),
        listChatObservations(scope),
      ]);
      setArchive(nextArchive);
      setChatClues(clues);
      setSelectedMemory(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  }, [scope]);
  useEffect(() => void load(), [load]);

  useEffect(() => {
    if (view !== "chat") return;
    void listConversations()
      .then((items) => {
        setConversations(items);
        setConversationId((current) => current || items[0]?.session_id || "");
      })
      .catch((cause) =>
        setError(cause instanceof Error ? cause.message : String(cause)),
      );
  }, [view]);

  const memoriesBySlot = useMemo(() => {
    const groups = new Map<string, LearningArchiveMemory[]>();
    for (const item of archive?.l2 ?? []) {
      const key = `${item.memory.scope.memory_namespace}:${item.memory.slot_key}`;
      groups.set(key, [...(groups.get(key) ?? []), item]);
    }
    for (const group of groups.values())
      group.sort((a, b) => b.memory.version - a.memory.version);
    return [...groups.values()];
  }, [archive]);

  const analyze = async () => {
    if (!scope || !conversationId) return;
    setBusy(true);
    setError(null);
    try {
      await analyzeConversation({
        sessionId: conversationId,
        examId: scope.examId,
        subjectId: scope.subjectId,
        taxonomyVersion: scope.taxonomyVersion,
        language: zh ? "zh" : "en",
      });
      setChatClues(await listChatObservations(scope));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  const action = async (
    observation: LearningObservation,
    next: "confirm" | "dismiss",
  ) => {
    setBusy(true);
    try {
      await actOnObservation(observation.observation_id, next);
      if (scope) setChatClues(await listChatObservations(scope));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  const submitCorrection = async () => {
    if (!scope || !selectedMemory || !correction.trim()) return;
    setBusy(true);
    try {
      const memory = selectedMemory.memory;
      const common = {
        memoryId: memory.memory_id,
        examId: scope.examId,
        subjectId: scope.subjectId,
        idempotencyKey: `memory:web:${crypto.randomUUID()}`,
      };
      if (memory.scope.memory_namespace === "plan") {
        await cancelLearningPlan({ ...common, reason: correction.trim() });
      } else {
        await correctLearningMemory({
          ...common,
          namespace: memory.scope.memory_namespace,
          statement: correction.trim(),
        });
      }
      setCorrection("");
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  const loadGraph = useCallback(async () => {
    if (!archive)
      throw new Error(
        tr(
          "请先选择已发布的学习计划。",
          "Select a published study plan first.",
        ),
      );
    return buildLearningArchiveGraph(archive);
  }, [archive, tr]);

  return (
    <div className="mx-auto max-w-7xl space-y-6 px-4 py-6 sm:px-6 sm:py-8 md:px-10 md:py-10">
      <header className="space-y-3">
        <div className="flex items-center gap-3">
          <span className="grid h-10 w-10 place-items-center rounded-xl bg-[var(--primary)]/10 text-[var(--primary)]">
            <Brain className="h-5 w-5" />
          </span>
          <div>
            <h1 className="font-serif text-2xl font-semibold">
              {tr("学习档案", "Learning archive")}
            </h1>
            <p className="text-sm text-[var(--muted-foreground)]">
              {tr(
                "用 DeepTutor 原生三层记忆的交互查看 ExamMem 正式学习证据、版本链和可重建画像。",
                "Use the native three-layer Memory interaction to inspect ExamMem evidence, version chains and rebuildable projections.",
              )}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2 rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-700 dark:text-amber-300">
          <AlertTriangle className="h-4 w-4 shrink-0" />
          {tr(
            "普通聊天线索与正式刷题证据严格分区；确认聊天线索也不会改变掌握度或判题。",
            "Chat clues are isolated from formal practice evidence; confirming one never changes mastery or grading.",
          )}
        </div>
      </header>

      <ScopeFilters
        plans={plans}
        planId={planId}
        setPlanId={setPlanId}
        versions={versions}
        versionNumber={version?.version ?? null}
        setVersionNumber={setVersionNumber}
        subjects={subjects}
        subjectId={subject?.id ?? ""}
        setSubjectId={setSubjectId}
        modules={modules}
        moduleId={selectedModule?.id ?? ""}
        setModuleId={setModuleId}
        knowledgePoints={knowledgePoints}
        knowledgePointId={knowledgePointId}
        setKnowledgePointId={setKnowledgePointId}
        namespace={namespace}
        setNamespace={setNamespace}
        lifecycleState={lifecycleState}
        setLifecycleState={setLifecycleState}
        loading={loading}
        refresh={load}
        tr={tr}
      />

      {plans.length === 0 ? (
        <Empty
          text={tr(
            "还没有已发布的学习计划。请先在“学习路径”导入大纲并发布。",
            "No published study plan yet. Import and publish a syllabus in Learning Paths first.",
          )}
        />
      ) : null}
      {error ? (
        <p className="rounded-lg bg-red-500/10 px-3 py-2 text-sm text-red-600">
          {error}
        </p>
      ) : null}

      <nav className="flex flex-wrap gap-2">
        {VIEWS.map((item) => (
          <button
            key={item}
            type="button"
            onClick={() => setView(item)}
            className={`rounded-lg border px-3 py-2 text-sm ${view === item ? "border-[var(--primary)] bg-[var(--primary)]/10 text-[var(--primary)]" : "border-[var(--border)]"}`}
          >
            {
              {
                overview: tr("总览", "Overview"),
                l1: "L1",
                l2: "L2",
                l3: "L3",
                chat: tr("对话线索", "Chat clues"),
                graph: tr("记忆图谱", "Graph"),
              }[item]
            }
          </button>
        ))}
      </nav>

      {view === "overview" && archive ? (
        <ArchiveOverview archive={archive} tr={tr} />
      ) : null}
      {view === "l1" ? <L1View archive={archive} tr={tr} /> : null}
      {view === "l2" ? (
        <L2View
          groups={memoriesBySlot}
          selected={selectedMemory}
          setSelected={setSelectedMemory}
          correction={correction}
          setCorrection={setCorrection}
          submit={submitCorrection}
          busy={busy}
          tr={tr}
        />
      ) : null}
      {view === "l3" ? <L3View archive={archive} tr={tr} /> : null}
      {view === "chat" ? (
        <ChatView
          conversations={conversations}
          conversationId={conversationId}
          setConversationId={setConversationId}
          observations={chatClues}
          analyze={analyze}
          action={action}
          busy={busy}
          tr={tr}
        />
      ) : null}
      {view === "graph" && archive ? (
        <div className="h-[720px] overflow-hidden rounded-2xl border border-[var(--border)]">
          <MemoryGraph
            loadGraph={loadGraph}
            backHref="/exam-mem/memories"
            backLabel={tr("学习档案", "Learning archive")}
            title={tr("学习记忆图谱", "Learning Memory graph")}
            description={tr(
              "L3 当前画像位于中心，L2 版本记忆位于中环，L1 正式证据位于外环。",
              "Current L3 model at the centre, versioned L2 memories in the middle and formal L1 evidence outside.",
            )}
          />
        </div>
      ) : null}
      <MemoryIssuesWorkbench embedded />
    </div>
  );
}

function ArchiveOverview({ archive, tr }: { archive: LearningArchive; tr: Tr }) {
  return (
    <ThreeLayerMemoryOverview
      layers={[
        {
          href: "/exam-mem/memories?view=l1",
          icon: Layers,
          title: tr("L1 · 正式学习证据", "L1 · Formal evidence"),
          tag: tr("只追加", "Append only"),
          stat: String(archive.counts.l1),
          statLabel: tr("条记录", "records"),
          detail: tr(
            "刷题作答、纠正和计划转换；来源可追溯，永不原地改写。学习路径 Agent 侧记单独标识。",
            "Practice attempts, corrections and plan transitions with immutable provenance; learning-path Agent notes are labelled separately.",
          ),
        },
        {
          href: "/exam-mem/memories?view=l2",
          icon: Workflow,
          title: tr("L2 · 分知识点记忆", "L2 · Per-point memories"),
          tag: tr("有版本", "Versioned"),
          stat: String(archive.counts.l2),
          statLabel: tr("个版本", "versions"),
          detail: tr(
            "掌握度、错因和计划按四维 Scope 隔离，展示当前值和完整历史版本。",
            "Mastery, errors and plans isolated by Scope, with current values and full history.",
          ),
        },
        {
          href: "/exam-mem/memories?view=l3",
          icon: Network,
          title: tr("L3 · 跨模块学习画像", "L3 · Cross-module model"),
          tag: tr("可重建", "Rebuildable"),
          stat: String(archive.counts.l3),
          statLabel: tr("项投影", "projections"),
          detail: tr(
            "薄弱点、已掌握点、稳定错因和活跃计划；首版只展示当前投影。",
            "Weak/mastered points, stable errors and active plans; v1 shows the current projection only.",
          ),
        },
      ]}
      graph={{
        href: "/exam-mem/memories?view=graph",
        title: tr("学习记忆图谱", "Learning Memory graph"),
        tag: tr("新", "New"),
        detail: tr(
          "中心是 L3 综合画像，中环是 L2 版本记忆，外环是 L1 正式证据。",
          "L3 projection in the centre, L2 memories in the middle, and L1 evidence outside.",
        ),
      }}
    />
  );
}

function ScopeFilters(props: {
  plans: StudyPlan[];
  planId: string;
  setPlanId: (value: string) => void;
  versions: PublishedStudyPlan[];
  versionNumber: number | null;
  setVersionNumber: (value: number) => void;
  subjects: StudySubject[];
  subjectId: string;
  setSubjectId: (value: string) => void;
  modules: StudyModule[];
  moduleId: string;
  setModuleId: (value: string) => void;
  knowledgePoints: StudyObjective[];
  knowledgePointId: string;
  setKnowledgePointId: (value: string) => void;
  namespace: string;
  setNamespace: (value: string) => void;
  lifecycleState: string;
  setLifecycleState: (value: string) => void;
  loading: boolean;
  refresh: () => Promise<void>;
  tr: Tr;
}) {
  const select =
    "w-full min-w-0 rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-sm";
  return (
    <section className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,13rem),1fr))] gap-3 rounded-xl border border-[var(--border)] bg-[var(--card)] p-4">
      <Filter label={props.tr("学习计划 / 专业", "Study plan / programme")}>
        <select
          className={select}
          value={props.planId}
          onChange={(event) => props.setPlanId(event.target.value)}
        >
          <option value="">—</option>
          {props.plans.map((item) => (
            <option key={item.plan_id} value={item.plan_id}>
              {item.name}
            </option>
          ))}
        </select>
      </Filter>
      <Filter label={props.tr("大纲版本", "Syllabus version")}>
        <select
          className={select}
          value={props.versionNumber ?? ""}
          onChange={(event) => props.setVersionNumber(Number(event.target.value))}
        >
          {props.versions.map((item) => (
            <option key={item.version} value={item.version}>
              v{item.version} · {new Date(item.published_at).toLocaleDateString()}
            </option>
          ))}
        </select>
      </Filter>
      <Filter label={props.tr("考试科目", "Subject")}>
        <select
          className={select}
          value={props.subjectId}
          onChange={(event) => props.setSubjectId(event.target.value)}
        >
          {props.subjects.map((item) => (
            <option key={item.id} value={item.id}>
              {item.name}
            </option>
          ))}
        </select>
      </Filter>
      <Filter label={props.tr("模块 / 章节", "Module / chapter")}>
        <select
          className={select}
          value={props.moduleId}
          onChange={(event) => props.setModuleId(event.target.value)}
        >
          {props.modules.map((item) => (
            <option key={item.id} value={item.id}>
              {item.name}
            </option>
          ))}
        </select>
      </Filter>
      <Filter label={props.tr("知识点", "Knowledge point")}>
        <select
          className={select}
          value={props.knowledgePointId}
          onChange={(event) => props.setKnowledgePointId(event.target.value)}
        >
          <option value="">{props.tr("全部知识点", "All knowledge points")}</option>
          {props.knowledgePoints.map((item) => (
            <option key={item.id} value={item.id}>
              {item.name}
            </option>
          ))}
        </select>
      </Filter>
      <Filter label={props.tr("记忆类型", "Memory type")}>
        <select
          className={select}
          value={props.namespace}
          onChange={(event) => props.setNamespace(event.target.value)}
        >
          <option value="">{props.tr("全部", "All")}</option>
          <option value="mastery">{props.tr("掌握度", "Mastery")}</option>
          <option value="error_pattern">{props.tr("错因模式", "Error pattern")}</option>
          <option value="plan">{props.tr("学习计划", "Plan")}</option>
        </select>
      </Filter>
      <Filter label={props.tr("状态", "State")}>
        <select
          className={select}
          value={props.lifecycleState}
          onChange={(event) => props.setLifecycleState(event.target.value)}
        >
          <option value="">{props.tr("全部", "All")}</option>
          {["active", "archived", "invalidated", "contested"].map((item) => (
            <option key={item}>{item}</option>
          ))}
        </select>
      </Filter>
      <div className="flex min-w-0 items-end">
        <button
          type="button"
          onClick={() => void props.refresh()}
          className="inline-flex w-full items-center justify-center gap-2 rounded-lg border border-[var(--border)] px-3 py-2 text-sm"
        >
          <RefreshCw className={`h-4 w-4 ${props.loading ? "animate-spin" : ""}`} />
          {props.tr("刷新", "Refresh")}
        </button>
      </div>
    </section>
  );
}

function Filter({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="grid min-w-0 gap-1 text-xs">
      <span>{label}</span>
      {children}
    </label>
  );
}

function L1View({ archive, tr }: { archive: LearningArchive | null; tr: Tr }) {
  const rows = [
    ...(archive?.l1.map((item) => ({
      id: item.event.event_id,
      at: item.created_at,
      title: item.source?.assessment_title || item.event.event_type,
      detail: `${item.event.question_id || ""} ${
        item.event.answer_correct === true
          ? "✓"
          : item.event.answer_correct === false
            ? "✗"
            : ""
      }`,
      badge: tr("正式刷题", "Formal practice"),
    })) ?? []),
    ...(archive?.learning_path_observations.map((item) => ({
      id: item.observation_id,
      at: item.created_at,
      title: item.summary,
      detail: item.rationale,
      badge: tr("学习路径侧记（非 L1）", "Learning-path note (not L1)"),
    })) ?? []),
  ].sort((a, b) => b.at.localeCompare(a.at));
  if (!rows.length)
    return (
      <Empty
        text={tr(
          "当前筛选范围还没有 L1 学习证据。",
          "No L1 evidence in the selected scope.",
        )}
      />
    );
  return (
    <section className="space-y-3">
      {rows.map((row) => (
        <article
          key={row.id}
          className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-4"
        >
          <div className="flex justify-between gap-3">
            <div>
              <span className="rounded-full bg-[var(--muted)] px-2 py-1 text-xs">
                {row.badge}
              </span>
              <h3 className="mt-2 text-sm font-semibold">{row.title}</h3>
              <p className="mt-1 text-xs text-[var(--muted-foreground)]">
                {row.detail}
              </p>
            </div>
            <time className="text-xs text-[var(--muted-foreground)]">
              {new Date(row.at).toLocaleString()}
            </time>
          </div>
        </article>
      ))}
    </section>
  );
}

function L2View(props: {
  groups: LearningArchiveMemory[][];
  selected: LearningArchiveMemory | null;
  setSelected: (value: LearningArchiveMemory) => void;
  correction: string;
  setCorrection: (value: string) => void;
  submit: () => Promise<void>;
  busy: boolean;
  tr: Tr;
}) {
  const selectedVersions =
    props.groups.find((group) =>
      group.some(
        (item) => item.memory.memory_id === props.selected?.memory.memory_id,
      ),
    ) ?? (props.selected ? [props.selected] : []);
  return (
    <div className="grid gap-5 lg:grid-cols-[1fr_0.85fr]">
      <section className="space-y-3">
        {props.groups.length ? (
          props.groups.map((versions) => {
            const current = versions[0];
            return (
              <button
                type="button"
                key={`${current.memory.scope.memory_namespace}:${current.memory.slot_key}`}
                onClick={() => props.setSelected(current)}
                className="w-full rounded-xl border border-[var(--border)] bg-[var(--card)] p-4 text-left"
              >
                <div className="flex justify-between">
                  <span className="rounded-full bg-[var(--muted)] px-2 py-1 text-xs">
                    {current.memory.scope.memory_namespace}
                  </span>
                  <span className="text-xs">
                    {versions.length} {props.tr("个版本", "versions")}
                  </span>
                </div>
                <p className="mt-3 break-all font-mono text-xs text-[var(--muted-foreground)]">
                  {current.memory.slot_key}
                </p>
                <p className="mt-2 text-sm">
                  {memoryValueSummary(current.memory.value)}
                </p>
              </button>
            );
          })
        ) : (
          <Empty
            text={props.tr(
              "当前筛选范围还没有 L2 记忆。",
              "No L2 memories in the selected scope.",
            )}
          />
        )}
      </section>
      <aside className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-5">
        {props.selected ? (
          <div className="space-y-4">
            <h2 className="font-semibold">
              {props.tr("版本与来源", "Versions and sources")}
            </h2>
            {selectedVersions.map((item) => (
              <button
                type="button"
                key={item.memory.memory_id}
                onClick={() => props.setSelected(item)}
                className="w-full rounded-lg bg-[var(--muted)]/50 p-3 text-left text-xs"
              >
                <p>
                  {`${item.memory.lifecycle_state} · v${item.memory.version} · ${item.sources.length} ${props.tr("条来源", "sources")}`}
                </p>
                <p className="mt-1">{memoryValueSummary(item.memory.value)}</p>
                {item.sources.map((source) => (
                  <p
                    key={`${item.memory.memory_id}:${source.event_id}`}
                    className="mt-1 text-[var(--muted-foreground)]"
                  >
                    {source.assessment_title || source.event_type}
                    {source.assessment_version
                      ? ` · exam v${source.assessment_version}`
                      : ""}
                  </p>
                ))}
              </button>
            ))}
            {props.selected.memory.lifecycle_state === "active" ? (
              <div className="rounded-lg border border-amber-500/30 p-3">
                <div className="flex items-center gap-2 text-sm font-semibold">
                  <ShieldCheck className="h-4 w-4" />
                  {props.tr("追加纠正", "Append correction")}
                </div>
                <textarea
                  value={props.correction}
                  onChange={(event) => props.setCorrection(event.target.value)}
                  rows={3}
                  className="mt-3 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] p-3 text-sm"
                />
                <button
                  type="button"
                  disabled={props.busy || !props.correction.trim()}
                  onClick={() => void props.submit()}
                  className="mt-2 rounded-lg bg-[var(--primary)] px-3 py-2 text-sm text-[var(--primary-foreground)] disabled:opacity-50"
                >
                  {props.tr("确认并追加", "Confirm and append")}
                </button>
              </div>
            ) : null}
          </div>
        ) : (
          <p className="text-sm text-[var(--muted-foreground)]">
            {props.tr(
              "选择一个 L2 卡片查看版本链和来源。",
              "Select an L2 card to inspect versions and provenance.",
            )}
          </p>
        )}
      </aside>
    </div>
  );
}

function L3View({ archive, tr }: { archive: LearningArchive | null; tr: Tr }) {
  const model = archive?.l3?.model;
  if (!model)
    return (
      <Empty
        text={tr(
          "当前 Scope 尚未生成 L3 投影；完成一次正式练习后会自动重建。",
          "No L3 projection yet; formal practice rebuilds it automatically.",
        )}
      />
    );
  const groups = [
    [tr("薄弱点", "Weak points"), model.weak_points],
    [tr("已掌握", "Mastered"), model.mastered_points],
    [tr("稳定错因", "Stable errors"), model.stable_error_patterns],
    [tr("活跃计划", "Active plans"), model.active_plans],
  ] as const;
  return (
    <section className="grid gap-4 md:grid-cols-2">
      {groups.map(([title, items]) => (
        <article
          key={title}
          className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-5"
        >
          <h2 className="font-semibold">{title}</h2>
          <div className="mt-3 space-y-2">
            {items.length ? (
              items.map((item) => (
                <p key={item} className="rounded-lg bg-[var(--muted)]/50 p-3 text-sm">
                  {item}
                </p>
              ))
            ) : (
              <p className="text-sm text-[var(--muted-foreground)]">—</p>
            )}
          </div>
        </article>
      ))}
    </section>
  );
}

function ChatView(props: {
  conversations: ConversationSummary[];
  conversationId: string;
  setConversationId: (value: string) => void;
  observations: LearningObservation[];
  analyze: () => Promise<void>;
  action: (
    item: LearningObservation,
    action: "confirm" | "dismiss",
  ) => Promise<void>;
  busy: boolean;
  tr: Tr;
}) {
  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-end gap-3 rounded-xl border border-[var(--border)] bg-[var(--card)] p-4">
        <label className="grid min-w-[280px] flex-1 gap-1 text-xs">
          <span>{props.tr("选择普通聊天会话", "Select an ordinary chat")}</span>
          <select
            value={props.conversationId}
            onChange={(event) => props.setConversationId(event.target.value)}
            className="rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-sm"
          >
            <option value="">—</option>
            {props.conversations.map((item) => (
              <option key={item.session_id} value={item.session_id}>
                {item.title} · {item.message_count}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          disabled={!props.conversationId || props.busy}
          onClick={() => void props.analyze()}
          className="inline-flex items-center gap-2 rounded-lg bg-[var(--primary)] px-4 py-2 text-sm text-[var(--primary-foreground)] disabled:opacity-50"
        >
          <MessageSquareText className="h-4 w-4" />
          {props.tr("Agent 整理知识线索", "Agent extracts study clues")}
        </button>
      </div>
      <p className="text-xs text-[var(--muted-foreground)]">
        {props.tr(
          "Agent 只分析你主动选择的会话。闲聊会被丢弃；相关内容先进入待确认区，不进入正式 L1/L2/L3。",
          "The Agent only analyzes the conversation you select. Small talk is discarded; relevant content remains pending outside formal L1/L2/L3.",
        )}
      </p>
      {props.observations.length ? (
        props.observations.map((item) => (
          <article
            key={item.observation_id}
            className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-4"
          >
            <div className="flex flex-wrap justify-between gap-3">
              <div>
                <span className="rounded-full bg-[var(--muted)] px-2 py-1 text-xs">
                  {item.status}
                </span>
                <h3 className="mt-2 font-semibold">{item.summary}</h3>
                <p className="mt-1 text-sm text-[var(--muted-foreground)]">
                  {item.rationale}
                </p>
                <p className="mt-2 text-xs">
                  {item.knowledge_point_ids.join(", ")} ·{" "}
                  {Math.round(item.confidence * 100)}%
                </p>
              </div>
              <div className="flex gap-2">
                {item.status !== "confirmed" ? (
                  <button
                    type="button"
                    disabled={props.busy}
                    onClick={() => void props.action(item, "confirm")}
                    className="rounded-lg border border-[var(--border)] p-2"
                    title={props.tr("确认", "Confirm")}
                  >
                    <Check className="h-4 w-4" />
                  </button>
                ) : null}
                {item.status !== "dismissed" ? (
                  <button
                    type="button"
                    disabled={props.busy}
                    onClick={() => void props.action(item, "dismiss")}
                    className="rounded-lg border border-[var(--border)] p-2"
                    title={props.tr("忽略", "Dismiss")}
                  >
                    <X className="h-4 w-4" />
                  </button>
                ) : null}
              </div>
            </div>
          </article>
        ))
      ) : (
        <Empty
          text={props.tr(
            "当前范围没有对话知识线索。",
            "No chat clues for this scope.",
          )}
        />
      )}
    </section>
  );
}

function Empty({ text }: { text: string }) {
  return (
    <div className="rounded-xl border border-dashed border-[var(--border)] p-10 text-center text-sm text-[var(--muted-foreground)]">
      <Database className="mx-auto mb-3 h-5 w-5" />
      {text}
    </div>
  );
}
