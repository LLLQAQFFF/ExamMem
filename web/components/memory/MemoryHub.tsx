"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Brain, Layers, Network, RefreshCw, Workflow } from "lucide-react";
import { useTranslation } from "react-i18next";

import { apiFetch, apiUrl } from "@/lib/api";
import MemoryArchivedBanner from "@/components/memory/MemoryArchivedBanner";
import ThreeLayerMemoryOverview from "@/components/memory/ThreeLayerMemoryOverview";

interface DocOverview {
  layer: "L2" | "L3";
  key: string;
  exists: boolean;
  updated_at: string | null;
  entry_count: number;
  backlog: number;
}

interface OverviewResponse {
  docs: DocOverview[];
  backups: string[];
}

interface SnapshotResponse {
  surface: string;
  entities: unknown[];
}

const SURFACES = [
  "chat",
  "notebook",
  "quiz",
  "kb",
  "book",
  "partner",
  "cowriter",
] as const;

const L3_VISIBLE = ["recent", "profile", "scope"] as const;

export default function MemoryHub() {
  const { t } = useTranslation();
  const [overview, setOverview] = useState<OverviewResponse | null>(null);
  const [l1Total, setL1Total] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [ovRes, ...l1Counts] = await Promise.all([
        apiFetch(apiUrl("/api/v1/memory/overview")).then((r) => r.json()),
        ...SURFACES.map((s) =>
          apiFetch(apiUrl(`/api/v1/memory/snapshot/${s}`))
            .then((r) => r.json())
            .then((d: SnapshotResponse) => d?.entities?.length ?? 0)
            .catch(() => 0),
        ),
      ]);
      setOverview(ovRes as OverviewResponse);
      setL1Total(l1Counts.reduce<number>((acc, n) => acc + (n as number), 0));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const l2Docs = (overview?.docs || []).filter((d) => d.layer === "L2");
  const l3Docs = (overview?.docs || []).filter(
    (d) => d.layer === "L3" && d.key !== "preferences",
  );
  const l2Total = l2Docs.reduce((acc, d) => acc + d.entry_count, 0);
  const l3Total = l3Docs.reduce((acc, d) => acc + d.entry_count, 0);
  const latestBackup = overview?.backups?.[overview.backups.length - 1] ?? null;

  return (
    <div className="space-y-10">
      <header className="space-y-3">
        <div className="flex items-center gap-3">
          <span className="grid h-10 w-10 place-items-center rounded-xl bg-[var(--primary)]/10 text-[var(--primary)]">
            <Brain className="h-5 w-5" />
          </span>
          <h1 className="font-serif text-[28px] font-semibold tracking-tight text-[var(--foreground)] md:text-[32px]">
            {t("Memory")}
          </h1>
        </div>
        <p className="max-w-2xl text-[14px] text-[var(--muted-foreground)] md:text-[15px]">
          {t(
            "Everything DeepTutor remembers about you, organised across three layers. Click into any layer to inspect or curate it.",
          )}
        </p>
        <div className="flex items-center gap-3 text-[12px] text-[var(--muted-foreground)]">
          <button
            type="button"
            onClick={() => void load()}
            className="inline-flex items-center gap-1.5 rounded-md border border-[var(--border)] bg-[var(--background)] px-2.5 py-1 transition hover:bg-[var(--muted)]"
          >
            <RefreshCw
              className={loading ? "h-3 w-3 animate-spin" : "h-3 w-3"}
            />
            {t("Refresh")}
          </button>
          <Link
            href="/settings/memory"
            className="inline-flex items-center gap-1.5 rounded-md border border-transparent px-2.5 py-1 transition hover:bg-[var(--muted)]"
          >
            {t("Memory settings")}
          </Link>
        </div>
      </header>

      <ThreeLayerMemoryOverview
        layers={[
          {
            href: "/memory/l1",
            icon: Layers,
            title: t("L1 · Workspace mirror"),
            tag: t("Live"),
            stat: l1Total === null ? "…" : l1Total.toLocaleString(),
            statLabel: t("entities tracked"),
            detail: t(
              "Snapshot of your live workspace across {{n}} surfaces. Refresh to record changes.",
              { n: SURFACES.length },
            ),
          },
          {
            href: "/memory/l2",
            icon: Workflow,
            title: t("L2 · Per-surface summaries"),
            tag: t("Curated"),
            stat: l2Total.toLocaleString(),
            statLabel: t("facts across {{n}} surfaces", {
              n: l2Docs.length || SURFACES.length,
            }),
            detail: t(
              "Surface-specific facts extracted by the consolidator. Run Update / Audit / Dedup per doc.",
            ),
          },
          {
            href: "/memory/l3",
            icon: Network,
            title: t("L3 · Cross-surface knowledge"),
            tag: t("Synthesis"),
            stat: l3Total.toLocaleString(),
            statLabel: t("propositions across {{n}} slots", {
              n: l3Docs.length || L3_VISIBLE.length,
            }),
            detail: t(
              "Cross-surface synthesis: profile, recent timeline, knowledge scope. Hedged claims with L2 evidence.",
            ),
          },
        ]}
        graph={{
          href: "/memory/graph",
          title: t("Memory graph"),
          tag: t("New"),
          detail: t(
            "See all three layers at once — L3 synthesis at the centre, L2 facts in the middle, L1 traces on the outside. Hover any node for a preview.",
          ),
        }}
      />

      <MemoryArchivedBanner latestBackup={latestBackup} variant="compact" />
    </div>
  );
}
