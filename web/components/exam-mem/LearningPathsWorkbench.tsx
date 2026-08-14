"use client";

import Link from "next/link";
import { BookOpenCheck, Circle, CircleCheck, CircleDot, GraduationCap, Loader2, MessageSquare } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  fetchAllProgress,
  fetchMasteryMap,
  type MasteryMapResult,
  type ObjectiveStatus,
  type ProgressSummary,
} from "@/lib/learning-api";
import { newMasteryPathChatUrl } from "@/lib/mastery-path-navigation";

const STATUS_ICON = {
  mastered: CircleCheck,
  learning: CircleDot,
  new: Circle,
} satisfies Record<ObjectiveStatus, typeof Circle>;

export default function LearningPathsWorkbench() {
  const { i18n } = useTranslation();
  const zh = i18n.language?.toLowerCase().startsWith("zh");
  const tr = useCallback((cn: string, en: string) => (zh ? cn : en), [zh]);
  const [paths, setPaths] = useState<ProgressSummary[]>([]);
  const [selected, setSelected] = useState("");
  const [detail, setDetail] = useState<MasteryMapResult | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    void fetchAllProgress()
      .then((result) => {
        if (!active) return;
        const items = result.summaries.filter((item) => item.kp_count > 0);
        setPaths(items);
        setSelected((current) => current || items[0]?.book_id || "");
        if (items.length === 0) setLoading(false);
      })
      .catch(() => active && setLoading(false));
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!selected) return;
    let active = true;
    void fetchMasteryMap(selected)
      .then((result) => active && setDetail(result))
      .finally(() => active && setLoading(false));
    return () => { active = false; };
  }, [selected]);

  return (
    <div className="mx-auto flex h-full min-h-0 max-w-7xl flex-col px-4 py-6 sm:px-6 lg:px-10">
      <header className="mb-5 flex flex-wrap items-start justify-between gap-4">
        <div className="flex gap-3">
          <span className="grid h-11 w-11 place-items-center rounded-xl bg-teal-500/10 text-teal-600"><GraduationCap className="h-5 w-5" /></span>
          <div>
            <h1 className="font-serif text-2xl font-semibold">{tr("学习路径", "Learning Paths")}</h1>
            <p className="mt-1 text-sm text-[var(--muted-foreground)]">{tr("按知识点查看学习进度，继续辅导，或进入智能备考进行独立练习。", "Review progress by objective, continue tutoring, or start an independent Smart Exam Prep practice.")}</p>
          </div>
        </div>
        <Link href="/home?capability=mastery_path" className="inline-flex items-center gap-2 rounded-lg bg-[var(--primary)] px-4 py-2 text-sm text-[var(--primary-foreground)]"><MessageSquare className="h-4 w-4" />{tr("新建学习路径", "New learning path")}</Link>
      </header>

      <div className="grid min-h-0 flex-1 gap-4 lg:grid-cols-[260px_minmax(0,1fr)]">
        <aside className="overflow-y-auto rounded-xl border border-[var(--border)] bg-[var(--card)] p-2">
          {paths.map((path) => (
            <button key={path.book_id} type="button" onClick={() => { setLoading(true); setSelected(path.book_id); }} className={`mb-1 w-full rounded-lg px-3 py-2 text-left ${selected === path.book_id ? "bg-[var(--primary)]/10 ring-1 ring-[var(--primary)]/30" : "hover:bg-[var(--muted)]/50"}`}>
              <span className="block truncate text-sm font-medium">{path.name}</span>
              <span className="mt-1 block text-xs text-[var(--muted-foreground)]">{path.kp_count} {tr("个知识点", "objectives")} · {path.avg_mastery_pct}%</span>
            </button>
          ))}
          {!loading && paths.length === 0 ? <p className="p-4 text-sm text-[var(--muted-foreground)]">{tr("还没有学习路径，请先在对话中使用“精通之路”创建。", "No learning paths yet. Create one from Chat using Mastery Path.")}</p> : null}
        </aside>

        <section className="min-h-0 overflow-y-auto rounded-xl border border-[var(--border)] bg-[var(--card)] p-5">
          {loading ? <div className="grid h-48 place-items-center"><Loader2 className="h-5 w-5 animate-spin" /></div> : null}
          {!loading && detail ? (
            <div className="space-y-5">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <p className="text-sm text-[var(--muted-foreground)]">{detail.map.counts.mastered}/{detail.map.counts.total} {tr("已掌握", "mastered")} · {detail.map.due_reviews} {tr("项待复习", "reviews due")}</p>
                <Link href={newMasteryPathChatUrl(selected)} className="text-sm text-[var(--primary)] hover:underline">{tr("继续学习辅导 →", "Continue tutoring →")}</Link>
              </div>
              {detail.map.modules.map((module) => (
                <div key={module.id}>
                  <h2 className="mb-2 text-sm font-semibold">{module.name} <span className="font-normal text-[var(--muted-foreground)]">{module.mastered}/{module.total}</span></h2>
                  <div className="grid gap-2 md:grid-cols-2">
                    {module.knowledge_points.map((kp) => {
                      const Icon = STATUS_ICON[kp.status];
                      const query = new URLSearchParams({ path: selected, kp: kp.id, name: kp.name }).toString();
                      return <article key={kp.id} className="flex min-w-0 items-center gap-3 rounded-lg border border-[var(--border)] p-3">
                        <Icon className={`h-4 w-4 shrink-0 ${kp.status === "mastered" ? "text-emerald-500" : kp.status === "learning" ? "text-amber-500" : "text-[var(--muted-foreground)]"}`} />
                        <div className="min-w-0 flex-1"><p className="truncate text-sm">{kp.name}</p><p className="text-xs text-[var(--muted-foreground)]">{Math.round(kp.mastery * 100)}% · {kp.type}</p></div>
                        <Link href={`/exam-mem/practice?${query}`} title={tr("练习这个知识点", "Practice this objective")} className="rounded-lg border border-[var(--border)] p-2 text-[var(--primary)] hover:bg-[var(--muted)]"><BookOpenCheck className="h-4 w-4" /></Link>
                      </article>;
                    })}
                  </div>
                </div>
              ))}
            </div>
          ) : null}
        </section>
      </div>
    </div>
  );
}
