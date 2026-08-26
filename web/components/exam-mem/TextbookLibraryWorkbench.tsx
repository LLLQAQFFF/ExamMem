"use client";

import { Archive, BookMarked, FileUp, GraduationCap, Loader2, RefreshCw, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { autofillTitleFromFilename, extractBase64FromDataUrl, readFileAsDataUrl } from "@/lib/file-attachments";
import { archiveTextbook, createStudyPlanFromTextbook, getTextbookVersion, listTextbooks, retryTextbookJob, type Textbook, type TextbookSection, type TextbookVersion, uploadTextbook } from "@/lib/exam-mem-textbooks";

export default function TextbookLibraryWorkbench() {
  const router = useRouter();
  const { i18n } = useTranslation();
  const zh = i18n.language?.toLowerCase().startsWith("zh");
  const tr = useCallback((cn: string, en: string) => (zh ? cn : en), [zh]);
  const [items, setItems] = useState<Textbook[]>([]);
  const [selected, setSelected] = useState<TextbookVersion | null>(null);
  const [title, setTitle] = useState("");
  const [titleIsAutomatic, setTitleIsAutomatic] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [planScope, setPlanScope] = useState<TextbookSection | null | undefined>(undefined);
  const [planName, setPlanName] = useState("");
  const [planIdempotencyKey, setPlanIdempotencyKey] = useState("");
  const [planWorking, setPlanWorking] = useState(false);

  const refresh = useCallback(async () => setItems(await listTextbooks()), []);
  useEffect(() => { void refresh().catch((cause) => setError(String(cause))); }, [refresh]);
  useEffect(() => {
    if (!selected || !["queued", "processing"].includes(selected.status)) return;
    const timer = window.setInterval(() => {
      void getTextbookVersion(selected.textbook_id, selected.version_id).then((version) => { setSelected(version); void refresh(); });
    }, 1500);
    return () => window.clearInterval(timer);
  }, [selected, refresh]);

  const upload = async () => {
    if (!file || !title.trim()) return;
    setWorking(true); setError(null);
    try {
      const suffix = file.name.toLowerCase().split(".").pop();
      const mime = file.type || (suffix === "pdf" ? "application/pdf" : suffix === "md" ? "text/markdown" : "text/plain");
      const version = await uploadTextbook({ title, filename: file.name, mime_type: mime, base64: extractBase64FromDataUrl(await readFileAsDataUrl(file)), idempotency_key: crypto.randomUUID() });
      setSelected(version); setTitle(""); setTitleIsAutomatic(false); setFile(null); await refresh();
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setWorking(false); }
  };
  const selectedProgress = selected?.job?.progress ?? (selected?.status === "completed" ? 100 : 0);
  const selectedVersionLabel = selected ? `v${selected.version}` : "";
  const selectedFileSummary = selected
    ? `${selected.filename} · ${(selected.size_bytes / 1024 / 1024).toFixed(2)} MB · ${selected.job?.stage || selected.status}`
    : "";
  const selectFile = (selectedFile: File | null) => {
    setFile(selectedFile);
    if (!selectedFile) return;
    const next = autofillTitleFromFilename(title, titleIsAutomatic, selectedFile.name);
    setTitle(next.title);
    setTitleIsAutomatic(next.isAutomatic);
  };
  const selectedBook = selected
    ? items.find((book) => book.textbook_id === selected.textbook_id)
    : undefined;
  const openPlanDialog = (scope: TextbookSection | null) => {
    if (!selectedBook) return;
    setPlanScope(scope);
    setPlanName(`${scope?.title || selectedBook.title}${tr("学习计划", " Study Plan")}`);
    setPlanIdempotencyKey(crypto.randomUUID());
  };
  const createPlan = async () => {
    if (!selected || !selectedBook || planScope === undefined || !planName.trim() || !planIdempotencyKey) return;
    setPlanWorking(true);
    setError(null);
    try {
      const plan = await createStudyPlanFromTextbook(selectedBook.textbook_id, selected.version_id, {
        name: planName,
        section_id: planScope?.section_id ?? null,
        idempotency_key: planIdempotencyKey,
      });
      setPlanScope(undefined);
      router.push(`/exam-mem/learning?plan=${encodeURIComponent(plan.plan_id)}`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setPlanWorking(false);
    }
  };

  return <div className="mx-auto flex h-full max-w-7xl flex-col gap-4 overflow-y-auto px-4 py-6 sm:px-6 lg:px-10">
    <header className="flex flex-wrap items-start justify-between gap-4"><div className="flex gap-3"><span className="grid h-11 w-11 place-items-center rounded-xl bg-indigo-500/10 text-indigo-600"><BookMarked className="h-5 w-5" /></span><div><h1 className="font-serif text-2xl font-semibold">{tr("教材库", "Textbook Library")}</h1><p className="mt-1 text-sm text-[var(--muted-foreground)]">{tr("管理不可变教材版本、章节结构和可恢复索引。", "Manage immutable versions, chapter structure, and recoverable indexes.")}</p></div></div></header>
    {error ? <p className="rounded-lg bg-red-500/10 px-3 py-2 text-sm text-red-600">{error}</p> : null}
    <section className="grid gap-3 rounded-xl border border-[var(--border)] bg-[var(--card)] p-4 md:grid-cols-[1fr_1fr_auto]"><input value={title} onChange={(event) => { setTitle(event.target.value); setTitleIsAutomatic(false); }} placeholder={tr("教材标题", "Textbook title")} className="rounded-lg border border-[var(--border)] bg-transparent px-3 py-2 text-sm" /><input type="file" accept=".pdf,.txt,.md" onChange={(event) => selectFile(event.target.files?.[0] || null)} className="text-sm" /><button type="button" disabled={working || !file || !title.trim()} onClick={() => void upload()} className="inline-flex items-center justify-center gap-2 rounded-lg bg-[var(--primary)] px-4 py-2 text-sm text-[var(--primary-foreground)] disabled:opacity-50">{working ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileUp className="h-4 w-4" />}{tr("上传并处理", "Upload and process")}</button></section>
    <div className="grid gap-4 lg:grid-cols-[320px_minmax(0,1fr)]"><aside className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-2">{items.map((book) => <div key={book.textbook_id} className="mb-2 rounded-lg border border-[var(--border)] p-3"><div className="flex items-start justify-between gap-2"><div><p className="text-sm font-medium">{book.title}</p><p className="text-xs text-[var(--muted-foreground)]">{book.versions.length} {tr("个版本", "versions")}</p></div><button type="button" title={tr("归档", "Archive")} onClick={() => void archiveTextbook(book.textbook_id).then(refresh)}><Archive className="h-4 w-4" /></button></div>{book.versions.map((version) => <button type="button" key={version.version_id} onClick={() => void getTextbookVersion(book.textbook_id, version.version_id).then(setSelected)} className="mt-2 flex w-full items-center justify-between rounded-md bg-[var(--muted)]/40 px-2 py-1.5 text-left text-xs"><span>v{version.version} · {version.filename}</span><span>{version.status}</span></button>)}</div>)}</aside>
      <section className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-5">{selected ? <><div className="flex flex-wrap items-center justify-between gap-2"><div><h2 className="font-serif text-xl font-semibold">{selectedVersionLabel}</h2><p className="text-xs text-[var(--muted-foreground)]">{selectedFileSummary}</p></div><div className="flex items-center gap-2">{selected.status === "completed" && selected.sections?.length ? <button type="button" onClick={() => openPlanDialog(null)} className="inline-flex items-center gap-1 rounded-lg bg-teal-600 px-3 py-2 text-xs text-white"><GraduationCap className="h-3.5 w-3.5" />{tr("整本创建学习路径", "Create path from whole book")}</button> : null}<span className="rounded-full bg-indigo-500/10 px-3 py-1 text-xs text-indigo-600">{`${selectedProgress}%`}</span></div></div>{selected.job?.error_message ? <div className="mt-4 rounded-lg bg-red-500/10 p-3 text-sm text-red-600"><p>{selected.job.error_code}: {selected.job.error_message}</p><button type="button" onClick={() => void retryTextbookJob(selected.job!.job_id).then(() => getTextbookVersion(selected.textbook_id, selected.version_id).then(setSelected))} className="mt-2 inline-flex items-center gap-1 rounded border px-2 py-1"><RefreshCw className="h-3 w-3" />{tr("从 checkpoint 重试", "Retry from checkpoint")}</button></div> : null}<div className="mt-5 space-y-1">{selected.sections?.map((section) => { const hasChildren = selected.sections?.some((item) => item.parent_section_id === section.section_id); return <div key={section.section_id} style={{ marginLeft: `${Math.max(0, section.level - 1) * 16}px` }} className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-[var(--muted)]/40"><div className="min-w-0 flex-1"><span>{section.title}</span><span className="ml-2 text-xs text-[var(--muted-foreground)]">{section.start_page ? `${tr("第", "p.")}${section.start_page}${section.end_page && section.end_page !== section.start_page ? `–${section.end_page}` : ""}` : tr("无页码", "No page")}{section.inferred ? ` · ${tr("推断目录", "Inferred")}` : ""}</span></div>{selected.status === "completed" ? <button type="button" onClick={() => openPlanDialog(section)} className="shrink-0 rounded border border-[var(--border)] px-2 py-1 text-[11px] text-teal-700 hover:bg-teal-500/10">{hasChildren ? tr("从本章创建", "Use chapter") : tr("从本节创建", "Use section")}</button> : null}</div>; })}</div></> : <p className="text-sm text-[var(--muted-foreground)]">{tr("选择一个教材版本查看章节与处理状态。", "Select a version to inspect its chapters and processing status.")}</p>}</section></div>
    {planScope !== undefined && selectedBook ? <div className="fixed inset-0 z-50 grid place-items-center bg-black/50 p-4"><div className="w-full max-w-lg rounded-2xl border border-[var(--border)] bg-[var(--background)] p-5 shadow-2xl"><div className="flex items-start justify-between gap-3"><div><h2 className="font-serif text-xl font-semibold">{tr("从教材创建学习路径", "Create a learning path")}</h2><p className="mt-1 text-xs text-[var(--muted-foreground)]">{planScope ? tr(`范围：${planScope.path.join(" / ")}（包含子章节）`, `Scope: ${planScope.path.join(" / ")} (including descendants)`) : tr(`范围：${selectedBook.title}整本教材`, `Scope: the whole ${selectedBook.title} textbook`)}</p></div><button type="button" onClick={() => setPlanScope(undefined)}><X className="h-5 w-5" /></button></div><label className="mt-5 block text-xs text-[var(--muted-foreground)]">{tr("计划名称", "Plan name")}<input value={planName} onChange={(event) => setPlanName(event.target.value)} className="mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--card)] px-3 py-2 text-sm" /></label><p className="mt-3 text-xs text-[var(--muted-foreground)]">{tr("将生成可编辑草稿；教材版本和章节建议会在发布后等待你确认。", "An editable draft will be created; textbook version and section suggestions await confirmation after publishing.")}</p><div className="mt-5 flex justify-end gap-2"><button type="button" onClick={() => setPlanScope(undefined)} className="rounded-lg border border-[var(--border)] px-4 py-2 text-sm">{tr("取消", "Cancel")}</button><button type="button" disabled={planWorking || !planName.trim()} onClick={() => void createPlan()} className="inline-flex items-center gap-2 rounded-lg bg-[var(--primary)] px-4 py-2 text-sm text-[var(--primary-foreground)] disabled:opacity-50">{planWorking ? <Loader2 className="h-4 w-4 animate-spin" /> : <GraduationCap className="h-4 w-4" />}{tr("生成草稿", "Create draft")}</button></div></div></div> : null}
  </div>;
}
