import LearningMemoryWorkbench from "@/components/exam-mem/LearningMemoryWorkbench";
import { Suspense } from "react";

export default function ExamMemMemoriesPage() {
  return (
    <main className="h-full min-h-0 overflow-y-auto bg-[var(--background)] [scrollbar-gutter:stable]">
      <Suspense>
        <LearningMemoryWorkbench />
      </Suspense>
    </main>
  );
}
