# ExamMem dual-baseline audit

Audit date: 2026-08-13 (Asia/Shanghai)

## Repository identities

| Role | Repository | Frozen identity | Working tree |
| --- | --- | --- | --- |
| Read-only executable source | `/home/lh/code/ExamMem` | `747958725b6e681a3a846a0430b5a21deb163188` | clean; current branch tip is a documentation-only successor |
| Development target | `/home/lh/DeepTutor` | `9228d10abc114ec87321c6861e7e384db022e8ce` | clean before migration; branch `feat/exam-mem-plugin-migration` |

The source branch tip is not used as executable input. Its only delta from the
frozen source code is the explicitly requested post-release baseline update and
the two migration-governance documents.

## Environment baseline

- Python runtime used for migration verification: Conda `exammem`, Python 3.11.15.
- Existing storage dependencies: SQLAlchemy 2.0.51, Alembic 1.19.1,
  asyncpg 0.31.0, pgvector importable.
- Node.js 22.23.2 and npm 10.9.8; target `web/node_modules` already exists.
- No dependency was installed or upgraded.
- `pip check` reports the pre-existing deferred `wheel 0.47.0` /
  `packaging 21.3` mismatch recorded by the source as `DL-04`.

## Source classification

### ExamMem-owned domain code

- `exam_mem/contracts`, `domain`, taxonomy and normalization policies;
- `storage`, repository implementations, projections, Alembic environment, and
  migrations `0001` through `0006`;
- deterministic lifecycle policy, applier, audit, contested convergence,
  compensation, and projection refresh;
- five Memory Backend implementations;
- Practice contracts, adapters, workflow, checkpoint, Trace, recommendation,
  query/correction/plan services, and plugin-owned HTTP/UI adapters;
- the corresponding ExamMem and protocol-check tests required to preserve the
  frozen invariants.

### Neutral DeepTutor Host Hooks

- discover and register full-stack plugin contributions;
- Capability and Tool registration without built-in domain imports;
- authenticated API-router contribution and lifecycle/health reporting;
- namespaced non-sensitive settings and explicit load failure reporting;
- a generic Session Surface stored and filtered by repository/API;
- compile-time Web navigation contribution and plugin route ownership;
- stable host services consumed by the plugin: authenticated identity, turn
  runtime, stream, LLM, embedding, and Native Memory adapter port.

### Fork coupling that must not migrate

- adding `exam_practice` to `BUILTIN_CAPABILITY_CLASSES`;
- importing `exam_mem` from DeepTutor API main, Registry, Settings, TurnRuntime,
  or built-in Tool modules;
- global ExamMem feature flags that disable unrelated DeepTutor capabilities,
  tools, background services, or Native Memory initialization;
- static ExamMem router/sidebar imports in host code;
- test-only metadata fallback as a production configuration channel;
- any compatibility branch, silent Backend fallback, or duplicated production
  workflow retained only because the source was a Fork.

## Dependency and data boundaries

The target host currently has no complete full-stack plugin loader. It contains
an optional capability-loader call site, which is a useful seam but not a usable
contract. The migration therefore adds a small neutral Plugin API rather than
porting the Fork wiring.

ExamMem PostgreSQL remains the only ExamMem business truth. The `native` backend
is an explicit adapter/baseline and does not authorize direct use of DeepTutor
SQLite, PocketBase, or Native Memory as Learning Memory storage. There are no
cross-database foreign keys.

## Baseline verification

- Web production build using `DEEPTUTOR_NEXT_DIST_DIR=.next-deeptutor`: passed;
  TypeScript and 57/57 static routes completed.
- Python collection: 3,822 tests; final result `3813 passed, 9 skipped,
  5 warnings` in 67.16 seconds.
- Sandboxed Python processes that used `asyncio.to_thread()` completed their
  assertions but could not shut down the worker pool. The same minimal probe
  exited normally outside the sandbox. No test or production workaround was
  retained; the native Python baseline is therefore executed outside the
  sandbox with database variables removed.
- The first unsandboxed full regression found one stale assertion from target
  commit `9228d10a`: the document-parsing migration expected the pre-LiteParse
  engine set although production configuration and packaging include
  `liteparse`. The assertion was aligned with the released six-engine contract;
  no runtime behavior changed.
- ExamMem database variables were explicitly removed for native tests. No
  PostgreSQL business writes occurred during this audit.

## Deferred boundary

This migration does not implement file/video/image/note/PPT/audio/web ingestion,
ASR/OCR, source-driven question generation, Learning Journey Memory, unified
Learner Model rules, formal stage 08 datasets/metrics, or stage 09 optimization.
