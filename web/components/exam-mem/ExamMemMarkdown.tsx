"use client";

import MarkdownRenderer from "@/components/common/MarkdownRenderer";

export default function ExamMemMarkdown({
  content,
  className = "",
}: {
  content?: string | null;
  className?: string;
}) {
  if (!content?.trim()) return null;
  return (
    <MarkdownRenderer
      content={content}
      className={`min-w-0 break-words ${className}`}
      variant="compact"
      enableMath
      enableMermaid={false}
      allowHtml={false}
    />
  );
}
