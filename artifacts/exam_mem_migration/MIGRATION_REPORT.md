# ExamMem → DeepTutor migration report

Date: 2026-08-14
Target branch: `feat/exam-mem-plugin-migration`
DeepTutor base: `9228d10abc114ec87321c6861e7e384db022e8ce`
Frozen ExamMem executable baseline: `747958725b6e681a3a846a0430b5a21deb163188`

## Outcome

The current ExamMem product loop is migrated into DeepTutor as a first-party,
compile-time full-stack plugin. DeepTutor Core owns only neutral contribution
points; no file below `deeptutor/` imports `exam_mem`. The plugin owns the
Practice domain, Learning Memory, lifecycle, storage, migrations, API assembly
and Web pages. ExamMem PostgreSQL is the business source of truth and has no
foreign key or implicit fallback to a DeepTutor internal database or Native
Memory.

The delivered loop is:

```text
Browser HTTP / Python SDK / unified WebSocket
  -> DeepTutor Turn Host
  -> Capability Registry: exam_practice
  -> ExamMem PracticeRuntimeProvider
  -> Question -> Grade -> Taxonomy mapping -> Diagnosis
  -> selected Memory Backend
  -> Recommendation -> checkpoint + append-only Trace
  -> server-side Resume / Correction / Review / derived Issues

Host Mastery Path -> Smart Exam Prep Learning Paths
  -> controlled exam Scope + canonical knowledge point
  -> optional transient PDF/TXT/Markdown context
  -> neutral Host Turn -> native Quiz generation
  -> immutable per-Practice question catalog in checkpoint
  -> the same Grade -> Memory -> Recommendation loop above
```

External model results are deterministic fakes in automated acceptance tests;
the registry, transport, workflow, transaction, PostgreSQL and response
boundaries are real. The tests therefore prove wiring and invariants, not live
model quality or provider availability.

## Migration classification

| Class | Migrated result |
| --- | --- |
| ExamMem-owned domain | Taxonomy, normalization, `slot_key`, four-dimensional Memory Scope, contracts, five Backends, L1/L2/L3, lifecycle policy/applier, Decision Journal, Change Log, compensation, Practice workflow, checkpoint, Trace, Review and read models live under `exam_mem/`. |
| Neutral Host hooks | Full-stack plugin manifest/discovery/lifecycle, capability/tool/router/settings/migration/navigation contributions, Host service ports, and `session_surface` filtering live under `deeptutor/`. These contracts do not name ExamMem. |
| Fork coupling not migrated | Fork feature flags, built-in registry hard-coding, direct Native Memory formats, shared/internal database access, duplicated Host configuration, and any `if exam_mem` branch in Core were discarded. |

## Preserved contracts

- Taxonomy IDs and versioned normalization remain ExamMem-owned. `slot_key` and
  four-dimensional Learning Memory Scope are validated before persistence.
- L1 and the audit streams are append-only. L2 writes retain provenance and CAS
  transaction semantics. L3 remains a rebuildable projection, never truth.
- Lifecycle states, deterministic policy, Decision Journal, Change Log,
  compensation and contested evidence semantics remain intact.
- `none`, `native`, `append_only`, `vector` and `lifecycle` use one workflow and
  fail closed when their required dependency is unavailable; there is no
  fallback to another mode.
- Practice checkpoint, Trace and idempotency survive response loss. Runtime
  backend/configuration is pinned per practice session.
- Grade Artifact reuse is Scope-bound and reuses only grading computation; each
  answer still executes diagnosis and Memory side effects.
- Grade Review is separate from Learning Memory Correction. The only new schema
  fact is append-only `grade_review_events`, justified by
  `ADR_0007_GRADE_REVIEWS.md` because a new dispute/disposition cannot be
  derived from existing facts.

## Product boundaries

- Chat sessions use `session_surface=chat`; Practice sessions use
  `session_surface=exam_practice`. Chat Recents, search/detail and resume never
  surface Practice sessions.
- DeepTutor Native Memory remains a Host feature. In the `native` comparison
  arm ExamMem calls a neutral Host port; Native Memory is not Learning Memory
  truth and is not queried by ExamMem repositories.
- Learning Memory is ExamMem PostgreSQL data with explicit Scope, lifecycle,
  provenance and correction semantics. Corrections append evidence; they do not
  overwrite history.
- Review derives checkpoints, Trace, lifecycle and audit facts. Issues are
  derived views (`workflow_failure`, `grade_disputed`, `contested_evidence`,
  `projection_pending`, `memory_inaccurate`), not a second mutable issue ledger.
- Host Mastery Path remains the learning-progress source of truth. Smart Exam
  Prep owns its learner-facing placement and links objectives to independent
  Practice; it does not copy Host progress into Learning Memory.
- Native Quiz is used only as an explicitly requested question generator.
  ExamMem maps the selected objective to its canonical Taxonomy, pins the full
  question/rubric catalog and source SHA-256 provenance in the Practice
  checkpoint, and grades answers itself. Native Quiz correctness is ignored.
- Multiple attempts share one controlled exam/subject Scope while using
  different Practice session and Trace identities. Attempt numbers are a
  derived ordered read model, not a mutable counter.

## Database and migration result

- `EXAM_MEM_DATABASE_URL` is required at the engine boundary and accepts only a
  complete `postgresql+asyncpg` URL. It is never persisted by this repository.
- Frozen migrations `0001`–`0006` match the source baseline byte-for-byte.
- The single target head is `0007_grade_reviews`.
- A new empty PostgreSQL database upgraded linearly from base through all seven
  revisions and produced 13 public tables including `alembic_version` and six
  distinct append-only triggers.
- Integration tests use random schemas and transactions. Final audit found no
  random schema. The reused local demo database retained its pre-existing two
  Practice checkpoints and seven Trace spans; the acceptance suite did not
  clear or repurpose those public rows.
- No DeepTutor SQLite, PocketBase or Native Memory store is read or written by
  an ExamMem repository. Host entry tests use isolated temporary Host storage.

## Acceptance evidence

| Category | Result |
| --- | --- |
| Host with ExamMem disabled and DSN absent | `3836 passed, 9 skipped`; plugin probe returned `loaded_plugins=[]` |
| Full repository with ExamMem and isolated PostgreSQL | `4268 passed, 9 skipped` |
| CP6 focused backend/entry/product suite | `484 passed` |
| Five Backend matrix | `33 passed` |
| Browser HTTP / Python SDK / unified WebSocket real entry suite | `3 passed` |
| Frozen migrations/config/Session/WS gate | `45 passed` |
| Python static gate | Ruff passed |
| Web tests | Node `64/64`; ESLint 0 errors (56 pre-existing warnings outside ExamMem) |
| Web production build | Turbopack compiled and type-checked; 63 routes, including six ExamMem routes |
| Git/security | diff check, Core dependency scan, changed-file secret scan and source integrity passed |

The post-migration product-information checkpoint consolidates the five
ExamMem pages behind one learner-facing `Smart Exam Prep` plugin navigation
entry. Existing routes, APIs, persistence, and audit semantics remain stable;
the other pages are internal workspaces rather than competing top-level
products.

The recurring pytest warnings concern cleanup of pre-existing knowledge-base
temporary directories under `/tmp/pytest-of-lh/garbage-*`; no ExamMem assertion,
schema cleanup or test result failed.

## Checkpoint commits

1. `257c82ab` — freeze migration baselines and checkpoints
2. `766e0c48` — add neutral full-stack contribution API
3. `b51b3b23` — migrate domain lifecycle and storage
4. `f2e466f0` — assemble recoverable practice plugin
5. `572c763d` — expose real practice entry surfaces
6. `0cf6095a` — productize review recovery and configuration
7. Final documentation/baseline commit — recorded by the final handoff

No commit was pushed, released or deployed.
