# ExamMem deferred items after migration

These items are deliberately outside the migrated current loop. Their absence
is a product boundary, not an unfinished fallback path.

## Deferred product capabilities

| Item | Why deferred | Entry condition |
| --- | --- | --- |
| Durable file/document ingestion and tutoring retrieval | Imported PDF/TXT/Markdown or URL content is used only to derive a reviewed hierarchy; only source metadata/hash is retained. Reusing source content in later tutoring requires authorization, versioning, retention, citation and retrieval provenance. Practice attachments remain transient context for one explicitly requested generation. | Approved source/RAG contract and end-to-end acceptance. |
| Video, image and audio ingestion | Requires modality-specific extraction, timestamps/regions, evidence rendering and model-quality evaluation. | Separate modality ADRs and privacy/cost controls. |
| Notes and PPT ingestion | Requires source versioning and conflict semantics; must not be treated as plain Practice evidence. | Multi-source provenance model approved. |
| Learning Journey Memory | Has a different longitudinal aggregation and product lifecycle from current Learning Memory. | Dedicated schema/invariants and migration plan. |
| Course question answering | Is not Exam Practice and must not reuse Practice sessions or grading checkpoints. | Separate capability and session-surface contract. |
| Large-scale source-driven question pipeline | The narrow current feature pins native Quiz output, canonical Taxonomy identity, source hashes and rubric inside one Practice checkpoint. Reusable content libraries, automatic ingestion, licensing workflow and quality evaluation remain absent. | Approved question-content pipeline and quality gates. |
| Stage 08 optimization/evaluation | No experiment ledger, online metrics or model-quality benchmark was authorized in this migration. | Explicit Stage 08 scope and reproducible evaluation baseline. |

## Known current limitations

- Generated questions are immutable assessment-version and checkpoint snapshots.
  The same assessment version can be attempted repeatedly, but there is no
  shared question bank, manual item editor, content administration or licensing UI.
- PDF, TXT and Markdown attachments are passed through the neutral Host Turn
  contract to one transient native Quiz session. ExamMem persists only the
  generated question/rubric plus filename, MIME type and SHA-256 provenance;
  it does not persist or index the source content. DOCX, PPT/PPTX, notes, image,
  video and audio ingestion remain unsupported.
- Arbitrary DeepTutor Native Quiz history is not automatically promoted to
  Learning Memory. Only the explicit Smart Exam Prep generation entry pins a
  controlled four-dimensional Scope and canonical Taxonomy identity; Learning
  Memory evidence is created later by an ExamMem answer/grade workflow, never
  by trusting Native Quiz's own correctness result.
- Automated tests fix external LLM/embedding results. They prove real transport,
  registry, workflow and database behavior, not live provider accuracy, latency
  or cost.
- Practice generation reports real pipeline stages and validated-question
  counts, but it does not predict an ETA or fabricate an overall percentage for
  open-ended exploration and planning. Token-level model progress is not part of
  the public ExamMem contract.
- Browser pending-request recovery is tab-local. Durable recovery is the
  server-side Practice history/Resume path, which creates a new Host transport
  session rather than restoring a closed WebSocket.
- Grade Overturn is API-only because it requires a complete structured
  replacement Grade; the UI exposes dispute and administrator Uphold.
- Issues are derived from authoritative facts and intentionally have no mutable
  assignment, comments, SLA or notification ledger.
- Plugin health reports lifecycle assembly, not active PostgreSQL connectivity;
  operations must pair it with migration/current and authenticated read probes.
- Saved configuration becomes Effective after process restart; an in-progress
  exam always uses its Pinned snapshot.
- Ordinary Chat is analyzed only after the learner explicitly selects a
  conversation. Background scanning is intentionally absent so small talk and
  unrelated private context are not silently processed. Confirmed Chat clues
  remain outside formal L1/L2/L3 and do not affect mastery.
- L3 displays only the current rebuildable Student Model. Historical L3
  projection browsing and single-taxonomy-version rebuild are deferred. The UI
  explicitly labels current L3 as a plan+subject projection across syllabus
  versions; L2 provides the authoritative version timeline and provenance.
- Free-form learner reflections after an assessment are not yet persisted.
  Adding them requires an append-only, idempotent and explicitly confirmed
  contract before any reflection may influence L2.
- Migrations `0007`～`0011` have no automatic destructive downgrade when review,
  study-plan/session-link, assessment/attempt or Agent observation rows exist.
  Assessment archive is now reversible and preserves evidence; hard deletion and
  compensating removal of an assessment's prior Learning Memory impact remain
  separate, explicitly governed operations.

None of these limitations authorizes direct writes to DeepTutor internal stores,
compatibility fallbacks, `if exam_mem` branches in Core, or changes to frozen
migrations `0001`–`0006`.
