"use client";

import { Link2, Loader2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  getTextbookVersion,
  listTextbookBindings,
  listTextbookMappings,
  listTextbooks,
  setTextbookBinding,
  setTextbookMapping,
  type Textbook,
  type TextbookBinding,
  type TextbookMapping,
  type TextbookSection,
} from "@/lib/exam-mem-textbooks";

interface ObjectiveOption {
  id: string;
  name: string;
}

interface TextbookGroundingPanelProps {
  planId: string;
  version: number;
  objectives: ObjectiveOption[];
}

export default function TextbookGroundingPanel({ planId, version, objectives }: TextbookGroundingPanelProps) {
  const { i18n } = useTranslation();
  const zh = i18n.language?.toLowerCase().startsWith("zh");
  const tr = useCallback((cn: string, en: string) => (zh ? cn : en), [zh]);
  const [books, setBooks] = useState<Textbook[]>([]);
  const [bindings, setBindings] = useState<TextbookBinding[]>([]);
  const [mappings, setMappings] = useState<TextbookMapping[]>([]);
  const [versionId, setVersionId] = useState("");
  const [bindingRole, setBindingRole] = useState<TextbookBinding["role"]>("primary");
  const [bindingPriority, setBindingPriority] = useState(0);
  const [objectiveId, setObjectiveId] = useState(objectives[0]?.id || "");
  const [sectionId, setSectionId] = useState("");
  const [sections, setSections] = useState<TextbookSection[]>([]);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    const [available, currentBindings, currentMappings] = await Promise.all([
      listTextbooks(),
      listTextbookBindings(planId, version),
      listTextbookMappings(planId, version),
    ]);
    setBooks(available);
    setBindings(currentBindings);
    setMappings(currentMappings);
    const completed = available.flatMap((book) => book.versions).find((item) => item.status === "completed");
    setVersionId((current) => current || completed?.version_id || "");
  }, [planId, version]);

  useEffect(() => {
    void reload().catch((cause) => setError(String(cause)));
  }, [reload]);

  useEffect(() => {
    if (!objectives.some((item) => item.id === objectiveId)) {
      setObjectiveId(objectives[0]?.id || "");
    }
  }, [objectiveId, objectives]);

  useEffect(() => {
    const book = books.find((item) => item.versions.some((candidate) => candidate.version_id === versionId));
    if (!book || !versionId) {
      setSections([]);
      return;
    }
    const currentBinding = bindings.find((item) => item.textbook_version_id === versionId);
    if (currentBinding) {
      setBindingRole(currentBinding.role);
      setBindingPriority(currentBinding.priority);
    } else {
      setBindingRole(bindings.some((item) => item.role === "primary" && item.status === "confirmed") ? "supplement" : "primary");
      setBindingPriority(bindings.length);
    }
    void getTextbookVersion(book.textbook_id, versionId)
      .then((item) => {
        setSections(item.sections || []);
        setSectionId((current) => current || item.sections?.[0]?.section_id || "");
      })
      .catch((cause) => setError(String(cause)));
  }, [bindings, books, versionId]);

  const completedVersions = useMemo(
    () => books.flatMap((book) => book.versions.filter((item) => item.status === "completed").map((item) => ({ book, version: item }))),
    [books],
  );

  const saveBinding = async () => {
    if (!versionId) return;
    setWorking(true);
    setError(null);
    try {
      await setTextbookBinding(planId, version, {
        textbook_version_id: versionId,
        role: bindingRole,
        priority: bindingPriority,
        status: "confirmed",
      });
      await reload();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setWorking(false);
    }
  };

  const saveMapping = async (status: "candidate" | "confirmed") => {
    if (!objectiveId || !sectionId) return;
    setWorking(true);
    setError(null);
    try {
      await setTextbookMapping(planId, version, {
        objective_id: objectiveId,
        textbook_section_id: sectionId,
        confidence: 1,
        created_via: "manual",
        status,
      });
      await reload();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setWorking(false);
    }
  };

  const confirmedCount = bindings.filter((item) => item.status === "confirmed").length;

  return (
    <section className="rounded-xl border border-indigo-500/20 bg-indigo-500/5 p-4">
      <div className="flex items-center gap-2">
        <Link2 className="h-4 w-4 text-indigo-600" />
        <h3 className="text-sm font-semibold">{tr("教材绑定与章节映射", "Textbook bindings and section mappings")}</h3>
      </div>
      <p className="mt-1 text-xs text-[var(--muted-foreground)]">
        {confirmedCount
          ? tr(`已确认 ${confirmedCount} 个教材版本；教学只使用已确认章节。`, `${confirmedCount} confirmed textbook versions; teaching uses confirmed sections only.`)
          : tr("未绑定教材；学习会话会明确标记为通用模型讲解。", "No textbook is bound; learning sessions are explicitly marked as general-model tutoring.")}
      </p>
      {error ? <p className="mt-2 text-xs text-red-600">{error}</p> : null}

      <div className="mt-3 grid gap-2 md:grid-cols-[minmax(0,1fr)_auto_auto_auto]">
        <select
          value={versionId}
          onChange={(event) => {
            setVersionId(event.target.value);
            setSectionId("");
          }}
          className="rounded-lg border border-[var(--border)] bg-[var(--background)] px-2 py-2 text-xs"
        >
          <option value="">{tr("选择教材版本", "Choose a textbook version")}</option>
          {completedVersions.map(({ book, version: item }) => (
            <option key={item.version_id} value={item.version_id}>{`${book.title} v${item.version}`}</option>
          ))}
        </select>
        <select
          aria-label={tr("教材角色", "Textbook role")}
          value={bindingRole}
          onChange={(event) => setBindingRole(event.target.value as TextbookBinding["role"])}
          className="rounded-lg border border-[var(--border)] bg-[var(--background)] px-2 py-2 text-xs"
        >
          <option value="primary">{tr("主教材", "Primary")}</option>
          <option value="supplement">{tr("辅教材", "Supplement")}</option>
          <option value="reference">{tr("参考资料", "Reference")}</option>
        </select>
        <label className="flex items-center gap-1 rounded-lg border border-[var(--border)] px-2 text-xs text-[var(--muted-foreground)]">
          {tr("优先级", "Priority")}
          <input
            aria-label={tr("教材优先级", "Textbook priority")}
            type="number"
            min={0}
            max={1000}
            value={bindingPriority}
            onChange={(event) => setBindingPriority(Math.max(0, Number(event.target.value) || 0))}
            className="w-14 bg-transparent text-[var(--foreground)] outline-none"
          />
        </label>
        <button
          type="button"
          disabled={working || !versionId}
          onClick={() => void saveBinding()}
          className="rounded-lg border border-[var(--border)] px-3 py-2 text-xs disabled:opacity-50"
        >
          {working ? <Loader2 className="inline h-3 w-3 animate-spin" /> : tr("保存绑定版本", "Save binding revision")}
        </button>
      </div>

      {bindings.length ? (
        <div className="mt-2 flex flex-wrap gap-1">
          {bindings.map((binding) => {
            const selected = completedVersions.find(({ version: item }) => item.version_id === binding.textbook_version_id);
            const sourceLabel = selected ? `${selected.book.title} v${selected.version.version}` : binding.textbook_version_id;
            return (
              <span key={binding.binding_id} className="rounded-full bg-[var(--muted)] px-2 py-1 text-[11px] text-[var(--muted-foreground)]">
                {tr(`${sourceLabel} · ${binding.role} · 优先级 ${binding.priority} · ${binding.status}`, `${sourceLabel} · ${binding.role} · priority ${binding.priority} · ${binding.status}`)}
              </span>
            );
          })}
        </div>
      ) : null}

      <div className="mt-2 grid gap-2 md:grid-cols-[1fr_1fr_auto_auto]">
        <select value={objectiveId} onChange={(event) => setObjectiveId(event.target.value)} className="rounded-lg border border-[var(--border)] bg-[var(--background)] px-2 py-2 text-xs">
          {objectives.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select>
        <select value={sectionId} onChange={(event) => setSectionId(event.target.value)} className="rounded-lg border border-[var(--border)] bg-[var(--background)] px-2 py-2 text-xs">
          <option value="">{tr("选择章节", "Choose a section")}</option>
          {sections.map((item) => <option key={item.section_id} value={item.section_id}>{item.path.join(" / ")}</option>)}
        </select>
        <button type="button" disabled={working || !sectionId} onClick={() => void saveMapping("candidate")} className="rounded-lg border border-[var(--border)] px-3 py-2 text-xs disabled:opacity-50">
          {tr("保存候选", "Save candidate")}
        </button>
        <button type="button" disabled={working || !sectionId} onClick={() => void saveMapping("confirmed")} className="rounded-lg bg-indigo-600 px-3 py-2 text-xs text-white disabled:opacity-50">
          {tr("确认映射", "Confirm mapping")}
        </button>
      </div>

      <div className="mt-3 flex flex-wrap gap-1">
        {objectives.map((objective) => {
          const confirmed = mappings.filter((item) => item.objective_id === objective.id && item.status === "confirmed").length;
          return (
            <span key={objective.id} className={`rounded-full px-2 py-1 text-[11px] ${confirmed ? "bg-emerald-500/10 text-emerald-700" : "bg-amber-500/10 text-amber-700"}`}>
              {tr(`${objective.name} · ${confirmed} 章`, `${objective.name} · ${confirmed} sections`)}
            </span>
          );
        })}
      </div>
    </section>
  );
}
