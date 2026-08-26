"use client";

import { Link2, Loader2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  confirmTextbookPlanSuggestions,
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
  const [sectionsByVersionId, setSectionsByVersionId] = useState<Record<string, TextbookSection[]>>({});
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const confirmationIdempotencyKey = useRef<string | null>(null);
  const sections = sectionsByVersionId[versionId] || [];

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
    const currentBinding = bindings.find((item) => item.textbook_version_id === versionId);
    if (currentBinding) {
      setBindingRole(currentBinding.role);
      setBindingPriority(currentBinding.priority);
    } else {
      setBindingRole(bindings.some((item) => item.role === "primary" && item.status === "confirmed") ? "supplement" : "primary");
      setBindingPriority(bindings.length);
    }

    const relevantVersionIds = Array.from(
      new Set([versionId, ...bindings.map((item) => item.textbook_version_id)].filter(Boolean)),
    );
    let cancelled = false;
    void Promise.all(
      relevantVersionIds.map(async (currentVersionId) => {
        const book = books.find((item) => item.versions.some((candidate) => candidate.version_id === currentVersionId));
        if (!book) return null;
        const item = await getTextbookVersion(book.textbook_id, currentVersionId);
        return [currentVersionId, item.sections || []] as const;
      }),
    )
      .then((entries) => {
        if (cancelled) return;
        const nextSections: Record<string, TextbookSection[]> = {};
        for (const entry of entries) {
          if (entry) nextSections[entry[0]] = entry[1];
        }
        setSectionsByVersionId(nextSections);
        setSectionId((current) => current || nextSections[versionId]?.[0]?.section_id || "");
      })
      .catch((cause) => {
        if (!cancelled) setError(String(cause));
      });
    return () => {
      cancelled = true;
    };
  }, [bindings, books, versionId]);

  const completedVersions = useMemo(
    () => books.flatMap((book) => book.versions.filter((item) => item.status === "completed").map((item) => ({ book, version: item }))),
    [books],
  );
  const objectiveNames = useMemo(
    () => new Map(objectives.map((item) => [item.id, item.name])),
    [objectives],
  );
  const sectionDetails = useMemo(() => {
    const details = new Map<string, { path: string; source: string; order: number }>();
    for (const [currentVersionId, currentSections] of Object.entries(sectionsByVersionId)) {
      const selected = completedVersions.find(({ version: item }) => item.version_id === currentVersionId);
      const source = selected ? `${selected.book.title} v${selected.version.version}` : currentVersionId;
      for (const section of currentSections) {
        details.set(section.section_id, {
          path: section.path.join(" / "),
          source,
          order: section.order,
        });
      }
    }
    return details;
  }, [completedVersions, sectionsByVersionId]);
  const mappingRows = useMemo(
    () => [...mappings].sort((left, right) => {
      const objectiveOrder = objectives.findIndex((item) => item.id === left.objective_id)
        - objectives.findIndex((item) => item.id === right.objective_id);
      if (objectiveOrder) return objectiveOrder;
      return (sectionDetails.get(left.textbook_section_id)?.order ?? 0)
        - (sectionDetails.get(right.textbook_section_id)?.order ?? 0);
    }),
    [mappings, objectives, sectionDetails],
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

  const confirmGeneratedScope = async () => {
    setWorking(true);
    setError(null);
    try {
      confirmationIdempotencyKey.current ||= crypto.randomUUID();
      await confirmTextbookPlanSuggestions(planId, version, confirmationIdempotencyKey.current);
      await reload();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setWorking(false);
    }
  };

  const confirmedCount = bindings.filter((item) => item.status === "confirmed").length;
  const suggestedCount = mappings.filter((item) => item.status === "candidate" && item.created_via === "recommended").length;
  const mappedObjectiveCount = new Set(
    mappings.filter((item) => item.status !== "rejected").map((item) => item.objective_id),
  ).size;
  const hasSuggestedBinding = bindings.some((item) => item.status === "candidate");

  return (
    <section className="rounded-xl border border-indigo-500/20 bg-indigo-500/5 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2"><Link2 className="h-4 w-4 text-indigo-600" /><h3 className="text-sm font-semibold">{tr("教材绑定与章节映射", "Textbook bindings and section mappings")}</h3></div>
        {hasSuggestedBinding || suggestedCount ? <button type="button" disabled={working} onClick={() => void confirmGeneratedScope()} className="rounded-lg bg-indigo-600 px-3 py-2 text-xs text-white disabled:opacity-50">{working ? <Loader2 className="inline h-3 w-3 animate-spin" /> : tr(`确认教材范围（${suggestedCount} 条章节建议）`, `Confirm textbook scope (${suggestedCount} section suggestions)`)}</button> : null}
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

      <div className="mt-3 overflow-hidden rounded-lg border border-[var(--border)] bg-[var(--background)]/60">
        <div className="flex items-center justify-between gap-3 border-b border-[var(--border)] px-3 py-2 text-[11px] text-[var(--muted-foreground)]">
          <span>{tr("知识点 → 教材章节", "Objective → textbook section")}</span>
          <span>{tr(`${mappedObjectiveCount}/${objectives.length} 个知识点 · ${mappingRows.length} 条映射`, `${mappedObjectiveCount}/${objectives.length} objectives · ${mappingRows.length} mappings`)}</span>
        </div>
        <div className="max-h-[360px] overflow-y-auto">
          {mappingRows.map((mapping) => {
            const section = sectionDetails.get(mapping.textbook_section_id);
            const statusClass = mapping.status === "confirmed"
              ? "bg-emerald-500/10 text-emerald-700"
              : mapping.status === "rejected"
                ? "bg-red-500/10 text-red-700"
                : "bg-amber-500/10 text-amber-700";
            const statusLabel = mapping.status === "confirmed"
              ? tr("已确认", "Confirmed")
              : mapping.status === "rejected"
                ? tr("已拒绝", "Rejected")
                : tr("推荐待确认", "Suggested");
            return (
              <div key={mapping.mapping_id} className="grid min-h-9 grid-cols-[minmax(0,1fr)_auto_minmax(0,1.4fr)_auto] items-center gap-2 border-b border-[var(--border)]/60 px-3 py-1.5 text-xs last:border-b-0">
                <span className="truncate" title={objectiveNames.get(mapping.objective_id) || mapping.objective_id}>
                  {objectiveNames.get(mapping.objective_id) || mapping.objective_id}
                </span>
                <span className="text-[var(--muted-foreground)]">→</span>
                <span className="truncate" title={section ? `${section.source} / ${section.path}` : mapping.textbook_section_id}>
                  {section ? `${section.source} / ${section.path}` : mapping.textbook_section_id}
                </span>
                <span className={`whitespace-nowrap rounded-full px-2 py-0.5 text-[10px] ${statusClass}`}>{statusLabel}</span>
              </div>
            );
          })}
          {!mappingRows.length ? (
            <p className="px-3 py-4 text-center text-xs text-[var(--muted-foreground)]">
              {tr("还没有章节映射，请在上方选择知识点和章节。", "No section mappings yet. Choose an objective and section above.")}
            </p>
          ) : null}
        </div>
      </div>
    </section>
  );
}
