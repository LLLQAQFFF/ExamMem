"use client";

import { AlertOctagon, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { listMemoryIssues, type MemoryIssue } from "@/lib/exam-mem-product";

const LABELS: Record<string, string> = {
  workflow_failure: "Workflow failure",
  grade_disputed: "Grade disputed",
  memory_inaccurate: "Memory inaccurate",
  contested_evidence: "Contested evidence",
  projection_pending: "Projection pending",
};

export default function MemoryIssuesWorkbench({ embedded = false }: { embedded?: boolean }) {
  const { t } = useTranslation();
  const [issues, setIssues] = useState<MemoryIssue[]>([]);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try { setError(null); setIssues(await listMemoryIssues()); }
    catch (cause) { setError(cause instanceof Error ? cause.message : t("Issues failed to load.")); }
  }, [t]);

  useEffect(() => {
    let active = true;
    void listMemoryIssues()
      .then((items) => { if (active) setIssues(items); })
      .catch((cause) => { if (active) setError(cause instanceof Error ? cause.message : t("Issues failed to load.")); });
    return () => { active = false; };
  }, [t]);

  const content = <>
    <header className="flex flex-wrap items-start justify-between gap-4">
      <div className="flex gap-3">
        <span className="grid h-10 w-10 place-items-center rounded-xl bg-red-500/10 text-red-500"><AlertOctagon className="h-5 w-5" /></span>
        <div><h2 className={`${embedded ? "text-lg" : "font-serif text-2xl"} font-semibold`}>{t("Memory Issues")}</h2><p className="mt-1 text-sm text-[var(--muted-foreground)]">{t("Derived operational and evidence states; contested evidence is not labelled as a system failure.")}</p></div>
      </div>
      <button type="button" onClick={() => void load()} className="inline-flex items-center gap-2 rounded-lg border border-[var(--border)] px-3 py-2 text-sm"><RefreshCw className="h-4 w-4" />{t("Refresh")}</button>
    </header>
    {error ? <p className="rounded-lg bg-red-500/10 p-3 text-sm text-red-600">{error}</p> : null}
    <section className="grid gap-3 md:grid-cols-2">
      {issues.map((issue) => <article key={issue.issue_id} className="min-w-0 rounded-xl border border-[var(--border)] bg-[var(--card)] p-4"><div className="flex flex-wrap items-center gap-2 text-xs"><span className="rounded-full bg-[var(--muted)] px-2 py-1">{t(LABELS[issue.type] ?? issue.type)}</span><span className="rounded-full bg-[var(--primary)]/10 px-2 py-1 text-[var(--primary)]">{issue.status}</span></div><p className="mt-3 text-sm">{issue.summary}</p><p className="mt-2 break-all font-mono text-[11px] text-[var(--muted-foreground)]">{issue.issue_id}</p></article>)}
    </section>
    {issues.length === 0 ? <p className="rounded-xl border border-dashed p-8 text-center text-sm text-[var(--muted-foreground)]">{t("No derived issues in this Scope.")}</p> : null}
  </>;

  return embedded ? <section className="space-y-4 border-t border-[var(--border)] pt-6">{content}</section> : <div className="mx-auto max-w-6xl space-y-6 px-4 py-8 sm:px-6 lg:px-10">{content}</div>;
}
