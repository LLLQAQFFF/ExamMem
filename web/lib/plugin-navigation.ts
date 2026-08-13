import {
  BookOpenCheck,
  BrainCircuit,
  Puzzle,
  type LucideIcon,
} from "lucide-react";

import { apiFetch, apiUrl } from "@/lib/api";

export interface PluginNavigationItem {
  href: string;
  label: string;
  icon: string;
  section: "primary" | "secondary";
  order: number;
}

const PLUGIN_ICONS: Record<string, LucideIcon> = {
  BookOpenCheck,
  BrainCircuit,
};

export function pluginNavigationIcon(name: string): LucideIcon {
  return PLUGIN_ICONS[name] ?? Puzzle;
}

export function normalizePluginNavigation(value: unknown): PluginNavigationItem[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((item): item is Record<string, unknown> => {
      if (!item || typeof item !== "object") return false;
      return (
        typeof item.href === "string" &&
        item.href.startsWith("/") &&
        !item.href.startsWith("//") &&
        typeof item.label === "string" &&
        item.label.trim().length > 0 &&
        typeof item.icon === "string" &&
        (item.section === "primary" || item.section === "secondary")
      );
    })
    .map((item) => ({
      href: item.href as string,
      label: (item.label as string).trim(),
      icon: item.icon as string,
      section: item.section as "primary" | "secondary",
      order: typeof item.order === "number" ? item.order : 100,
    }))
    .sort((left, right) => left.order - right.order || left.href.localeCompare(right.href));
}

export async function loadPluginNavigation(): Promise<PluginNavigationItem[]> {
  const response = await apiFetch(apiUrl("/api/v1/plugins/list"));
  if (!response.ok) return [];
  const payload = (await response.json()) as { navigation?: unknown };
  return normalizePluginNavigation(payload.navigation);
}
