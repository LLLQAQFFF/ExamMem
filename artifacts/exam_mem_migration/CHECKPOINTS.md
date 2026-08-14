# ExamMem migration checkpoints

This file records the authorized migration from the read-only ExamMem source
baseline into this clean DeepTutor repository. Checkpoints 1 through 7 are the
only implementation scope. The future multi-source learning work described by
the source design remains deferred.

## Checkpoint 1 — dual-baseline audit

- Goal: freeze both repositories, classify the source changes, and prove the
  unmodified host baseline.
- Assumptions: source code comes only from commit
  `747958725b6e681a3a846a0430b5a21deb163188`; the post-baseline governance
  documents are requirements, not executable source.
- Success: source remains clean; target identity and environment are recorded;
  native Python regression and Web production build pass without an ExamMem
  database connection.

## Checkpoint 2 — neutral full-stack Plugin API

- Goal: provide only the host contribution points required by ExamMem.
- Assumptions: plugins are compile-time Python/Web contributions; no marketplace,
  remote installer, or runtime micro-frontend is required.
- Success: capability, tool, router, settings, health, navigation, and lifecycle
  contributions load through a domain-neutral API; host-without-plugin has no
  plugin router, navigation, migration, or database side effects.

### Result

- Added the compile-time `deeptutor_plugins` namespace and standard
  `deeptutor.plugins` entry-point discovery. Discovery returns lazy factories;
  names listed in `plugins.json.disabled` are never imported.
- Added immutable contribution contracts plus a process-scoped manager. It
  materializes each enabled plugin once, rejects duplicate contributions and
  Host Registry conflicts, rolls back partial startup, shuts down in reverse
  order, and reports health without hiding an unhealthy plugin.
- Capability and Tool registries, FastAPI router mounting/access dependencies,
  API metadata, navigation, settings discovery, and application lifecycle now
  consume only the neutral contract. No Host file names or imports ExamMem.
- Contract/config tests: `29 passed`; full native regression:
  `3821 passed, 9 skipped, 5 warnings`; Ruff passed; Web production build and
  TypeScript passed with 57/57 routes.
- Database side effects: none. ExamMem database variables were removed for the
  full regression; this checkpoint added no database dependency or migration.
- Deferred boundary: no ExamMem package, workflow, UI route, session surface,
  or multi-source capability was added in this checkpoint.

## Checkpoint 3 — domain and storage

- Goal: migrate ExamMem-owned contracts, taxonomy, normalization, five backends,
  lifecycle, repositories, projections, and migrations `0001` through `0006`.
- Assumptions: ExamMem PostgreSQL is an independent business store; DeepTutor
  Native Memory and internal databases are not its source of truth.
- Success: domain/unit tests, migration hashes, repository/PostgreSQL integration,
  append-only, CAS, provenance, audit, compensation, and rebuild invariants pass.

### Result

- Migrated the frozen contracts, Taxonomy and normalization resources,
  `slot_key`, four-dimensional Scope, projection, lifecycle policy/applier,
  Decision Journal, Change Log, compensation, repositories, schema metadata,
  and all five Backend contracts into the ExamMem-owned package.
- Removed Fork coupling while preserving behavior: LLM, JSON and embedding
  operations enter through `deeptutor.plugins.host_services`; the Native
  comparison arm now consumes an ExamMem-owned event DTO and an injected Host
  port instead of reading or writing DeepTutor Native Memory directly.
- Migrations `0001` through `0006` match frozen commit
  `747958725b6e681a3a846a0430b5a21deb163188` byte-for-byte. A committed SHA-256
  test freezes this property; Alembic reports the single head
  `0006_practice_workflow`.
- Real PostgreSQL verification used a disposable pgvector 0.8.2 / PostgreSQL 16
  container bound only to localhost with its data directory on tmpfs. Upgrade
  produced 12 tables (including `alembic_version`) and five append-only
  triggers. The complete checkpoint suite passed: `315 passed`; after the run,
  no random test schema or business row remained.
- Host-without-plugin regression passed with the ExamMem DSN removed:
  `4108 passed, 40 skipped, 6 warnings`. Ruff, migration hashes, dependency
  direction, source integrity, and diff checks passed.
- Database side effects are confined to the disposable `exammem_test` database;
  stopping `exammem-postgres-codex` deletes the tmpfs database. No DeepTutor
  internal database or Native Memory file was touched.
- Deferred boundary: Practice-owned checkpoint/Trace repositories and the
  database-backed five-arm workflow matrix move with the Practice assembly in
  checkpoint 4. No Practice entry point, UI, or future multi-source feature is
  enabled here.

## Checkpoint 4 — Practice production chain

- Goal: assemble the recoverable Practice workflow through the Plugin API.
- Assumptions: external LLM/embedding results may be fixed in wiring tests, but
  runtime, registry, provider, repository, and PostgreSQL must remain real.
- Success: Practice → Grade → Memory → Recommendation → Recovery/Correction,
  checkpoint, Trace, idempotency, Capability, and seven Tool contracts pass.

### Result

- Added the ExamMem-owned seven-state Practice workflow, strict contracts,
  server-side question catalog, grading/mapping/diagnosis adapters, candidate
  builder, deterministic recommendation, recovery/replay, explicit correction,
  plan transition, checkpoint CAS, and append-only Trace repositories.
- Added the compile-time `deeptutor_plugins.exam_mem` assembly. Its manifest
  contributes exactly `exam_practice`, seven single-purpose Tools, the
  namespaced settings contract, and the frozen Alembic head through the neutral
  Plugin API. No Core Registry or built-in list names ExamMem.
- Removed Fork coupling: Practice obtains identity, LLM, embedding, stream,
  Tool/Capability protocols, and Native Memory only through neutral Host
  services. Native mode converts an ExamMem DTO in the plugin adapter; the
  ExamMem package never reads or writes Host Native Memory formats directly.
- Removed Fork feature-flag duplication. ExamMem owns only
  `capabilities.exam_practice`; it cannot disable Chat, Knowledge Base,
  Research, Book, Co-writer, Visualize, or Partners. The production default is
  the independent PostgreSQL `lifecycle` backend; all five frozen modes remain
  explicit and never fall back.
- A real plugin-discovery → Capability → Provider → Workflow → PostgreSQL test
  runs question issue, wrong-answer grading, taxonomy mapping, diagnosis, L1,
  L2, provenance, lifecycle audit, L3 rebuild, recommendation, and replay. It
  verifies one L1 event, two L2 memories/provenance links/decisions, four
  `PLANNED`/`APPLIED` Change Log rows, one L3 snapshot, two checkpoints, and
  append-only Trace without duplicate replay writes.
- Checkpoint suite with the disposable PostgreSQL passed: `420 passed`.
  Host regression with the DSN removed passed: `4200 passed, 51 skipped,
  10 warnings`. Ruff, migration hashes, dependency direction, diff, source
  integrity, and post-test database cleanup gates passed.
- Database side effects remained inside the tmpfs `exammem_test` database;
  random schemas were dropped and public business tables retained zero rows.
- Deferred boundary: no HTTP, WebSocket, SDK/CLI, browser route, session-surface
  policy, Review, Issues, Configuration UI, or future multi-source work is
  included in this checkpoint.

## Checkpoint 5 — Web and real entries

- Goal: migrate the Practice and Learning Memory browser surfaces and all public
  entry adapters.
- Assumptions: the browser never receives answer keys or controls authenticated
  user scope.
- Success: Browser, authenticated HTTP, WebSocket, and Python SDK traverse the
  same plugin workflow; Web static checks, Node tests, and production build pass.

### Result

- Added an authenticated router contribution owned by the ExamMem plugin at
  `/api/v1/exam-mem`. The plugin injects one `PracticeRuntimeProvider` into
  both its Capability and its Learning Memory read/correction endpoints, so
  HTTP does not construct a parallel backend or bypass the workflow.
- Added the neutral `PluginTurnHost` seam. Practice start/answer HTTP requests
  are protocol adapters over the public Host turn facade and therefore share
  Capability Registry, TurnRuntime, session persistence, streaming events,
  checkpoint, Trace, and idempotency behavior with Python SDK and WebSocket.
- Added real PostgreSQL entry tests for Browser HTTP, Python SDK, and unified
  WebSocket. Each entry runs start, wrong-answer grading, taxonomy mapping,
  diagnosis, L1/L2/audit/L3 recommendation, and identical replay through the
  plugin. Replay leaves all business and checkpoint rows unchanged while
  appending the two expected replay-observation Trace spans.
- The HTTP test additionally traverses scoped Learning Memory list, version
  detail, and L1 provenance evidence. Authenticated `user_id` is supplied by
  the Host; browser question payloads contain neither reference answers nor
  grading rubrics.
- Added compile-time Practice and Learning Memory browser routes. Practice
  persists the complete pending answer request in `sessionStorage`, reuses its
  immutable idempotency material after response loss, and surfaces partial
  server checkpoints. Learning Memory visibly distinguishes its independent
  PostgreSQL truth from Host Native Memory and exposes Scope, lifecycle,
  version-chain, and evidence views.
- Added generic Web consumption of plugin navigation contributions from the
  existing Plugins API. Disabled plugins therefore disappear without any
  `if exam_mem` branch in Sidebar or Core; unknown icons use a bounded neutral
  fallback and external navigation URLs are rejected.
- Hardened Trace against NTP/VM wall-clock rollback by clamping a span's wall
  completion timestamp to its start while retaining monotonic duration. The
  strict `completed_at >= started_at` invariant remains unchanged and has a
  deterministic regression test.
- Checkpoint backend suite passed: `433 passed`. Web Node tests passed:
  `63 passed`; Web lint passed with zero errors and only the repository's 56
  existing warnings. Next production build compiled, type-checked, and
  generated all 59 routes, including `/exam-mem/practice` and
  `/exam-mem/memories`.
- Random PostgreSQL schemas were dropped and public business tables retained
  zero rows. Ruff, diff, migration hashes, sensitive-content scan, dependency
  direction, and frozen-source integrity gates passed.
- Deferred boundary: session-history isolation, interactive Correction/Plan
  controls, Review, Issues, and Saved/Effective/Pinned Configuration belong to
  checkpoint 6. No multi-source ingestion or Stage 08 feature was added.

## Checkpoint 6 — current-loop productization

- Goal: close the current product's Session Surface, recovery, Review, Issues,
  Configuration, and Native/Learning Memory usability gaps.
- Assumptions: existing checkpoint, Trace, L1/L2, provenance, Decision Journal,
  Change Log, and L3 facts are reused before any new schema is proposed.
- Success: Practice is excluded from Chat recents/search/resume at repository and
  API layers; recovery, review, issue derivation, configuration pinning, side
  effect preview, correction, and responsive UI contracts pass.

### Result

- Added the neutral `session_surface` capability contribution and repository
  filter. Built-in capabilities default to `chat`; `exam_practice` owns the
  separate `exam_practice` surface. SQLite treats legacy rows without the field
  as Chat, PocketBase filters before pagination, and TurnRuntime rejects reuse
  across surfaces. Chat Recents, Search/detail, SDK list/detail, and dashboard
  APIs request only the Chat surface without importing or naming ExamMem.
- Added exact Grade Artifact identity over question version, normalized-answer
  hash, rubric version, grader contract version, and effective configuration
  revision. A matching artifact may reuse only the Grade computation across
  practice sessions in the same authenticated three-dimensional Scope; every
  submission still runs mapping, diagnosis, L1/L2/lifecycle writes and produces
  its own checkpoint and Trace. Historic checkpoints without an identity remain
  readable but are never cache sources.
- Pinned each new practice session to an immutable runtime snapshot containing
  configuration revision, one of the five backend modes, and its deterministic
  side-effect set. Recovery resolves the pinned snapshot before constructing a
  backend, so changing Saved configuration cannot silently alter an in-progress
  exam. The administrator-only Configuration API and page distinguish Saved,
  process-effective, and per-exam Pinned values and state when restart is needed.
- Reused checkpoint, Trace, L1/L2, provenance, Decision Journal, Change Log and
  L3 to provide practice history/detail, server-side resume, Review, and derived
  Issues. Resume starts a new Host transport session while replaying the latest
  authenticated business checkpoint, original idempotency context, Trace, and
  pinned backend; browser `sessionStorage` remains only a response-loss
  optimization.
- Added the separately documented append-only Grade Review event stream in
  migration `0007_grade_reviews`. Existing audit facts cannot truthfully derive
  a learner's new dispute or an administrator's disposition, and storing either
  as a Learning Memory correction would corrupt the domain boundary. The table
  is Scope-bound, idempotent, append-only, requires an existing graded
  checkpoint at the API boundary, and has no cross-database foreign key. Frozen
  migrations `0001`–`0006` remain byte-identical.
- Completed the current-loop UI with five plugin-contributed pages: Practice,
  Learning Memory, Review, Issues and Configuration. Learning Memory correction
  and Plan cancellation require explicit confirmation and append evidence rather
  than editing history. Grade dispute is visibly separate; administrator Uphold
  is available in the UI, while Overturn remains an HTTP operation requiring a
  complete structured replacement Grade.
- The complete checkpoint backend suite passed: `484 passed`; the strengthened
  real Browser HTTP, Python SDK, and unified WebSocket entry suite passed again:
  `3 passed`. Ruff passed. Web Node tests passed `63/63`; ESLint had zero errors
  (only 56 pre-existing warnings outside ExamMem); Next production build
  compiled, type-checked, and generated 62 routes including all five ExamMem
  routes.
- PostgreSQL head is `0007_grade_reviews`. The disposable public schema has six
  distinct append-only triggers, no random test schema, and no business rows
  after tests. Sensitive-content, Core dependency-direction, diff, and frozen
  source-integrity checks passed.
- Deferred boundary: no file, video, image, note or PPT ingestion; no Learning
  Journey Memory, course Q&A, source-driven question generation, or other Stage
  08/multi-source implementation was added.

## Checkpoint 7 — total acceptance

- Goal: freeze the migrated production baseline and operating evidence.
- Assumptions: no push, release, deployment, stage 08 evaluation, or multi-source
  ingestion is authorized.
- Success: five-backend parity, full regression, production build, migration
  head, Git, sensitive-content, source-integrity, report, Runbook, and deferred
  boundary gates all pass.

### Result

- Verified a clean Host in an isolated runtime home with
  `EXAM_MEM_DATABASE_URL` absent and `exam_mem` disabled before import. The
  manager reported `loaded_plugins=[]`; DeepTutor's native suite passed
  `3829 passed, 9 skipped` without an ExamMem database connection.
- Verified the entire repository with the first-party plugin enabled against
  the disposable independent PostgreSQL database: `4258 passed, 9 skipped`.
  The explicit five-Backend selection/isolation/idempotency matrix passed
  `33 passed`, including fail-closed behavior and no cross-mode fallback.
- Created a separate empty `exammem_acceptance` database, upgraded linearly
  from base through `0007_grade_reviews`, and confirmed 13 public tables
  including `alembic_version` plus six distinct append-only triggers. The
  acceptance database was then deleted. Final `exammem_test` audit found head
  `0007_grade_reviews`, zero random schemas, and zero public business rows.
- Re-ran frozen migration hashes and chain contracts, Ruff, diff checks, Core
  dependency-direction scan, changed-content sensitive scan, and frozen-source
  integrity. All passed; migrations `0001`–`0006` remain byte-identical and no
  DeepTutor Core module imports `exam_mem`.
- Web ESLint passed with zero errors, Node tests passed `63/63`, and production
  build compiled/type-checked 62 routes including Practice, Review, Learning
  Memory, Issues and Configuration. The build's generated `next-env.d.ts`
  change was intentionally not committed.
- Added `MIGRATION_REPORT.md`, this Runbook, and `DEFERRED_ITEMS.md` to the target
  repository. They record the real call chain, commits, test categories,
  database side effects, product boundaries, operational checks and remaining
  limitations without claiming live-model quality or future Stage 08 work.
- The source repository remained completely unmodified. No push, release,
  deployment, destructive production operation, credential migration, or
  dependency installation occurred.

## Checkpoint 8 — Smart Exam Prep product information architecture

- Goal: present ExamMem as one learner-facing smart exam-preparation product
  instead of five competing sidebar products.
- Assumptions: existing routes are public local contracts and must remain
  stable; no native Quiz result may become Learning Memory evidence without a
  provenance- and Taxonomy-preserving neutral Host contract.
- Success: the plugin contributes one `Smart Exam Prep` navigation item;
  Practice, Learning Profile, Review, Issues, and Configuration remain usable
  through plugin-owned internal navigation; localization, plugin contracts,
  lint, Node tests, and the production build pass without database writes.

### Result

- Replaced five top-level plugin contributions with one neutral navigation
  contribution and added a plugin-owned internal shell across all five routes.
- Preserved `/exam-mem/*` URLs, `exam_practice`, the independent PostgreSQL
  boundary, migration head, checkpoints, idempotency, and audit semantics.
- Plugin contract tests passed `3/3`; Web Node tests passed `64/64`; locale
  parity passed; ESLint reported zero errors and the same 56 pre-existing
  warnings; an isolated production build compiled, type-checked, and generated
  all 62 routes.
- No database was read or written. The tracked `.next-deeptutor` runtime cache
  already present in the worktree was neither reset nor included in this
  checkpoint.
