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

The delivered product loop is:

```text
Browser HTTP / Python SDK / unified WebSocket
  -> DeepTutor Turn Host
  -> Capability Registry: exam_practice
  -> ExamMem PracticeRuntimeProvider
  -> Question -> Grade -> Taxonomy mapping -> Diagnosis
  -> selected Memory Backend
  -> Recommendation -> checkpoint + append-only Trace
  -> server-side Resume / Correction / Review / derived Issues

PDF/TXT/MD syllabus / public URL / model outline request
  -> title-only hierarchy draft -> user review -> immutable published Taxonomy
  -> one leaf objective -> one Host Mastery Path + durable Chat-session link
  -> first open starts tutoring; later opens restore the same session
  -> controlled published Scope + exact canonical leaf identity
  -> optional transient PDF/TXT/Markdown context
  -> neutral Host Turn -> native Quiz generation
  -> stable assessment ID -> immutable catalog version -> multiple attempts
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
| Neutral Host hooks | Full-stack plugin manifest/discovery/lifecycle, capability/tool/router/settings/migration/navigation contributions, Host turn/source/learning service ports, optional Mastery Path identity, and `session_surface` filtering live under `deeptutor/`. These contracts do not name ExamMem. |
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
- Host Mastery Path remains the learning-progress source of truth. Each
  published leaf gets a deterministic single-objective Host path, and ExamMem
  stores only its versioned objective-to-session link. Smart Exam Prep does not
  copy Host progress into Learning Memory.
- Native Quiz is used only as an explicitly requested question generator.
  ExamMem maps the selected objective to its canonical Taxonomy, pins the full
  question/rubric catalog and source SHA-256 provenance in the Practice
  checkpoint, and grades answers itself. Native Quiz correctness is ignored.
- One assessment keeps a stable ID and blueprint. Generated catalogs are
  immutable versions; repeated attempts may pin an old version or a newly
  generated version while using different Practice session and Trace identities.
- Completing a finite catalog marks the assessment attempt completed while the
  frozen seven-state Practice machine ends at `MEMORY_UPDATED`; no eighth
  Practice state or test-only transition was introduced.

## Database and migration result

- `EXAM_MEM_DATABASE_URL` is required at the engine boundary and accepts only a
  complete `postgresql+asyncpg` URL. It is never persisted by this repository.
- Frozen migrations `0001`–`0006` match the source baseline byte-for-byte.
- The single target head is `0009_assessments`.
- A new empty PostgreSQL database upgrades linearly from base through all nine
  revisions and produces 20 public tables including `alembic_version` and eight
  distinct append-only triggers.
- `0008_study_plans` adds mutable drafts, immutable published plan versions and
  objective-to-Host-session links. `0009_assessments` adds stable assessment
  identities, immutable question-catalog versions and multiple attempts.
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
| Full repository with ExamMem and local isolated PostgreSQL | `4283 passed, 9 skipped` |
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
7. `0b06568b` — freeze the migrated production acceptance baseline
8. Checkpoint 8 productization:
   - `a5e62922` — consolidate the Smart Exam Prep product surface
   - `af67f442` — connect Learning Paths to generated, scoped Practice

Supporting local documentation/demo/localization commits are `38164c68`,
`08a8f3ba`, and `a357df53`; they do not create additional migration
checkpoints.

No commit was pushed, released or deployed.

## Study-plan and assessment product increment

The 2026-08-14 product increment adds migrations `0008` and `0009`, imported
and reviewed study-plan scopes, deterministic one-objective Host learning paths,
durable Chat-session restoration, exact published-Taxonomy Practice selection,
and stable assessment IDs with immutable versions and repeated attempts. The
local demo database was upgraded to `0009_assessments`; final read-only audit
found 20 public tables, eight append-only triggers, zero rows in all seven new
business tables, and zero leftover test schemas.

Executable extraction and frozen migration verification remain pinned to
`747958725b6e681a3a846a0430b5a21deb163188`. Final source audit found a clean
worktree at `c8512ffff5834198b009833e0228543df69b25cb`, a pre-existing
documentation-only descendant of the executable baseline; this work neither
checked out nor modified the read-only source repository. Imported source
content is not a tutoring knowledge base in this increment; source-aware
Chat/RAG remains an explicit deferred item.

## Review grouping and response-language follow-up

The 2026-08-14 Checkpoint 8 follow-up changes the Review read model from a
flat Practice-session list to `assessment -> immutable version -> attempt`.
Learners can filter exams by keyword, subject and status, then open one exam to
compare every attempted version. An attempt score is the arithmetic mean of
its persisted per-question grades; the UI also shows the correct-answer count.
This is derived read data and adds no table, migration or write-side effect.

New generated assessment versions pin `zh` or `en` in each server-side question
rubric. Generation, grading and error-analysis turns use separate explicit
Chinese and English prompts, including an instruction that reasons and
explanations use the selected language. Existing checkpoints without the field
default to Chinese, preserving the previously shipped Chinese workflow without
an indefinite compatibility branch.
