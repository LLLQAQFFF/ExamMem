import ExamReviewWorkbench from "@/components/exam-mem/ExamReviewWorkbench";
import { Suspense } from "react";

export default function ExamReviewPage() {
  return (
    <main className="h-full min-h-0 overflow-y-auto bg-[var(--background)] [scrollbar-gutter:stable]">
      <Suspense>
        <ExamReviewWorkbench />
      </Suspense>
    </main>
  );
}
