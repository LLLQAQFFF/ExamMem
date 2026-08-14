import SmartExamPrepShell from "@/components/exam-mem/SmartExamPrepShell";

export default function ExamMemLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return <SmartExamPrepShell>{children}</SmartExamPrepShell>;
}
