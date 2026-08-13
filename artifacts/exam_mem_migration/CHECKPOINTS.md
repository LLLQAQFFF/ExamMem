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

## Checkpoint 5 — Web and real entries

- Goal: migrate the Practice and Learning Memory browser surfaces and all public
  entry adapters.
- Assumptions: the browser never receives answer keys or controls authenticated
  user scope.
- Success: Browser, authenticated HTTP, WebSocket, and Python SDK traverse the
  same plugin workflow; Web static checks, Node tests, and production build pass.

## Checkpoint 6 — current-loop productization

- Goal: close the current product's Session Surface, recovery, Review, Issues,
  Configuration, and Native/Learning Memory usability gaps.
- Assumptions: existing checkpoint, Trace, L1/L2, provenance, Decision Journal,
  Change Log, and L3 facts are reused before any new schema is proposed.
- Success: Practice is excluded from Chat recents/search/resume at repository and
  API layers; recovery, review, issue derivation, configuration pinning, side
  effect preview, correction, and responsive UI contracts pass.

## Checkpoint 7 — total acceptance

- Goal: freeze the migrated production baseline and operating evidence.
- Assumptions: no push, release, deployment, stage 08 evaluation, or multi-source
  ingestion is authorized.
- Success: five-backend parity, full regression, production build, migration
  head, Git, sensitive-content, source-integrity, report, Runbook, and deferred
  boundary gates all pass.
