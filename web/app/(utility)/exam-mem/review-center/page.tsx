import LearningInsightsWorkbench from "@/components/exam-mem/LearningInsightsWorkbench";
import { Suspense } from "react";

export default function ReviewCenterPage() {
  return (
    <main className="h-full min-h-0 overflow-y-auto bg-[var(--background)] [scrollbar-gutter:stable]">
      <Suspense>
        <LearningInsightsWorkbench mode="review" />
      </Suspense>
    </main>
  );
}
