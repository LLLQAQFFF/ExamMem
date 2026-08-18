import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";

function source(file: string): string {
  return readFileSync(path.join(process.cwd(), file), "utf8");
}

test("ExamMem markdown enables math while rejecting raw HTML", () => {
  const renderer = source("components/exam-mem/ExamMemMarkdown.tsx");

  assert.match(renderer, /<MarkdownRenderer/);
  assert.match(renderer, /variant="compact"/);
  assert.match(renderer, /enableMath/);
  assert.match(renderer, /enableMermaid=\{false\}/);
  assert.match(renderer, /allowHtml=\{false\}/);
});

test("ExamMem practice renders question and grading content as markdown", () => {
  const practice = source("components/exam-mem/PracticeWorkbench.tsx");

  assert.match(practice, /<ExamMemMarkdown[\s\S]*content=\{question\.stem\}/);
  assert.match(
    practice,
    /<ExamMemMarkdown[\s\S]*content=\{grade\.evidence\.join\("\\n\\n"\)\}/,
  );
  assert.match(
    practice,
    /<ExamMemMarkdown[\s\S]*content=\{diagnosis\.explanation\}/,
  );
});

test("ExamMem review and learning archive render persisted evidence as markdown", () => {
  const review = source("components/exam-mem/ExamReviewWorkbench.tsx");
  const archive = source("components/exam-mem/LearningMemoryWorkbench.tsx");

  assert.match(
    review,
    /content=\{checkpoint\.question\?\.stem \?\? practiceStateLabel\(checkpoint\.step_state, zh\)\}/,
  );
  assert.match(review, /value=\{checkpoint\.submitted_answer\?\.answer\}/);
  assert.match(review, /function AnswerBlock[\s\S]*<ExamMemMarkdown/);
  assert.match(
    review,
    /content=\{checkpoint\.grade_result\.evidence\.join\("\\n\\n"\)\}/,
  );
  assert.match(archive, /value=\{row\.evidence\.detail\.submitted_answer\?\.answer\}/);
  assert.match(archive, /function EvidenceBlock[\s\S]*<ExamMemMarkdown/);
});
