# Security Policy

## Supported versions

Security fixes are made against the current default branch and the latest
published release. Older releases may be asked to upgrade before a fix can be
provided.

## Reporting a vulnerability

Please do not open a public issue for a suspected vulnerability or include
credentials, private prompts, user data, database dumps, or exploit details in
public discussions.

Use GitHub's private vulnerability reporting / Security Advisory flow for this
repository when it is available. If that channel is unavailable, contact the
maintainer privately at `bingxizhao39@gmail.com` with:

- the affected version or commit;
- the entry point and required configuration;
- reproduction steps with secrets and personal data removed;
- the expected impact and any known mitigation.

The maintainers will acknowledge the report, reproduce and triage it, and
coordinate disclosure after a fix or mitigation is available. Do not test
against systems or data you do not own or have permission to use.

## ExamMem-specific boundaries

- `EXAM_MEM_DATABASE_URL` is a process secret. Never commit it or place it in
  DeepTutor's ordinary JSON/YAML settings.
- ExamMem must use a dedicated PostgreSQL database. Do not point it at a
  DeepTutor internal, shared, staging, or production database unless that
  target has been explicitly approved for ExamMem.
- Uploaded syllabus and question-generation sources may contain private or
  copyrighted material. Use only content you are authorized to process.
- L1 events and audit streams are append-only. Report any path that permits an
  unaudited update/delete, cross-user Scope access, or bypass of the pinned
  Taxonomy/grader contract.

Operational setup and secret-safe database checks are documented in the
[ExamMem Runbook](docs/exam-mem/runbook.zh-CN.md).
