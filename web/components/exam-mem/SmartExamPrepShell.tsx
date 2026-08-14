"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  BookOpenCheck,
  BrainCircuit,
  FileCheck2,
  Settings2,
  Sparkles,
  GraduationCap,
  type LucideIcon,
} from "lucide-react";
import { useTranslation } from "react-i18next";

interface PrepSection {
  href: string;
  label: string;
  icon: LucideIcon;
}

const PREP_SECTIONS: PrepSection[] = [
  { href: "/exam-mem/learning", label: "Learning Paths", icon: GraduationCap },
  { href: "/exam-mem/practice", label: "Practice", icon: BookOpenCheck },
  {
    href: "/exam-mem/memories",
    label: "Learning Memory",
    icon: BrainCircuit,
  },
  { href: "/exam-mem/review", label: "Exam Review", icon: FileCheck2 },
  {
    href: "/exam-mem/configuration",
    label: "Configuration",
    icon: Settings2,
  },
];

export default function SmartExamPrepShell({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const pathname = usePathname();
  const { t } = useTranslation();

  return (
    <div className="flex h-full min-h-0 flex-col bg-[var(--background)]">
      <header className="shrink-0 border-b border-[var(--border)] bg-[var(--card)]/60">
        <div className="flex min-w-0 items-center gap-3 overflow-x-auto px-4 py-2 sm:px-6">
          <Link
            href="/exam-mem/practice"
            className="flex shrink-0 items-center gap-2 font-semibold text-[var(--foreground)]"
          >
            <span className="grid h-8 w-8 place-items-center rounded-lg bg-[var(--primary)]/10 text-[var(--primary)]">
              <Sparkles className="h-4 w-4" />
            </span>
            <span>{t("Smart Exam Prep")}</span>
          </Link>
          <span className="h-5 w-px shrink-0 bg-[var(--border)]" />
          <nav
            aria-label={t("Smart Exam Prep sections")}
            className="flex min-w-max items-center gap-1"
          >
            {PREP_SECTIONS.map((item) => {
              const active = pathname === item.href;
              const Icon = item.icon;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  aria-current={active ? "page" : undefined}
                  className={`inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs transition-colors ${
                    active
                      ? "bg-[var(--accent)] font-medium text-[var(--foreground)]"
                      : "text-[var(--muted-foreground)] hover:bg-[var(--muted)]/60 hover:text-[var(--foreground)]"
                  }`}
                >
                  <Icon className="h-3.5 w-3.5" />
                  {t(item.label)}
                </Link>
              );
            })}
          </nav>
        </div>
      </header>
      <div className="min-h-0 flex-1 overflow-hidden">{children}</div>
    </div>
  );
}
