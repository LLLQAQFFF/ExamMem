"use client";

import { AlertTriangle, Database, RefreshCw, Search } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { apiFetch, apiUrl } from "@/lib/api";
import {
  LEARNING_MEMORY_NAMESPACES,
  learningMemoryQuery,
  memoryValueSummary,
  type LearningMemoryDetail,
  type LearningMemoryEvidence,
  type LearningMemorySummary,
} from "@/lib/exam-mem-memory";

const EXAM_ID = "postgraduate_entrance_exam";
const SUBJECT_ID = "math_1";

export default function LearningMemoryWorkbench() {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const [memories, setMemories] = useState<LearningMemorySummary[]>([]);
  const [selected, setSelected] = useState<LearningMemoryDetail | null>(null);
  const [evidence, setEvidence] = useState<LearningMemoryEvidence | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const responses = await Promise.all(
        LEARNING_MEMORY_NAMESPACES.map(async (namespace) => {
          const params = learningMemoryQuery(EXAM_ID, SUBJECT_ID, namespace, query);
          const response = await apiFetch(
            apiUrl(`/api/v1/exam-mem/memories?${params}`),
          );
          if (!response.ok) throw new Error(`Memory query failed (${response.status}).`);
          const payload = (await response.json()) as {
            memories: LearningMemorySummary[];
          };
          return payload.memories;
        }),
      );
      setMemories(responses.flat());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("Memory query failed."));
    } finally {
      setLoading(false);
    }
  }, [query, t]);

  useEffect(() => {
    void load();
  }, [load]);

  const openDetail = async (summary: LearningMemorySummary) => {
    setError(null);
    const memory = summary.memory;
    const params = learningMemoryQuery(
      EXAM_ID,
      SUBJECT_ID,
      memory.scope.memory_namespace,
    );
    const id = encodeURIComponent(memory.memory_id);
    try {
      const [detailResponse, evidenceResponse] = await Promise.all([
        apiFetch(apiUrl(`/api/v1/exam-mem/memories/${id}?${params}`)),
        apiFetch(apiUrl(`/api/v1/exam-mem/memories/${id}/evidence?${params}`)),
      ]);
      if (!detailResponse.ok || !evidenceResponse.ok) {
        throw new Error(t("Memory detail or evidence could not be loaded."));
      }
      setSelected((await detailResponse.json()) as LearningMemoryDetail);
      setEvidence((await evidenceResponse.json()) as LearningMemoryEvidence);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("Memory detail failed."));
    }
  };

  return (
    <div className="mx-auto max-w-7xl space-y-6 px-6 py-10 md:px-10">
      <header className="space-y-2">
        <div className="flex items-center gap-3">
          <span className="grid h-10 w-10 place-items-center rounded-xl bg-[var(--primary)]/10 text-[var(--primary)]">
            <Database className="h-5 w-5" />
          </span>
          <div>
            <h1 className="font-serif text-2xl font-semibold">{t("ExamMem Learning Memory")}</h1>
            <p className="text-sm text-[var(--muted-foreground)]">{t("Typed lifecycle state, version chains, provenance, and correction eligibility.")}</p>
          </div>
        </div>
        <div className="flex items-center gap-2 rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-700 dark:text-amber-300">
          <AlertTriangle className="h-4 w-4 shrink-0" />
          {t("Learning Memory is independent from DeepTutor Native Memory. Historical evidence is append-only and never edited in place.")}
        </div>
      </header>

      <section className="grid gap-3 rounded-xl border border-[var(--border)] bg-[var(--card)] p-4 md:grid-cols-[1fr_2fr_auto]">
        <div className="rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-sm">
          {EXAM_ID} / {SUBJECT_ID}
        </div>
        <label className="flex items-center gap-2 rounded-lg border border-[var(--border)] bg-[var(--background)] px-3">
          <Search className="h-4 w-4 text-[var(--muted-foreground)]" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            className="min-w-0 flex-1 bg-transparent py-2 text-sm outline-none"
            placeholder={t("Search slot, value, or Memory ID")}
          />
        </label>
        <button type="button" onClick={() => void load()} className="inline-flex items-center justify-center gap-2 rounded-lg border border-[var(--border)] px-3 py-2 text-sm">
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          {t("Refresh")}
        </button>
      </section>

      {error ? <p className="rounded-lg bg-red-500/10 px-3 py-2 text-sm text-red-600">{error}</p> : null}

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(340px,0.8fr)]">
        <section className="space-y-3">
          <p className="text-xs text-[var(--muted-foreground)]">{t("{{count}} scoped versions", { count: memories.length })}</p>
          {!loading && memories.length === 0 ? (
            <p className="rounded-xl border border-dashed border-[var(--border)] p-8 text-center text-sm text-[var(--muted-foreground)]">{t("No Learning Memory matches this Scope and query.")}</p>
          ) : null}
          {memories.map((summary) => {
            const memory = summary.memory;
            return (
              <article key={memory.memory_id} className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2 text-xs">
                      <span className="rounded-full bg-[var(--muted)] px-2 py-1">{memory.scope.memory_namespace}</span>
                      <span className="rounded-full bg-[var(--muted)] px-2 py-1">{memory.lifecycle_state}</span>
                      <span>v{memory.version}</span>
                    </div>
                    <p className="mt-2 break-all font-mono text-xs text-[var(--muted-foreground)]">{memory.slot_key}</p>
                    <p className="mt-2 text-sm">{memoryValueSummary(memory.value)}</p>
                  </div>
                  <button type="button" onClick={() => void openDetail(summary)} className="rounded-lg border border-[var(--border)] px-3 py-1.5 text-xs">{t("View evidence")}</button>
                </div>
              </article>
            );
          })}
        </section>

        <aside className="min-w-0 rounded-xl border border-[var(--border)] bg-[var(--card)] p-5">
          {selected ? (
            <div className="space-y-5">
              <div>
                <h2 className="text-sm font-semibold">{t("Version chain")}</h2>
                <div className="mt-2 space-y-2">
                  {selected.version_chain.map((item) => (
                    <div key={item.memory.memory_id} className="rounded-lg bg-[var(--muted)]/50 p-3 text-xs">
                      <p>{item.memory.lifecycle_state} · {t("version {{version}}", { version: item.memory.version })} · {t("row {{rowVersion}}", { rowVersion: item.row_version })}</p>
                      <p className="mt-1 break-all font-mono text-[var(--muted-foreground)]">{item.memory.memory_id}</p>
                    </div>
                  ))}
                </div>
              </div>
              <div>
                <h2 className="text-sm font-semibold">{t("Provenance evidence")}</h2>
                <pre className="mt-2 max-h-80 overflow-auto rounded-lg bg-[var(--muted)]/50 p-3 text-xs">{JSON.stringify(evidence?.events ?? [], null, 2)}</pre>
              </div>
              <p className="text-xs text-[var(--muted-foreground)]">
                {selected.correction_allowed
                  ? t("This version is eligible for an explicit, confirmed correction.")
                  : t("This historical version is read-only.")}
              </p>
            </div>
          ) : (
            <p className="text-sm text-[var(--muted-foreground)]">{t("Select a Memory to inspect its complete version and evidence chain.")}</p>
          )}
        </aside>
      </div>
    </div>
  );
}
