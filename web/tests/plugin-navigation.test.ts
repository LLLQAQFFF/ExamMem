import assert from "node:assert/strict";
import test from "node:test";

import { normalizePluginNavigation } from "../lib/plugin-navigation";

test("plugin navigation accepts only local declared routes and sorts by contribution order", () => {
  assert.deepEqual(
    normalizePluginNavigation([
      { href: "/memory-plugin", label: "Memory", icon: "BrainCircuit", section: "secondary", order: 50 },
      { href: "https://bad.example", label: "Bad", icon: "Puzzle", section: "primary", order: 1 },
      { href: "/practice-plugin", label: "Practice", icon: "BookOpenCheck", section: "primary", order: 20 },
    ]).map((item) => item.href),
    ["/practice-plugin", "/memory-plugin"],
  );
});
