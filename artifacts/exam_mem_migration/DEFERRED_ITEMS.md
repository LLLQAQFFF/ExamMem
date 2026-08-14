# ExamMem deferred items after migration

These items are deliberately outside the migrated current loop. Their absence
is a product boundary, not an unfinished fallback path.

## Deferred product capabilities

| Item | Why deferred | Entry condition |
| --- | --- | --- |
| File/document ingestion | Multi-source source identity, parsing provenance, authorization and retention are not part of the frozen current loop. | Approved Stage 08 design, source contract and end-to-end acceptance. |
| Video, image and audio ingestion | Requires modality-specific extraction, timestamps/regions, evidence rendering and model-quality evaluation. | Separate modality ADRs and privacy/cost controls. |
| Notes and PPT ingestion | Requires source versioning and conflict semantics; must not be treated as plain Practice evidence. | Multi-source provenance model approved. |
| Learning Journey Memory | Has a different longitudinal aggregation and product lifecycle from current Learning Memory. | Dedicated schema/invariants and migration plan. |
| Course question answering | Is not Exam Practice and must not reuse Practice sessions or grading checkpoints. | Separate capability and session-surface contract. |
| Source-driven question generation | Requires content licensing, question/rubric version governance and evaluation. | Approved question-content pipeline and quality gates. |
| Stage 08 optimization/evaluation | No experiment ledger, online metrics or model-quality benchmark was authorized in this migration. | Explicit Stage 08 scope and reproducible evaluation baseline. |

## Known current limitations

- The controlled question catalog remains the migrated Stage 07 product set;
  there is no large-scale content administration workflow.
- DeepTutor Native Quiz results are not automatically promoted to Learning
  Memory evidence. Their current envelope has no pinned four-dimensional exam
  Scope, canonical Taxonomy/knowledge-point identity, controlled question and
  grading-policy revision, or sufficient provenance. A neutral assessment
  contract plus explicit Smart Exam Prep opt-in is required before integration.
- Automated tests fix external LLM/embedding results. They prove real transport,
  registry, workflow and database behavior, not live provider accuracy, latency
  or cost.
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
- Migration `0007` has no automatic destructive downgrade when review rows
  exist. Audit retention/archival requires an explicit operational decision.

None of these limitations authorizes direct writes to DeepTutor internal stores,
compatibility fallbacks, `if exam_mem` branches in Core, or changes to frozen
migrations `0001`–`0006`.
