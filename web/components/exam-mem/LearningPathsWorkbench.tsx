"use client";

import {
  Archive,
  ArchiveRestore,
  BookOpenCheck,
  Check,
  Circle,
  CircleCheck,
  CircleDot,
  FileUp,
  GraduationCap,
  Link2,
  GitCompareArrows,
  Loader2,
  MessageSquare,
  Plus,
  Save,
  Sparkles,
  X,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { extractBase64FromDataUrl, readFileAsDataUrl } from "@/lib/file-attachments";
import TextbookGroundingPanel from "@/components/exam-mem/TextbookGroundingPanel";
import {
  archiveStudyPlan,
  getStudyPlan,
  importStudyPlan,
  listStudyPlans,
  openStudyObjective,
  publishStudyPlan,
  restoreStudyPlan,
  saveStudyPlanDraft,
  type StudyPlan,
  type StudyPlanTree,
} from "@/lib/exam-mem-study-plans";

type ImportKind = "file" | "url" | "generated";
type ArchiveFilter = "active" | "archived";

const STATUS_ICON = {
  mastered: CircleCheck,
  learning: CircleDot,
  new: Circle,
  unavailable: Circle,
} as const;

export default function LearningPathsWorkbench() {
  const router = useRouter();
  const { i18n } = useTranslation();
  const zh = i18n.language?.toLowerCase().startsWith("zh");
  const tr = useCallback((cn: string, en: string) => (zh ? cn : en), [zh]);
  const [plans, setPlans] = useState<StudyPlan[]>([]);
  const [archiveFilter, setArchiveFilter] = useState<ArchiveFilter>("active");
  const [selectedPlanId, setSelectedPlanId] = useState("");
  const [selectedSubjectId, setSelectedSubjectId] = useState("");
  const [detail, setDetail] = useState<StudyPlan | null>(null);
  const [draftTree, setDraftTree] = useState<StudyPlanTree | null>(null);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showImport, setShowImport] = useState(false);
  const [importKind, setImportKind] = useState<ImportKind>("file");
  const [importName, setImportName] = useState("");
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importUrl, setImportUrl] = useState("");
  const [importRequest, setImportRequest] = useState("");

  const refresh = useCallback(async (preferPlanId?: string) => {
    const items = await listStudyPlans(archiveFilter);
    setPlans(items);
    setSelectedPlanId((current) => {
      const candidate = preferPlanId || current;
      return items.some((item) => item.plan_id === candidate)
        ? candidate
        : items[0]?.plan_id || "";
    });
    return items;
  }, [archiveFilter]);

  useEffect(() => {
    void refresh()
      .catch((cause) => setError(cause instanceof Error ? cause.message : tr("加载学习计划失败。", "Could not load study plans.")))
      .finally(() => setLoading(false));
  }, [refresh, tr]);

  useEffect(() => {
    if (!selectedPlanId) {
      setDetail(null);
      setDraftTree(null);
      return;
    }
    let active = true;
    setLoading(true);
    void getStudyPlan(selectedPlanId)
      .then((plan) => {
        if (!active) return;
        setDetail(plan);
        setDraftTree(plan.draft?.tree ?? null);
        const subjects = (plan.published?.tree ?? plan.draft?.tree)?.subjects ?? [];
        setSelectedSubjectId((current) => subjects.some((item) => item.id === current) ? current : subjects[0]?.id || "");
      })
      .catch((cause) => active && setError(cause instanceof Error ? cause.message : tr("加载学习计划失败。", "Could not load the study plan.")))
      .finally(() => active && setLoading(false));
    return () => { active = false; };
  }, [selectedPlanId, tr]);

  const activeTree = detail?.published?.tree ?? detail?.draft?.tree ?? null;
  const selectedSubject = activeTree?.subjects.find((item) => item.id === selectedSubjectId) ?? activeTree?.subjects[0];
  const publishedSubjects = useMemo(
    () => plans.flatMap((plan) => (plan.published?.tree.subjects ?? []).map((subject) => ({ plan, subject }))),
    [plans],
  );
  const draftPlans = plans.filter((plan) => plan.draft);

  const runImport = async () => {
    if (!importName.trim()) return;
    setWorking(true);
    setError(null);
    try {
      let imported: StudyPlan;
      if (importKind === "file") {
        if (!importFile) throw new Error(tr("请选择大纲或教材文件。", "Choose a syllabus or textbook file."));
        const suffix = importFile.name.toLowerCase().split(".").pop();
        const mimeType = importFile.type || (suffix === "pdf" ? "application/pdf" : suffix === "md" ? "text/markdown" : "text/plain");
        imported = await importStudyPlan({
          name: importName,
          source_kind: "file",
          filename: importFile.name,
          mime_type: mimeType,
          base64: extractBase64FromDataUrl(await readFileAsDataUrl(importFile)),
        });
      } else if (importKind === "url") {
        imported = await importStudyPlan({ name: importName, source_kind: "url", url: importUrl });
      } else {
        imported = await importStudyPlan({ name: importName, source_kind: "generated", request: importRequest });
      }
      setShowImport(false);
      await refresh(imported.plan_id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : tr("导入学习计划失败。", "Study-plan import failed."));
    } finally {
      setWorking(false);
    }
  };

  const saveDraft = async () => {
    if (!detail || !draftTree) return;
    setWorking(true);
    setError(null);
    try {
      const saved = await saveStudyPlanDraft(detail.plan_id, normalizeOrders(draftTree));
      setDetail(saved);
      setDraftTree(saved.draft?.tree ?? null);
      await refresh(saved.plan_id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : tr("保存草稿失败。", "Could not save the draft."));
    } finally { setWorking(false); }
  };

  const publish = async () => {
    if (!detail) return;
    setWorking(true);
    setError(null);
    try {
      if (draftTree) await saveStudyPlanDraft(detail.plan_id, normalizeOrders(draftTree));
      const published = await publishStudyPlan(detail.plan_id);
      setDetail(published);
      setDraftTree(null);
      await refresh(published.plan_id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : tr("发布失败，请检查层级和重复名称。", "Publish failed. Check the hierarchy and duplicate labels."));
    } finally { setWorking(false); }
  };

  const continueLearning = async (objectiveId: string, sourceMode: "primary" | "compare" = "primary") => {
    if (!detail?.published) return;
    setWorking(true);
    setError(null);
    try {
      const session = await openStudyObjective(
        detail.plan_id,
        objectiveId,
        detail.published.version,
        zh ? "zh" : "en",
        sourceMode,
      );
      router.push(session.chat_url);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : tr("无法打开学习会话。", "Could not open the learning session."));
      setWorking(false);
    }
  };

  const toggleArchive = async () => {
    if (!detail) return;
    setWorking(true);
    setError(null);
    try {
      if (detail.archived_at) await restoreStudyPlan(detail.plan_id);
      else await archiveStudyPlan(detail.plan_id);
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : tr("无法更新归档状态。", "Could not update archive state."));
    } finally {
      setWorking(false);
    }
  };

  return (
    <div className="mx-auto flex h-full min-h-0 max-w-7xl flex-col px-4 py-6 sm:px-6 lg:px-10">
      <header className="mb-5 flex flex-wrap items-start justify-between gap-4">
        <div className="flex gap-3">
          <span className="grid h-11 w-11 place-items-center rounded-xl bg-teal-500/10 text-teal-600"><GraduationCap className="h-5 w-5" /></span>
          <div>
            <h1 className="font-serif text-2xl font-semibold">{tr("学习计划", "Study Plans")}</h1>
            <p className="mt-1 text-sm text-[var(--muted-foreground)]">{tr("先从大纲或教材生成科目—章节—知识点，再按知识点恢复专属辅导与练习。", "Import a syllabus into subjects, modules, and objectives, then resume dedicated tutoring or practice.")}</p>
          </div>
        </div>
        <button type="button" onClick={() => setShowImport(true)} className="inline-flex items-center gap-2 rounded-lg bg-[var(--primary)] px-4 py-2 text-sm text-[var(--primary-foreground)]"><Plus className="h-4 w-4" />{tr("新建学习计划", "New study plan")}</button>
      </header>

      {error ? <p className="mb-4 rounded-lg bg-red-500/10 px-3 py-2 text-sm text-red-600">{error}</p> : null}
      <div className="grid min-h-0 flex-1 gap-4 lg:grid-cols-[280px_minmax(0,1fr)]">
        <aside className="overflow-y-auto rounded-xl border border-[var(--border)] bg-[var(--card)] p-2">
          <div className="mb-2 grid grid-cols-2 gap-1 rounded-lg bg-[var(--muted)]/40 p-1">
            {(["active", "archived"] as const).map((value) => (
              <button key={value} type="button" onClick={() => setArchiveFilter(value)} className={`rounded-md px-2 py-1.5 text-xs ${archiveFilter === value ? "bg-[var(--background)] font-medium shadow-sm" : "text-[var(--muted-foreground)]"}`}>
                {value === "active" ? tr("当前计划", "Current") : tr("已归档", "Archived")}
              </button>
            ))}
          </div>
          <p className="px-3 pb-2 pt-1 text-xs font-semibold uppercase tracking-wide text-[var(--muted-foreground)]">{tr("已发布科目", "Published subjects")}</p>
          {publishedSubjects.map(({ plan, subject }) => (
            <button key={`${plan.plan_id}:${subject.id}`} type="button" onClick={() => { setSelectedPlanId(plan.plan_id); setSelectedSubjectId(subject.id); }} className={`mb-1 w-full rounded-lg px-3 py-2 text-left ${selectedPlanId === plan.plan_id && selectedSubjectId === subject.id ? "bg-[var(--primary)]/10 ring-1 ring-[var(--primary)]/30" : "hover:bg-[var(--muted)]/50"}`}>
              <span className="block truncate text-sm font-medium">{subject.name}</span>
              <span className="mt-1 block truncate text-xs text-[var(--muted-foreground)]">{plan.name} · {subject.modules.reduce((sum, item) => sum + item.knowledge_points.length, 0)} {tr("个知识点", "objectives")}</span>
            </button>
          ))}
          {draftPlans.length ? <p className="mt-4 px-3 pb-2 text-xs font-semibold uppercase tracking-wide text-[var(--muted-foreground)]">{tr("待确认草稿", "Drafts to review")}</p> : null}
          {draftPlans.map((plan) => <button key={`draft:${plan.plan_id}`} type="button" onClick={() => setSelectedPlanId(plan.plan_id)} className={`mb-1 w-full rounded-lg border border-dashed px-3 py-2 text-left ${selectedPlanId === plan.plan_id ? "border-[var(--primary)] bg-[var(--primary)]/5" : "border-[var(--border)]"}`}><span className="block truncate text-sm font-medium">{plan.name}</span><span className="text-xs text-amber-600">{tr("解析草稿，尚未成为考试范围", "Parsed draft, not an exam scope yet")}</span></button>)}
          {!loading && plans.length === 0 ? <p className="p-4 text-sm text-[var(--muted-foreground)]">{tr("还没有学习计划。请导入考试大纲或教材。", "No study plan yet. Import a syllabus or textbook.")}</p> : null}
        </aside>

        <section className="min-h-0 overflow-y-auto rounded-xl border border-[var(--border)] bg-[var(--card)] p-5">
          {loading ? <div className="grid h-48 place-items-center"><Loader2 className="h-5 w-5 animate-spin" /></div> : null}
          {!loading && detail ? <div className="mb-4 flex justify-end"><button type="button" disabled={working} onClick={() => void toggleArchive()} className="inline-flex items-center gap-2 rounded-lg border border-[var(--border)] px-3 py-2 text-xs disabled:opacity-50">{detail.archived_at ? <ArchiveRestore className="h-4 w-4" /> : <Archive className="h-4 w-4" />}{detail.archived_at ? tr("恢复学习计划", "Restore study plan") : tr("归档学习计划", "Archive study plan")}</button></div> : null}
          {!loading && detail?.draft && draftTree && !detail.archived_at ? <DraftEditor tree={draftTree} setTree={setDraftTree} tr={tr} working={working} onSave={saveDraft} onPublish={publish} /> : null}
          {!loading && detail?.published && !detail.draft && selectedSubject ? (
            <div className="space-y-5">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div><h2 className="font-serif text-xl font-semibold">{selectedSubject.name}</h2><p className="mt-1 text-xs text-[var(--muted-foreground)]">{tr(`${detail.name} · 版本 ${detail.published.version} · 已发布考试范围`, `${detail.name} · v${detail.published.version} · published exam scope`)}</p></div>
                <span className={`rounded-full px-3 py-1 text-xs ${detail.archived_at ? "bg-amber-500/10 text-amber-600" : "bg-emerald-500/10 text-emerald-600"}`}><Check className="mr-1 inline h-3 w-3" />{detail.archived_at ? tr("已归档，只读", "Archived, read-only") : tr("结构已锁定", "Structure locked")}</span>
              </div>
              <TextbookGroundingPanel planId={detail.plan_id} version={detail.published.version} objectives={selectedSubject.modules.flatMap((item) => item.knowledge_points.map((point) => ({ id: point.id, name: point.name })))} />
              {selectedSubject.modules.map((module) => (
                <div key={module.id}>
                  <h3 className="mb-2 text-sm font-semibold">{module.name} <span className="font-normal text-[var(--muted-foreground)]">{module.knowledge_points.length} {tr("个知识点", "objectives")}</span></h3>
                  <div className="grid gap-2 md:grid-cols-2">
                    {module.knowledge_points.map((objective) => {
                      const session = detail.objective_sessions?.[objective.id];
                      const status = (session?.learning_status || "new") as keyof typeof STATUS_ICON;
                      const Icon = STATUS_ICON[status] ?? Circle;
                      const query = new URLSearchParams({ plan: detail.plan_id, subject: selectedSubject.id, kp: objective.id }).toString();
                      return <article key={objective.id} className="flex min-w-0 items-center gap-3 rounded-lg border border-[var(--border)] p-3">
                        <Icon className={`h-4 w-4 shrink-0 ${status === "mastered" ? "text-emerald-500" : status === "learning" ? "text-amber-500" : "text-[var(--muted-foreground)]"}`} />
                        <div className="min-w-0 flex-1"><p className="truncate text-sm">{objective.name}</p><p className="text-xs text-[var(--muted-foreground)]">{Math.round((session?.learning_mastery ?? 0) * 100)}% · {objective.type}</p></div>
                        {!detail.archived_at ? <button type="button" disabled={working} onClick={() => void continueLearning(objective.id)} title={tr("按主教材学习", "Learn from primary source")} className="rounded-lg border border-[var(--border)] p-2 text-teal-600 hover:bg-[var(--muted)] disabled:opacity-50"><MessageSquare className="h-4 w-4" /></button> : null}
                        {!detail.archived_at ? <button type="button" disabled={working} onClick={() => void continueLearning(objective.id, "compare")} title={tr("比较教材观点", "Compare textbook views")} className="rounded-lg border border-[var(--border)] p-2 text-indigo-600 hover:bg-[var(--muted)] disabled:opacity-50"><GitCompareArrows className="h-4 w-4" /></button> : null}
                        {!detail.archived_at ? <Link href={`/exam-mem/practice?${query}`} title={tr("专项练习", "Targeted practice")} className="rounded-lg border border-[var(--border)] p-2 text-[var(--primary)] hover:bg-[var(--muted)]"><BookOpenCheck className="h-4 w-4" /></Link> : null}
                      </article>;
                    })}
                  </div>
                </div>
              ))}
            </div>
          ) : null}
        </section>
      </div>

      {showImport ? <ImportDialog kind={importKind} setKind={setImportKind} name={importName} setName={setImportName} file={importFile} setFile={setImportFile} url={importUrl} setUrl={setImportUrl} request={importRequest} setRequest={setImportRequest} working={working} tr={tr} onClose={() => setShowImport(false)} onImport={runImport} /> : null}
    </div>
  );
}

function DraftEditor({ tree, setTree, tr, working, onSave, onPublish }: { tree: StudyPlanTree; setTree: (tree: StudyPlanTree) => void; tr: (cn: string, en: string) => string; working: boolean; onSave: () => Promise<void>; onPublish: () => Promise<void> }) {
  const update = (subjectIndex: number, moduleIndex: number | null, objectiveIndex: number | null, name: string) => {
    const next = structuredClone(tree);
    if (moduleIndex === null) next.subjects[subjectIndex].name = name;
    else if (objectiveIndex === null) next.subjects[subjectIndex].modules[moduleIndex].name = name;
    else next.subjects[subjectIndex].modules[moduleIndex].knowledge_points[objectiveIndex].name = name;
    setTree(next);
  };
  return <div className="space-y-5">
    <div className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="font-serif text-xl font-semibold">{tr("确认解析结果", "Review parsed outline")}</h2><p className="mt-1 text-xs text-[var(--muted-foreground)]">{tr("发布前可修正标题。发布后会生成不可变的考试范围版本。", "Correct titles before publishing. Publishing creates an immutable exam-scope version.")}</p></div><div className="flex gap-2"><button type="button" disabled={working} onClick={() => void onSave()} className="inline-flex items-center gap-2 rounded-lg border border-[var(--border)] px-3 py-2 text-sm"><Save className="h-4 w-4" />{tr("保存草稿", "Save draft")}</button><button type="button" disabled={working} onClick={() => void onPublish()} className="inline-flex items-center gap-2 rounded-lg bg-[var(--primary)] px-3 py-2 text-sm text-[var(--primary-foreground)]"><Check className="h-4 w-4" />{tr("发布为考试范围", "Publish exam scope")}</button></div></div>
    <label className="block text-xs text-[var(--muted-foreground)]">{tr("计划名称", "Plan name")}<input value={tree.name} onChange={(event) => setTree({ ...tree, name: event.target.value })} className="mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-sm" /></label>
    {tree.subjects.map((subject, subjectIndex) => <div key={subject.id} className="rounded-xl border border-[var(--border)] p-4"><input value={subject.name} onChange={(event) => update(subjectIndex, null, null, event.target.value)} className="w-full bg-transparent text-base font-semibold outline-none" />{subject.modules.map((module, moduleIndex) => <div key={module.id} className="mt-4 border-l-2 border-[var(--primary)]/20 pl-4"><input value={module.name} onChange={(event) => update(subjectIndex, moduleIndex, null, event.target.value)} className="w-full bg-transparent text-sm font-medium outline-none" /><div className="mt-2 grid gap-2 md:grid-cols-2">{module.knowledge_points.map((objective, objectiveIndex) => <label key={objective.id} className="rounded-lg bg-[var(--muted)]/40 p-2 text-[11px] text-[var(--muted-foreground)]">{objective.type}<input value={objective.name} onChange={(event) => update(subjectIndex, moduleIndex, objectiveIndex, event.target.value)} className="mt-1 w-full bg-transparent text-sm text-[var(--foreground)] outline-none" /></label>)}</div></div>)}</div>)}
  </div>;
}

function ImportDialog({ kind, setKind, name, setName, file, setFile, url, setUrl, request, setRequest, working, tr, onClose, onImport }: { kind: ImportKind; setKind: (value: ImportKind) => void; name: string; setName: (value: string) => void; file: File | null; setFile: (value: File | null) => void; url: string; setUrl: (value: string) => void; request: string; setRequest: (value: string) => void; working: boolean; tr: (cn: string, en: string) => string; onClose: () => void; onImport: () => Promise<void> }) {
  return <div className="fixed inset-0 z-50 grid place-items-center bg-black/50 p-4"><div className="w-full max-w-xl rounded-2xl border border-[var(--border)] bg-[var(--background)] p-5 shadow-2xl"><div className="flex items-center justify-between"><div><h2 className="font-serif text-xl font-semibold">{tr("新建学习计划", "New study plan")}</h2><p className="mt-1 text-xs text-[var(--muted-foreground)]">{tr("仅解析层级标题，不生成课程正文或题目。", "Only hierarchical titles are extracted; no lessons or questions are generated.")}</p></div><button type="button" onClick={onClose}><X className="h-5 w-5" /></button></div><label className="mt-5 block text-xs text-[var(--muted-foreground)]">{tr("计划名称", "Plan name")}<input value={name} onChange={(event) => setName(event.target.value)} placeholder={tr("例如：2027 考研数学一", "e.g. 2027 Math I") } className="mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--card)] px-3 py-2 text-sm" /></label><div className="mt-4 grid grid-cols-3 gap-2">{([['file', FileUp, tr("文件", "File")], ['url', Link2, 'URL'], ['generated', Sparkles, tr("模型创建", "Model")]] as const).map(([value, Icon, label]) => <button key={value} type="button" onClick={() => setKind(value)} className={`inline-flex items-center justify-center gap-2 rounded-lg border px-3 py-2 text-sm ${kind === value ? "border-[var(--primary)] bg-[var(--primary)]/10" : "border-[var(--border)]"}`}><Icon className="h-4 w-4" />{label}</button>)}</div>{kind === "file" ? <label className="mt-4 flex cursor-pointer items-center justify-center gap-2 rounded-xl border border-dashed border-[var(--border)] p-8 text-sm"><FileUp className="h-5 w-5" />{file?.name ?? tr("选择 PDF / TXT / MD", "Choose PDF / TXT / MD")}<input type="file" accept=".pdf,.txt,.md" className="hidden" onChange={(event) => setFile(event.target.files?.[0] ?? null)} /></label> : null}{kind === "url" ? <label className="mt-4 block text-xs text-[var(--muted-foreground)]">{tr("公开大纲 URL", "Public syllabus URL")}<input value={url} onChange={(event) => setUrl(event.target.value)} placeholder={tr("https://…", "https://…")} className="mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--card)] px-3 py-2 text-sm" /></label> : null}{kind === "generated" ? <label className="mt-4 block text-xs text-[var(--muted-foreground)]">{tr("描述考试与科目", "Describe the exam and subjects")}<textarea value={request} onChange={(event) => setRequest(event.target.value)} rows={5} placeholder={tr("例如：为考研数学一创建完整章节和可独立学习、检测的叶子知识点。", "For example: create modules and independently teachable/testable objectives for Math I.")} className="mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--card)] px-3 py-2 text-sm" /></label> : null}<div className="mt-5 flex justify-end gap-2"><button type="button" onClick={onClose} className="rounded-lg border border-[var(--border)] px-4 py-2 text-sm">{tr("取消", "Cancel")}</button><button type="button" disabled={working || !name.trim()} onClick={() => void onImport()} className="inline-flex items-center gap-2 rounded-lg bg-[var(--primary)] px-4 py-2 text-sm text-[var(--primary-foreground)] disabled:opacity-50">{working ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}{tr("解析为草稿", "Parse draft")}</button></div></div></div>;
}

function normalizeOrders(tree: StudyPlanTree): StudyPlanTree {
  return {
    ...tree,
    subjects: tree.subjects.map((subject, subjectOrder) => ({
      ...subject,
      order: subjectOrder,
      modules: subject.modules.map((module, moduleOrder) => ({
        ...module,
        order: moduleOrder,
        knowledge_points: module.knowledge_points.map((objective, objectiveOrder) => ({ ...objective, order: objectiveOrder })),
      })),
    })),
  };
}
