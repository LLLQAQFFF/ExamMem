"use client";

import { Archive, BookMarked, FileUp, Loader2, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { extractBase64FromDataUrl, readFileAsDataUrl } from "@/lib/file-attachments";
import { archiveTextbook, getTextbookVersion, listTextbooks, retryTextbookJob, type Textbook, type TextbookVersion, uploadTextbook } from "@/lib/exam-mem-textbooks";

export default function TextbookLibraryWorkbench() {
  const { i18n } = useTranslation();
  const zh = i18n.language?.toLowerCase().startsWith("zh");
  const tr = useCallback((cn: string, en: string) => (zh ? cn : en), [zh]);
  const [items, setItems] = useState<Textbook[]>([]);
  const [selected, setSelected] = useState<TextbookVersion | null>(null);
  const [title, setTitle] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
      setSelected(version); setTitle(""); setFile(null); await refresh();
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setWorking(false); }
  };
  const selectedProgress = selected?.job?.progress ?? (selected?.status === "completed" ? 100 : 0);
  const selectedVersionLabel = selected ? `v${selected.version}` : "";
  const selectedFileSummary = selected
    ? `${selected.filename} · ${(selected.size_bytes / 1024 / 1024).toFixed(2)} MB · ${selected.job?.stage || selected.status}`
    : "";

  return <div className="mx-auto flex h-full max-w-7xl flex-col gap-4 overflow-y-auto px-4 py-6 sm:px-6 lg:px-10">
    <header className="flex flex-wrap items-start justify-between gap-4"><div className="flex gap-3"><span className="grid h-11 w-11 place-items-center rounded-xl bg-indigo-500/10 text-indigo-600"><BookMarked className="h-5 w-5" /></span><div><h1 className="font-serif text-2xl font-semibold">{tr("教材库", "Textbook Library")}</h1><p className="mt-1 text-sm text-[var(--muted-foreground)]">{tr("管理不可变教材版本、章节结构和可恢复索引。", "Manage immutable versions, chapter structure, and recoverable indexes.")}</p></div></div></header>
    {error ? <p className="rounded-lg bg-red-500/10 px-3 py-2 text-sm text-red-600">{error}</p> : null}
    <section className="grid gap-3 rounded-xl border border-[var(--border)] bg-[var(--card)] p-4 md:grid-cols-[1fr_1fr_auto]"><input value={title} onChange={(event) => setTitle(event.target.value)} placeholder={tr("教材标题", "Textbook title")} className="rounded-lg border border-[var(--border)] bg-transparent px-3 py-2 text-sm" /><input type="file" accept=".pdf,.txt,.md" onChange={(event) => setFile(event.target.files?.[0] || null)} className="text-sm" /><button type="button" disabled={working || !file || !title.trim()} onClick={() => void upload()} className="inline-flex items-center justify-center gap-2 rounded-lg bg-[var(--primary)] px-4 py-2 text-sm text-[var(--primary-foreground)] disabled:opacity-50">{working ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileUp className="h-4 w-4" />}{tr("上传并处理", "Upload and process")}</button></section>
    <div className="grid gap-4 lg:grid-cols-[320px_minmax(0,1fr)]"><aside className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-2">{items.map((book) => <div key={book.textbook_id} className="mb-2 rounded-lg border border-[var(--border)] p-3"><div className="flex items-start justify-between gap-2"><div><p className="text-sm font-medium">{book.title}</p><p className="text-xs text-[var(--muted-foreground)]">{book.versions.length} {tr("个版本", "versions")}</p></div><button type="button" title={tr("归档", "Archive")} onClick={() => void archiveTextbook(book.textbook_id).then(refresh)}><Archive className="h-4 w-4" /></button></div>{book.versions.map((version) => <button type="button" key={version.version_id} onClick={() => void getTextbookVersion(book.textbook_id, version.version_id).then(setSelected)} className="mt-2 flex w-full items-center justify-between rounded-md bg-[var(--muted)]/40 px-2 py-1.5 text-left text-xs"><span>v{version.version} · {version.filename}</span><span>{version.status}</span></button>)}</div>)}</aside>
      <section className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-5">{selected ? <><div className="flex flex-wrap items-center justify-between gap-2"><div><h2 className="font-serif text-xl font-semibold">{selectedVersionLabel}</h2><p className="text-xs text-[var(--muted-foreground)]">{selectedFileSummary}</p></div><span className="rounded-full bg-indigo-500/10 px-3 py-1 text-xs text-indigo-600">{`${selectedProgress}%`}</span></div>{selected.job?.error_message ? <div className="mt-4 rounded-lg bg-red-500/10 p-3 text-sm text-red-600"><p>{selected.job.error_code}: {selected.job.error_message}</p><button type="button" onClick={() => void retryTextbookJob(selected.job!.job_id).then(() => getTextbookVersion(selected.textbook_id, selected.version_id).then(setSelected))} className="mt-2 inline-flex items-center gap-1 rounded border px-2 py-1"><RefreshCw className="h-3 w-3" />{tr("从 checkpoint 重试", "Retry from checkpoint")}</button></div> : null}<div className="mt-5 space-y-1">{selected.sections?.map((section) => <div key={section.section_id} style={{ paddingLeft: `${Math.max(0, section.level - 1) * 16}px` }} className="rounded-md px-2 py-1.5 text-sm hover:bg-[var(--muted)]/40"><span>{section.title}</span><span className="ml-2 text-xs text-[var(--muted-foreground)]">{section.start_page ? `${tr("第", "p.")}${section.start_page}${section.end_page && section.end_page !== section.start_page ? `–${section.end_page}` : ""}` : tr("无页码", "No page")}{section.inferred ? ` · ${tr("推断目录", "Inferred")}` : ""}</span></div>)}</div></> : <p className="text-sm text-[var(--muted-foreground)]">{tr("选择一个教材版本查看章节与处理状态。", "Select a version to inspect its chapters and processing status.")}</p>}</section></div>
  </div>;
}
