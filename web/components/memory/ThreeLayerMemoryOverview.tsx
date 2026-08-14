"use client";

import Link from "next/link";
import { ArrowRight, Sparkles, type LucideIcon } from "lucide-react";

export interface MemoryLayerCardModel {
  href: string;
  icon: LucideIcon;
  title: string;
  tag: string;
  stat: string;
  statLabel: string;
  detail: string;
}

export interface MemoryGraphCalloutModel {
  href: string;
  title: string;
  tag: string;
  detail: string;
}

interface ThreeLayerMemoryOverviewProps {
  layers: readonly MemoryLayerCardModel[];
  graph?: MemoryGraphCalloutModel;
}

export default function ThreeLayerMemoryOverview({
  layers,
  graph,
}: ThreeLayerMemoryOverviewProps) {
  return (
    <div className="space-y-10">
      <div className="grid grid-cols-1 gap-5 md:grid-cols-3">
        {layers.map((layer) => (
          <LayerCard key={layer.title} {...layer} />
        ))}
      </div>
      {graph ? <GraphCallout {...graph} /> : null}
    </div>
  );
}

function GraphCallout({ href, title, tag, detail }: MemoryGraphCalloutModel) {
  return (
    <Link
      href={href}
      className="group relative block overflow-hidden rounded-2xl border border-[var(--border)] bg-[var(--card)] p-6 transition hover:-translate-y-[1px] hover:border-[var(--primary)]/40 hover:shadow-sm"
    >
      <div
        className="pointer-events-none absolute inset-0 opacity-80"
        style={{
          background:
            "radial-gradient(ellipse 60% 80% at 92% 50%, color-mix(in srgb, var(--primary) 16%, transparent), transparent 70%)",
        }}
      />
      <div className="relative flex items-center gap-5">
        <span className="grid h-12 w-12 shrink-0 place-items-center rounded-xl bg-[var(--primary)]/10 text-[var(--primary)]">
          <Sparkles className="h-5 w-5" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="text-[15px] font-semibold text-[var(--foreground)]">
              {title}
            </h3>
            <span className="rounded-full border border-[var(--border)] bg-[var(--background)]/60 px-2 py-0.5 text-[10.5px] font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
              {tag}
            </span>
          </div>
          <p className="mt-1 text-[13px] leading-relaxed text-[var(--muted-foreground)]">
            {detail}
          </p>
        </div>
        <ArrowRight className="hidden h-4 w-4 shrink-0 text-[var(--primary)] transition group-hover:translate-x-0.5 md:block" />
      </div>
    </Link>
  );
}

function LayerCard({
  href,
  icon: Icon,
  title,
  tag,
  stat,
  statLabel,
  detail,
}: MemoryLayerCardModel) {
  return (
    <Link
      href={href}
      className="group flex flex-col gap-4 rounded-2xl border border-[var(--border)] bg-[var(--card)] p-6 transition hover:-translate-y-[1px] hover:border-[var(--primary)]/40 hover:shadow-sm"
    >
      <div className="flex items-start justify-between">
        <span className="grid h-9 w-9 place-items-center rounded-lg bg-[var(--primary)]/10 text-[var(--primary)]">
          <Icon className="h-4 w-4" />
        </span>
        <span className="rounded-full border border-[var(--border)] bg-[var(--background)] px-2 py-0.5 text-[10.5px] font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
          {tag}
        </span>
      </div>
      <h2 className="text-[15px] font-semibold text-[var(--foreground)]">
        {title}
      </h2>
      <div className="flex items-baseline gap-2">
        <span className="text-[28px] font-semibold tracking-tight text-[var(--foreground)]">
          {stat}
        </span>
        <span className="text-[12px] text-[var(--muted-foreground)]">
          {statLabel}
        </span>
      </div>
      <p className="text-[13px] leading-relaxed text-[var(--muted-foreground)]">
        {detail}
      </p>
      <div className="mt-auto inline-flex items-center gap-1 text-[12px] font-medium text-[var(--primary)] opacity-0 transition group-hover:opacity-100">
        <span>{tag}</span>
        <ArrowRight className="h-3.5 w-3.5" />
      </div>
    </Link>
  );
}
